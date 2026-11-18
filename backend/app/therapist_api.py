"""치료사 화면이 쓰는 API 라우트: 아동·목표 관리, 대시보드, 추천·활동 제안, 회기 상세, 관찰 검증, 발화 교정, 규칙.
main.py에는 앱 설정·로그인·아동 놀이 흐름만 남기고, 치료사 기능은 이 파일 하나에서 읽을 수 있게 나눴다."""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic.alias_generators import to_camel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import (AIRecommendation, ActivityRecommendation, Account, AuditEvent, ClinicalObservation, ClinicalVerification, Child, GameEvent, HoyaChatSession, HoyaChatTurn, ProgressMetric, SpeechAnalysis,
                     Therapist, TherapistFeedback, TherapistRule, TrainingDecision, TrainingGoal,
                     TrainingPlan, TrainingSession, Utterance, now)
from .schemas import (ActivityRecommendationDecisionInput, ChildInput, FeedbackInput, GoalInput, ObservationDecisionInput,
                      RecommendationDecisionInput)
from .security import hash_password
from .auth import owned_child, require_therapist
from .analysis.progress import recompute
from .clinical.activity_recommendation import propose_activity
from .games.rounds import GAME_ROUNDS
from .games import crossing
from .adventure import service as adventure_service
from .therapist_planning.evidence import latest_decisions
from .serializers import (activity_rec_data, child_data, clinical_eligible, create_goal, current_goal, goal_data,
                          legacy_recommendation_allowed, observation_data, public, rec_data, rec_payload)

router = APIRouter(prefix="/api")


