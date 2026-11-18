"""치료사 회기 계획 API(계획 맥락 조회, AI 제안, 계획 만들기·수정·승인·취소·복제). 계약은 documents/02_architecture/therapist-workflow.md 6절이다.
모든 경로는 담당 치료사만 쓴다. 계획은 항상 아동을 거쳐 조회해 다른 아동·치료사의 계획을 읽지 못한다.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import owned_child, require_therapist
from ..db import get_db
from ..models import (AuditEvent, Child, ClinicalObservation, Therapist, TherapistSessionPlan, TherapistSessionPlanStep,
                      TrainingGoal, TrainingSession, now)
from .graph import goal_view, run_planning
from .schemas import SessionPlanInput

router = APIRouter(prefix="/api", tags=["therapist-planning"])

PLAN_FIELDS = ("target_phoneme", "word_position", "start_level", "target_level", "duration_min", "repetition_target",
               "preferred_cue", "priority_targets", "excluded_words", "conversation_theme", "therapist_note")


def _camel(name: str) -> str:
    """snake_case 이름을 camelCase로 바꾼다(예: start_level → startLevel).
    DB 필드 이름을 화면 JSON 키로 내보낼 때 쓴다.
    """
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def _current_goal(db, child_id: str) -> TrainingGoal:
    """아동의 현재 활성 치료 목표를 가져온다. 없으면 409다.
    계획은 항상 현재 목표를 기준으로 만들기 때문에 거의 모든 계획 API가 먼저 부른다.
    """
    goal = db.scalar(select(TrainingGoal).where(TrainingGoal.child_id == child_id, TrainingGoal.status == "active"))
    if goal is None:
        raise HTTPException(409, "현재 치료 목표가 없습니다")
    return goal


def _steps(db, plan_id: str) -> list[TherapistSessionPlanStep]:
    """계획의 활동 단계들을 순서(step_order)대로 가져온다.
    계획 응답 만들기, 양식 저장, 복제에서 쓴다.
    """
    return list(db.scalars(select(TherapistSessionPlanStep).where(TherapistSessionPlanStep.plan_id == plan_id)
                           .order_by(TherapistSessionPlanStep.step_order)).all())


def plan_data(db, plan: TherapistSessionPlan) -> dict:
    """계획 한 개를 화면용 dict(목표 버전, 상태, 양식 필드, 활동 단계, 근거·제안 스냅숏, 시각들)로 만든다.
    계획 조회·생성·수정·승인·취소·복제 API의 공통 응답 형식이다.
    """
    goal = db.get(TrainingGoal, plan.goal_id)
    return {"id": plan.id, "childId": plan.child_id, "goalId": plan.goal_id, "goalVersion": goal.version if goal else None,
            "parentPlanId": plan.parent_plan_id, "revision": plan.revision, "status": plan.status,
            **{_camel(field): getattr(plan, field) for field in PLAN_FIELDS},
            "steps": [{"id": step.id, "stepOrder": step.step_order, "stepType": step.step_type, "activity": step.activity,
                       "targetLevel": step.target_level, "parameters": step.parameters} for step in _steps(db, plan.id)],
            "evidenceSnapshot": plan.evidence_snapshot, "recommendationSnapshot": plan.recommendation_snapshot,
            "createdAt": plan.created_at, "updatedAt": plan.updated_at, "approvedAt": plan.approved_at,
            "cancelledAt": plan.cancelled_at}


def _owned_plan(db, plan_id: str, therapist: Therapist) -> tuple[TherapistSessionPlan, Child]:
    """계획을 찾고 그 아동이 이 치료사의 담당인지 확인해 계획과 아동을 돌려준다. 계획이 없으면 404다.
    다른 치료사의 계획은 존재 여부를 숨기고 거부 기록을 남긴다(owned_child가 처리).
    """
    plan = db.get(TherapistSessionPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "계획을 찾을 수 없습니다")
    # 아동 소유권으로 확인한다. 다른 치료사의 계획은 존재 여부를 숨기고 거부 기록을 남긴다.
    child = owned_child(db, plan.child_id, therapist)
    return plan, child


def _audit(db, therapist: Therapist, action: str, plan_id: str):
    """계획 관련 행동(생성·수정·승인 등)의 감사 기록을 추가한다.
    누가 언제 계획을 바꿨는지 나중에 확인할 수 있게 한다.
    """
    db.add(AuditEvent(actor_id=therapist.id, action=action, resource_id=plan_id, result="SUCCESS"))


def _write_form(db, plan: TherapistSessionPlan, body: SessionPlanInput):
    """요청 양식 값을 계획 필드에 쓰고, 기존 활동 단계를 지운 뒤 새 순서로 다시 만든다.
    계획 생성과 초안 수정이 같은 방식으로 저장하도록 모아 둔 도우미다.
    """
    values = body.model_dump()
    for field in PLAN_FIELDS:
        setattr(plan, field, values[field])
    for step in _steps(db, plan.id):
        db.delete(step)
    db.flush()
    for order, step in enumerate(body.steps, 1):
        db.add(TherapistSessionPlanStep(plan_id=plan.id, step_order=order, step_type=step.step_type,
                                        activity=step.activity, target_level=step.target_level,
                                        parameters=step.parameters.model_dump(by_alias=True, exclude_none=True)))
    plan.updated_at = now()


def _require_draft(plan: TherapistSessionPlan):
    """계획이 DRAFT(초안)인지 확인하고 아니면 409를 낸다.
    승인·종료된 계획은 바꾸지 않고 복제해서 새 초안을 만들게 하려고 있다.
    """
    if plan.status != "DRAFT":
        raise HTTPException(409, "승인되었거나 종료된 계획은 바꿀 수 없습니다. 복제해서 새 초안을 만드세요")


def _snapshots(db, child: Child, goal: TrainingGoal) -> tuple[dict, dict]:
    """LLM 없이 결정적 계산만 다시 해서 그 시점의 근거 스냅숏과 제안 스냅숏을 만든다.
    transcript·원음성·아동 이름은 담지 않는다. 계획 생성·승인·복제 때 당시 근거를 고정해 두려고 쓴다.
    """
    result = run_planning(db, child.id, goal, use_llm=False)
    evidence = {"goal": goal_view(goal), "metrics": result["metrics"], "evidence": result["verified"][:20],
                "capturedAt": now().isoformat()}
    return evidence, result["proposal"]


@router.get("/children/{child_id}/planning-context")
def planning_context(child_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """계획 화면에 필요한 맥락(아동 기본 정보, 현재 목표, 지표, 근거 상황, 최근 실제 회기, 최신 계획)을 돌려준다. LLM은 부르지 않는다.
    치료사 화면의 회기 계획 탭을 열 때 호출한다. 검토 대기 수는 최근 5회기 밖까지 실제 회기 전체 기준이다.
    """
    child = owned_child(db, child_id, therapist)
    goal = _current_goal(db, child.id)
    result = run_planning(db, child.id, goal, use_llm=False)
    # 검토 대기 수는 실제 음성 회기 전체 기준이다(최근 5회기 창 밖의 미검토 관찰도 치료사가 볼 수 있게).
    pending_all = db.scalar(select(func.count()).select_from(ClinicalObservation).join(
        TrainingSession, TrainingSession.id == ClinicalObservation.session_id).where(
        ClinicalObservation.child_id == child.id, ClinicalObservation.verification_state == "PENDING",
        TrainingSession.mode == "real", TrainingSession.is_seed.is_(False)))
    demo_excluded = db.scalar(select(func.count()).select_from(ClinicalObservation).join(
        TrainingSession, TrainingSession.id == ClinicalObservation.session_id).where(
        ClinicalObservation.child_id == child.id, (TrainingSession.mode != "real") | TrainingSession.is_seed.is_(True)))
    latest = db.scalar(select(TherapistSessionPlan).where(TherapistSessionPlan.child_id == child.id,
                                                          TherapistSessionPlan.status != "CANCELLED")
                       .order_by(TherapistSessionPlan.created_at.desc()))
    recent = result["metrics"]["trend"][::-1]
    return {"child": {"childCode": child.child_code, "alias": child.hero_name, "ageBand": child.age_band},
            "currentGoal": goal_view(goal), "metrics": result["metrics"],
            "evidenceAvailability": {"verifiedN": result["metrics"]["verifiedN"], "pendingReviewN": pending_all,
                                     "demoExcludedN": demo_excluded, "rejectedN": result["metrics"]["rejectedN"],
                                     "sufficient": result["metrics"]["sufficient"]},
            "recentRealSessions": recent,
            "latestPlan": {"id": latest.id, "status": latest.status, "revision": latest.revision} if latest else None}


@router.post("/children/{child_id}/session-plan-proposal")
def session_plan_proposal(child_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """계획 그래프를 실행해 다음 회기 제안, 요약(LLM 또는 템플릿)과 그 출처, 지표, 근거를 돌려준다. 저장하지는 않는다.
    치료사 화면의 "제안 받기" 버튼이 호출한다.
    """
    child = owned_child(db, child_id, therapist)
    goal = _current_goal(db, child.id)
    result = run_planning(db, child.id, goal)
    return {"status": result["proposal"]["status"], "proposal": result["proposal"],
            "summary": {**result["summary"], "source": result["summary_source"]},
            "metrics": result["metrics"], "evidence": result["verified"][:20], "goal": goal_view(goal)}


@router.get("/children/{child_id}/session-plans")
def list_session_plans(child_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """담당 아동의 모든 회기 계획을 최신순으로 돌려준다.
    치료사 화면의 계획 목록이 호출한다.
    """
    child = owned_child(db, child_id, therapist)
    plans = db.scalars(select(TherapistSessionPlan).where(TherapistSessionPlan.child_id == child.id)
                       .order_by(TherapistSessionPlan.created_at.desc())).all()
    return [plan_data(db, plan) for plan in plans]


@router.post("/children/{child_id}/session-plans")
def create_session_plan(child_id: str, body: SessionPlanInput, db: Session = Depends(get_db),
                        therapist: Therapist = Depends(require_therapist)):
    """현재 목표와 당시 근거·제안 스냅숏을 붙여 새 계획 초안(revision 1)을 저장한다.
    치료사가 제안을 검토·수정한 뒤 "초안 저장"을 누르면 호출된다.
    """
    child = owned_child(db, child_id, therapist)
    goal = _current_goal(db, child.id)
    evidence, recommendation = _snapshots(db, child, goal)
    plan = TherapistSessionPlan(child_id=child.id, therapist_id=therapist.id, goal_id=goal.id, revision=1,
                                evidence_snapshot=evidence, recommendation_snapshot=recommendation,
                                **{field: getattr(body, field) for field in PLAN_FIELDS})
    db.add(plan)
    db.flush()
    _write_form(db, plan, body)
    _audit(db, therapist, "SESSION_PLAN_CREATED", plan.id)
    db.commit()
    return plan_data(db, plan)


@router.get("/session-plans/{plan_id}")
def get_session_plan(plan_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """계획 하나를 돌려준다(담당 치료사만).
    치료사 화면이 계획 상세를 열 때 호출한다.
    """
    plan, _child = _owned_plan(db, plan_id, therapist)
    return plan_data(db, plan)


@router.patch("/session-plans/{plan_id}")
def update_session_plan(plan_id: str, body: SessionPlanInput, db: Session = Depends(get_db),
                        therapist: Therapist = Depends(require_therapist)):
    """초안(DRAFT) 계획의 양식과 활동 단계를 고쳐 저장한다. 승인·종료된 계획이면 409다.
    치료사 화면에서 초안을 수정할 때 호출한다.
    """
    plan, _child = _owned_plan(db, plan_id, therapist)
    _require_draft(plan)
    _write_form(db, plan, body)
    _audit(db, therapist, "SESSION_PLAN_UPDATED", plan.id)
    db.commit()
    return plan_data(db, plan)


@router.post("/session-plans/{plan_id}/approve")
def approve_session_plan(plan_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """초안을 승인하고 승인 시점의 근거·제안을 다시 고정한다. 기존 승인 계획은 SUPERSEDED로 바꾼다.
    초안 작성 뒤 목표가 바뀌었으면 409로 거절한다. 치료사의 "승인" 버튼이 호출한다.
    """
    plan, child = _owned_plan(db, plan_id, therapist)
    _require_draft(plan)
    goal = _current_goal(db, child.id)
    if goal.id != plan.goal_id:
        raise HTTPException(409, "초안 작성 뒤 치료 목표가 바뀌었습니다. 현재 목표로 계획을 다시 작성하세요")
    # 승인 시점의 목표·근거·제안을 고정한다. 이후 목표나 관찰이 바뀌어도 이 계획은 그대로 재현된다.
    plan.evidence_snapshot, plan.recommendation_snapshot = _snapshots(db, child, goal)
    for old in db.scalars(select(TherapistSessionPlan).where(TherapistSessionPlan.child_id == child.id,
                                                             TherapistSessionPlan.status == "APPROVED")).all():
        old.status = "SUPERSEDED"
        old.updated_at = now()
        _audit(db, therapist, "SESSION_PLAN_SUPERSEDED", old.id)
    plan.status = "APPROVED"
    plan.approved_at = plan.updated_at = now()
    _audit(db, therapist, "SESSION_PLAN_APPROVED", plan.id)
    db.commit()
    return plan_data(db, plan)


@router.post("/session-plans/{plan_id}/cancel")
def cancel_session_plan(plan_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """초안 또는 승인된 계획을 취소(CANCELLED) 처리한다. 이미 종료된 계획이면 409다.
    치료사 화면의 "취소" 버튼이 호출한다.
    """
    plan, _child = _owned_plan(db, plan_id, therapist)
    if plan.status not in {"DRAFT", "APPROVED"}:
        raise HTTPException(409, "이미 종료된 계획입니다")
    plan.status = "CANCELLED"
    plan.cancelled_at = plan.updated_at = now()
    _audit(db, therapist, "SESSION_PLAN_CANCELLED", plan.id)
    db.commit()
    return plan_data(db, plan)


@router.post("/session-plans/{plan_id}/clone")
def clone_session_plan(plan_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """승인·종료된 계획을 복제해 revision을 하나 올린 새 초안을 만든다. 초안이거나 이미 수정 초안이 있으면 409다.
    승인된 계획은 바꾸지 않는다는 원칙 때문에, 고치고 싶을 때 이 API로 새 초안을 만든다.
    """
    source, child = _owned_plan(db, plan_id, therapist)
    if source.status == "DRAFT":
        raise HTTPException(409, "초안은 복제하지 않고 바로 수정하세요")
    if db.scalar(select(TherapistSessionPlan.id).where(TherapistSessionPlan.parent_plan_id == source.id,
                                                       TherapistSessionPlan.status == "DRAFT")):
        raise HTTPException(409, "이 계획의 수정 초안이 이미 있습니다")
    goal = _current_goal(db, child.id)
    evidence, recommendation = _snapshots(db, child, goal)
    plan = TherapistSessionPlan(child_id=child.id, therapist_id=therapist.id, goal_id=goal.id,
                                parent_plan_id=source.id, revision=source.revision + 1,
                                evidence_snapshot=evidence, recommendation_snapshot=recommendation,
                                **{field: getattr(source, field) for field in PLAN_FIELDS})
    db.add(plan)
    db.flush()
    for step in _steps(db, source.id):
        db.add(TherapistSessionPlanStep(plan_id=plan.id, step_order=step.step_order, step_type=step.step_type,
                                        activity=step.activity, target_level=step.target_level,
                                        parameters=dict(step.parameters)))
    _audit(db, therapist, "SESSION_PLAN_CLONED", plan.id)
    db.commit()
    return plan_data(db, plan)