@router.get("/children")
def children(db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """로그인한 치료사가 담당하는 아동 목록을 돌려준다.
    치료사 화면의 아동 선택 목록에 쓴다."""
    return [child_data(c) for c in db.scalars(select(Child).where(Child.therapist_id == therapist.id)).all()]


@router.post("/children")
def add_child(body: ChildInput, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """새 아동·놀이 코드·학생 계정·첫 목표를 만들고 초기 비밀번호를 한 번만 돌려준다.
    치료사가 아동을 등록할 때 쓰며 비밀번호는 해시만 저장한다."""
    count = len(db.scalars(select(Child)).all()) + 1
    play_code = secrets.token_hex(3).upper()
    while db.scalar(select(Child.id).where(Child.play_code == play_code)):
        play_code = secrets.token_hex(3).upper()
    child = Child(child_code=f"C-{count:04d}", hero_name=body.hero_name, age_band=body.age_band, therapist_id=therapist.id, play_code=play_code, guardian_consent_at=now() if body.guardian_consent else None)
    db.add(child)
    db.flush()
    initial_password = secrets.token_urlsafe(16)
    salt = secrets.token_hex(16)
    db.add(Account(username=play_code, password_salt=salt,
                   password_hash=hash_password(initial_password, salt), role="STUDENT", child_id=child.id))
    create_goal(db, child, {})
    db.commit()
    return {**child_data(child), "initialPassword": initial_password}


@router.get("/children/{child_id}")
def child_detail(child_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """아동 정보·현재 목표·목표 이력·회기 목록(지표 포함)·활성 규칙을 돌려준다.
    치료사의 아동 상세 화면에서 쓰며 담당 아동이 아니면 404다."""
    child = owned_child(db, child_id, therapist)
    goals = db.scalars(select(TrainingGoal).where(TrainingGoal.child_id == child.id).order_by(TrainingGoal.version.desc())).all()
    sessions = db.scalars(select(TrainingSession).where(TrainingSession.child_id == child.id).order_by(TrainingSession.started_at.desc())).all()
    rules = db.scalars(select(TherapistRule).where(TherapistRule.child_id == child.id, TherapistRule.active == True)).all()
    return {"child": child_data(child), "currentGoal": goal_data(goals[0]) if goals else None, "goalHistory": [goal_data(g) for g in goals], "sessions": [{"id": s.id, "mode": s.mode, "isSeed": s.is_seed, "startedAt": s.started_at, "status": s.status, "summary": s.summary_json, "goalVersion": db.get(TrainingGoal, s.goal_id).version, "metric": ({"attempts": m.attempts, "successRate": m.success_rate, "firstTrySuccessRate": m.first_try_success_rate} if (m := db.scalar(select(ProgressMetric).where(ProgressMetric.session_id == s.id))) else None)} for s in sessions], "activeRules": [public(r, ["id", "rule_type", "params", "active"]) for r in rules]}


@router.get("/children/{child_id}/goals")
def goals(child_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """아동의 목표 버전 목록을 최신순으로 돌려준다.
    목표 이력 화면에서 쓴다."""
    owned_child(db, child_id, therapist)
    return [goal_data(g) for g in db.scalars(select(TrainingGoal).where(TrainingGoal.child_id == child_id).order_by(TrainingGoal.version.desc())).all()]


@router.post("/children/{child_id}/goals")
def add_goal(child_id: str, body: GoalInput, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """입력값으로 새 목표 버전을 만들고 돌려준다.
    치료사가 목표를 직접 바꿀 때 쓰며 이전 목표는 superseded가 된다."""
    child = owned_child(db, child_id, therapist)
    goal = create_goal(db, child, body.model_dump())
    db.commit()
    return goal_data(goal)


@router.delete("/children/{child_id}/speech-data", status_code=204)
def delete_speech_data(child_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """아동의 회기·발화·분석·관찰·검증·활동 제안·호야 대화를 참조 순서대로 모두 지운다(204).
    보호자 요청 등으로 음성 파생 자료를 삭제할 때 쓰며 삭제 사실은 감사 기록으로 남긴다."""
    owned_child(db, child_id, therapist)
    ids = db.scalars(select(TrainingSession.id).where(TrainingSession.child_id == child_id)).all()
    utterance_ids = db.scalars(select(Utterance.id).where(Utterance.session_id.in_(ids))).all() if ids else []
    # 발화에서 파생된 임상 관찰·치료사 검증·활동 제안도 함께 지운다. 참조하는 행부터 삭제한다.
    observation_ids = db.scalars(select(ClinicalObservation.id).where(ClinicalObservation.child_id == child_id)).all()
    if observation_ids:
        db.execute(delete(ClinicalVerification).where(ClinicalVerification.observation_id.in_(observation_ids)))
        db.execute(delete(ClinicalObservation).where(ClinicalObservation.id.in_(observation_ids)))
    db.execute(delete(ActivityRecommendation).where(ActivityRecommendation.child_id == child_id))
    # 호야 대화 문장도 아동 음성에서 나온 자료다. 턴을 먼저 지우고 대화 세션을 지운다.
    chat_ids = db.scalars(select(HoyaChatSession.id).where(HoyaChatSession.child_id == child_id)).all()
    if chat_ids:
        db.execute(delete(HoyaChatTurn).where(HoyaChatTurn.session_id.in_(chat_ids)))
        db.execute(delete(HoyaChatSession).where(HoyaChatSession.id.in_(chat_ids)))
    if utterance_ids:
        db.execute(delete(SpeechAnalysis).where(SpeechAnalysis.utterance_id.in_(utterance_ids)))
    if ids:
        for model in (TrainingDecision, GameEvent, ProgressMetric, AIRecommendation):
            db.execute(delete(model).where(model.session_id.in_(ids)))
    if utterance_ids:
        db.execute(delete(Utterance).where(Utterance.id.in_(utterance_ids)))
    if ids:
        adventure_service.remove_session_links(db, ids)
        db.execute(delete(TrainingSession).where(TrainingSession.id.in_(ids)))
    db.add(AuditEvent(actor_id=therapist.id, action="SPEECH_DATA_DELETED", resource_id=child_id, result="SUCCESS"))
    db.commit()
    return Response(status_code=204)


@router.get("/dashboard/overview")
def overview(db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """담당 아동·최근 회기 10개·대기 중인 legacy 추천을 한 번에 돌려준다.
    치료사 대시보드 첫 화면에서 쓴다."""
    children = db.scalars(select(Child).where(Child.therapist_id == therapist.id)).all()
    child_ids = [c.id for c in children]
    sessions = db.scalars(select(TrainingSession).where(TrainingSession.child_id.in_(child_ids)).order_by(TrainingSession.started_at.desc()).limit(10)).all() if child_ids else []
    pending = db.scalars(select(AIRecommendation).where(AIRecommendation.child_id.in_(child_ids), AIRecommendation.status == "pending")).all() if child_ids else []
    # 수정 전에 저장된 DEMO 연습 기반 추천(과거 행)은 대기 수와 목록에서 뺀다. 생성 경계만으로는 과거 행을 막지 못한다.
    pending = [r for r in pending if legacy_recommendation_allowed(db.get(TrainingSession, r.session_id))]
    return {"activeChildren": [{**child_data(c), "currentGoal": goal_data(current_goal(db, c.id)), "pendingRecommendations": sum(r.child_id == c.id for r in pending)} for c in children], "recentSessions": [{"id": s.id, "childId": s.child_id, "mode": s.mode, "isSeed": s.is_seed, "startedAt": s.started_at, "status": s.status} for s in sessions], "pendingRecommendations": [rec_payload(db, r) for r in pending]}


@router.get("/children/{child_id}/recommendations")
def recommendations(child_id: str, status: str | None = None, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """아동의 legacy 목표 추천을 최신순으로 돌려준다(상태로 거를 수 있음).
    추천 검토 화면에서 출처(provenance)와 함께 보여 준다."""
    owned_child(db, child_id, therapist)
    query = select(AIRecommendation).where(AIRecommendation.child_id == child_id)
    if status:
        query = query.where(AIRecommendation.status == status)
    return [rec_payload(db, r) for r in db.scalars(query.order_by(AIRecommendation.created_at.desc())).all()]


@router.get("/children/{child_id}/activity-recommendations")
def activity_recommendations(child_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """아동의 활동 제안 이력을 최신순으로 돌려준다.
    치료사가 지난 제안과 결정을 볼 때 쓴다."""
    owned_child(db, child_id, therapist)
    rows = db.scalars(select(ActivityRecommendation).where(ActivityRecommendation.child_id == child_id)
                      .order_by(ActivityRecommendation.created_at.desc())).all()
    return [activity_rec_data(row) for row in rows]


@router.post("/children/{child_id}/activity-recommendations")
def generate_activity_recommendation(child_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """대기 중인 활동 제안이 있으면 그것을, 없으면 확인 관찰로 새 제안을 만들어 돌려준다.
    자료가 부족하면 INSUFFICIENT_DATA를 돌려주며 제안은 치료사 결정 전까지 PENDING이다."""
    owned_child(db, child_id, therapist)
    pending = db.scalar(select(ActivityRecommendation).where(ActivityRecommendation.child_id == child_id,
                                                               ActivityRecommendation.status == "PENDING"))
    if pending:
        return {"status": "PENDING", "recommendation": activity_rec_data(pending)}
    proposal = propose_activity(db, child_id)
    if proposal is None:
        return {"status": "INSUFFICIENT_DATA", "recommendation": None}
    rec = ActivityRecommendation(child_id=child_id, activity=proposal["activity"],
                                 clinical_purpose=proposal["clinicalPurpose"], reason=proposal["reason"],
                                 evidence=proposal["evidence"], confidence=proposal["confidence"])
    db.add(rec)
    db.commit()
    return {"status": "PENDING", "recommendation": activity_rec_data(rec)}


@router.post("/activity-recommendations/{recommendation_id}/decision")
def decide_activity_recommendation(recommendation_id: str, body: ActivityRecommendationDecisionInput,
                                   db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """활동 제안을 수락·수정·거절하고, 거절이 아니면 아동의 배정 활동을 바꾼다.
    치료사 결정만 아동 화면에 반영되게 하는 경계이며 결정은 감사 기록으로 남긴다."""
    rec = db.get(ActivityRecommendation, recommendation_id)
    if rec is None:
        raise HTTPException(404)
    child = owned_child(db, rec.child_id, therapist)
    if rec.status != "PENDING":
        raise HTTPException(409, "이미 처리한 제안입니다")
    if body.action == "modify" and (body.modified_game is None or not body.note.strip()):
        raise HTTPException(422, "수정할 게임과 이유가 필요합니다")
    if body.action == "reject" and not body.note.strip():
        raise HTTPException(422, "거절 사유가 필요합니다")
    if body.action != "reject":
        child.collection_json = {**(child.collection_json or {}),
                                 "assignedActivity": body.modified_game if body.action == "modify" else rec.activity}
    rec.status = {"accept": "ACCEPTED", "modify": "MODIFIED", "reject": "REJECTED"}[body.action]
    rec.selected_activity = None if body.action == "reject" else body.modified_game if body.action == "modify" else rec.activity
    rec.decision_note = body.note
    rec.decided_at = now()
    rec.decided_by = therapist.id
    db.add(AuditEvent(actor_id=therapist.id, action=f"RECOMMENDATION_{rec.status}",
                      resource_id=rec.id, result="SUCCESS"))
    db.commit()
    return activity_rec_data(rec)


@router.get("/sessions/{session_id}")
def session_detail(session_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """회기 정보·목표·계획·발화와 분석·결정·이벤트·지표·인사이트·추천·활성 규칙을 돌려준다.
    치료사의 회기 상세 화면에서 쓴다."""
    session = db.get(TrainingSession, session_id)
    if not session:
        raise HTTPException(404)
    owned_child(db, session.child_id, therapist)
    utterances = db.scalars(select(Utterance).where(Utterance.session_id == session.id).order_by(Utterance.created_at)).all()
    decisions = db.scalars(select(TrainingDecision).where(TrainingDecision.session_id == session.id).order_by(TrainingDecision.created_at)).all()
    events = db.scalars(select(GameEvent).where(GameEvent.session_id == session.id).order_by(GameEvent.created_at)).all()
    metrics = db.scalars(select(ProgressMetric).where(ProgressMetric.session_id == session.id)).all()
    recs = db.scalars(select(AIRecommendation).where(AIRecommendation.session_id == session.id)).all()
    goal = db.get(TrainingGoal, session.goal_id)
    rules = db.scalars(select(TherapistRule).where(TherapistRule.child_id == session.child_id, TherapistRule.active == True)).all()
    return {"session": {"id": session.id, "mode": session.mode, "activityGame": session.runtime_state.get("activityGame"), "isSeed": session.is_seed, "status": session.status, "startedAt": session.started_at, "endedAt": session.ended_at, "durationSec": session.summary_json.get("durationSec"), "goalVersion": goal.version, "summary": session.summary_json}, "goal": goal_data(goal), "plan": db.get(TrainingPlan, session.plan_id).plan_json, "utterances": [{"id": u.id, "itemText": u.item_text, "level": u.level, "game": u.game, "recognizer": u.recognizer, "stageIndex": u.stage_index, "acoustic": u.acoustic, "createdAt": u.created_at, "transcript": u.transcript, "attemptIndex": u.attempt_index, "analysis": public(db.scalar(select(SpeechAnalysis).where(SpeechAnalysis.utterance_id == u.id)), ["ai_score", "ai_result", "final_score", "final_result", "target_status", "substitute_symbol", "pattern_tags", "therapist_override", "rule_applied_id", "method"])} for u in utterances], "decisions": [public(d, ["id", "decision_type", "reason_codes", "reason_text", "inputs_snapshot", "from_level", "to_level"]) for d in decisions], "events": [public(e, ["id", "type", "payload", "therapist_text", "utterance_id", "created_at"]) for e in events], "metrics": [public(m, ["attempts", "successes", "retries", "hints", "first_try_success_rate", "mean_score", "level_down_count"]) for m in metrics], "insight": session.insight_json, "recommendations": [rec_payload(db, r) for r in recs], "activeRules": [public(r, ["id", "rule_type", "params"]) for r in rules]}


@router.get("/sessions/{session_id}/timeline")
def session_timeline(session_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """회기의 게임 이벤트와 임상 관찰을 시간순으로 돌려준다.
    치료사가 회기 흐름을 따라가며 관찰을 검토할 때 쓴다."""
    session = db.get(TrainingSession, session_id)
    if session is None:
        raise HTTPException(404)
    owned_child(db, session.child_id, therapist)
    events = db.scalars(select(GameEvent).where(GameEvent.session_id == session_id).order_by(GameEvent.created_at)).all()
    observations = db.scalars(select(ClinicalObservation).where(ClinicalObservation.session_id == session_id).order_by(ClinicalObservation.created_at)).all()
    return {"events": [public(event, ["id", "type", "payload", "utterance_id", "created_at"]) for event in events],
            "observations": [observation_data(observation, session) for observation in observations]}


@router.get("/sessions/{session_id}/clinical-summary")
def clinical_summary(session_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """라운드별로 관찰 수·평가 가능 수·치료사 확인 수·확인 성공률을 계산해 돌려준다.
    임상 적격(실제·비샘플) 회기만 세며 각 관찰은 마지막 치료사 결정만 반영한다."""
    session = db.get(TrainingSession, session_id)
    if session is None:
        raise HTTPException(404)
    owned_child(db, session.child_id, therapist)
    game = session.runtime_state.get("activityGame")
    definitions = GAME_ROUNDS.get(game, ())
    observations = db.scalars(select(ClinicalObservation).where(ClinicalObservation.session_id == session_id)).all()
    decisions = latest_decisions(db, observations)
    rows = []
    for definition in definitions:
        all_round = [row for row in observations if row.round_index == definition.index and not crossing.is_probe_observation(row)]
        sample = all_round if clinical_eligible(session) else []
        verified = 0
        verified_evaluated = 0
        verified_observed = 0
        verified_success = 0
        for row in sample:
            latest = decisions.get(row.id)
            if latest and latest.action in {"confirm", "correct"}:
                verified += 1
                value = latest.correction.get("result") if latest.action == "correct" else row.ai_result
                # 불확실·무발화·대화 속 목표 관찰은 정오 판정이 아니므로 비율의 분모에 넣지 않는다.
                if value in {"success", "retry"}:
                    verified_evaluated += 1
                    verified_success += value == "success"
                elif value == "target_observed":
                    verified_observed += 1
        # totalObservedN: 실제 음성 관찰 전체. evaluableN: AI가 success/retry로 판정한 표본만.
        # 불확실·무발화·대화 속 목표 관찰은 평가 가능한 표본 수를 늘리지 않는다. 자료 없음은 0점이 아니다.
        evaluable = sum(row.ai_result in {"success", "retry"} for row in sample)
        rows.append({"roundIndex": definition.index, "roundId": definition.id,
                     "clinicalFocus": definition.clinical_focus,
                     "totalObservedN": len(sample), "evaluableN": evaluable, "demoN": len(all_round) - len(sample),
                     "aiSupportedSuccesses": sum(row.ai_result == "success" for row in sample),
                     "uncertainN": sum(row.ai_result == "uncertain" for row in sample),
                     "noSpeechN": sum(row.ai_result == "no_speech" for row in sample),
                     "targetObservedN": sum(row.ai_result == "target_observed" for row in sample),
                     "verifiedN": verified, "verifiedEvaluatedN": verified_evaluated,
                     "verifiedObservedN": verified_observed, "verifiedSuccesses": verified_success,
                     "verifiedRate": round(100 * verified_success / verified_evaluated, 1) if verified_evaluated else None,
                     "limitedData": evaluable < 5})
    return {"game": game, "rounds": rows}


@router.get("/observations/{observation_id}")
def observation_detail(observation_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """임상 관찰 하나와 치료사 결정 이력을 돌려준다.
    관찰 검토 상세 화면에서 쓴다."""
    observation = db.get(ClinicalObservation, observation_id)
    if observation is None:
        raise HTTPException(404)
    owned_child(db, observation.child_id, therapist)
    decisions = db.scalars(select(ClinicalVerification).where(ClinicalVerification.observation_id == observation_id).order_by(ClinicalVerification.created_at)).all()
    return {"observation": observation_data(observation, db.get(TrainingSession, observation.session_id)),
            "decisions": [public(row, ["id", "action", "correction", "note", "created_at"]) for row in decisions]}


@router.post("/observations/{observation_id}/decision")
def decide_observation(observation_id: str, body: ObservationDecisionInput, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """임상 관찰을 확인·교정·거부하고 검증 상태를 바꾼다.
    DEMO 관찰은 DEMO_ 상태로 구분해 임상 검증 자료로 승격하지 않는다."""
    observation = db.get(ClinicalObservation, observation_id)
    if observation is None:
        raise HTTPException(404)
    owned_child(db, observation.child_id, therapist)
    if body.action == "correct" and body.corrected_result is None:
        raise HTTPException(422, "교정 결과가 필요합니다")
    if body.action == "reject" and not body.note.strip():
        raise HTTPException(422, "거부 사유가 필요합니다")
    correction = {"result": body.corrected_result} if body.action == "correct" else {}
    db.add(ClinicalVerification(observation_id=observation.id, therapist_id=therapist.id,
                                action=body.action, correction=correction, note=body.note))
    session = db.get(TrainingSession, observation.session_id)
    state = {"confirm": "CONFIRMED", "correct": "CORRECTED", "reject": "REJECTED"}[body.action]
    # DEMO 관찰도 검토할 수 있지만 임상 검증 자료로 승격하지 않도록 상태를 구분한다.
    observation.verification_state = state if clinical_eligible(session) else f"DEMO_{state}"
    db.add(AuditEvent(actor_id=therapist.id, action=f"THERAPIST_{body.action.upper()}",
                      resource_id=observation.id, result="SUCCESS"))
    db.commit()
    return observation_data(observation, session)


@router.post("/recommendations/{recommendation_id}/decision")
def decide_recommendation(recommendation_id: str, body: RecommendationDecisionInput, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """legacy 목표 추천을 수락·수정·거절하고, 수락·수정이면 새 목표 버전을 만든다.
    치료사 결정을 피드백으로 남기며 DEMO 연습에서 나온 추천은 거절만 할 수 있다."""
    rec = db.get(AIRecommendation, recommendation_id)
    if not rec:
        raise HTTPException(404)
    child = owned_child(db, rec.child_id, therapist)
    if rec.status != "pending":
        raise HTTPException(409, "이미 처리한 추천입니다")
    if body.action not in ("accept", "modify", "reject") or (body.action == "reject" and not body.note.strip()):
        raise HTTPException(422, "결정 또는 거절 사유가 필요합니다")
    if body.action != "reject" and not legacy_recommendation_allowed(db.get(TrainingSession, rec.session_id)):
        # 수정 전에 저장된 DEMO 연습 기반 추천은 목표를 바꾸지 못한다. 거절(기록)만 할 수 있다.
        raise HTTPException(409, "DEMO 연습에서 나온 추천은 목표에 반영할 수 없습니다. 거절만 할 수 있습니다")
    goal = None
    if body.action != "reject":
        patch = rec.suggested_goal if body.action == "accept" else body.modified_goal
        if patch is None:
            raise HTTPException(422, "수정 목표가 필요합니다")
        fields = {key: patch[to_camel(key)] if to_camel(key) in patch else patch[key] for key in GoalInput.model_fields if key in patch or to_camel(key) in patch}
        goal = create_goal(db, child, fields, "recommendation" if body.action == "accept" else "recommendation_modified", rec.id)
    rec.status, rec.decided_at = {"accept": "accepted", "modify": "modified", "reject": "rejected"}[body.action], now()
    feedback = TherapistFeedback(therapist_id=therapist.id, child_id=child.id, kind="recommendation_decision", recommendation_id=rec.id, action=body.action, payload=body.model_dump(), note=body.note, resulting_goal_id=goal.id if goal else None)
    db.add(feedback)
    db.commit()
    return {"recommendation": rec_data(rec), "feedback": {"id": feedback.id, "action": feedback.action}, "newGoal": goal_data(goal) if goal else None}


@router.post("/utterances/{utterance_id}/feedback")
def utterance_feedback(utterance_id: str, body: FeedbackInput, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """발화 하나를 치료사가 교정·채점·메모하고 필요하면 아동별 규칙을 만든 뒤 지표를 다시 계산한다.
    자동 판정을 치료사 판단으로 덮어쓸 때 쓴다."""
    utterance = db.get(Utterance, utterance_id)
    if not utterance:
        raise HTTPException(404)
    session = db.get(TrainingSession, utterance.session_id)
    child = owned_child(db, session.child_id, therapist)
    analysis = db.scalar(select(SpeechAnalysis).where(SpeechAnalysis.utterance_id == utterance.id))
    goal = db.get(TrainingGoal, session.goal_id)
    if body.action not in ("not_error", "allowed_at_stage", "cue_needed", "manual_score", "note"):
        raise HTTPException(422)
    if body.action == "manual_score" and (body.score is None or not 0 <= body.score <= 100):
        raise HTTPException(422, "0~100 점수가 필요합니다")
    if body.action == "cue_needed" and not body.cue:
        raise HTTPException(422, "단서 종류가 필요합니다")
    if body.action in ("not_error", "allowed_at_stage"):
        analysis.final_result, analysis.therapist_override = "success", True
    if body.action == "manual_score":
        analysis.final_score, analysis.therapist_override = body.score, True
    feedback = TherapistFeedback(therapist_id=therapist.id, child_id=child.id, kind="utterance_correction", utterance_id=utterance.id, action=body.action, payload=body.model_dump(), note=body.note)
    db.add(feedback)
    db.flush()
    rule = None
    if body.action == "cue_needed":
        rule = TherapistRule(child_id=child.id, rule_type="CUE_OVERRIDE", params={"cue": body.cue}, level_at_creation=goal.level, source_feedback_id=feedback.id)
    elif body.action == "allowed_at_stage":
        rule = TherapistRule(child_id=child.id, rule_type="ACCEPT_SUBSTITUTION", params={"phoneme": goal.target_phoneme, "substitute": analysis.substitute_symbol, "scope": "until_level_change"}, level_at_creation=goal.level, source_feedback_id=feedback.id)
    elif body.action == "not_error" and body.create_rule and utterance.transcript:
        rule = TherapistRule(child_id=child.id, rule_type="ACCEPT_WORD_VARIANT", params={"item_text": utterance.item_text, "variant": utterance.transcript}, level_at_creation=goal.level, source_feedback_id=feedback.id)
    if rule:
        db.add(rule)
        db.flush()
        feedback.resulting_rule_id = rule.id
    recompute(db, session, goal, session.summary_json.get("durationSec", 0))
    db.commit()
    return {"feedback": {"id": feedback.id, "action": feedback.action}, "analysis": public(analysis, ["ai_score", "ai_result", "final_score", "final_result", "therapist_override"]), "rule": public(rule, ["id", "rule_type", "params"]) if rule else None}


@router.post("/rules/{rule_id}/deactivate")
def deactivate_rule(rule_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """치료사 규칙 하나를 비활성화한다.
    치료사가 더 이상 맞지 않는 규칙을 끌 때 쓴다."""
    rule = db.get(TherapistRule, rule_id)
    if not rule:
        raise HTTPException(404)
    owned_child(db, rule.child_id, therapist)
    rule.active, rule.deactivated_reason = False, "therapist"
    db.commit()
    return public(rule, ["id", "rule_type", "params", "active"])
