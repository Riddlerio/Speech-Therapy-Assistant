"""두두 모험 API: 치료사의 매직빔 스킬 승인·철회, 아동의 소지품·음식 만들기, 진행 기기(lease) 이어받기·생존 신호·중단.
기존 인증·CSRF·소유권 의존성을 그대로 적용하며, 실제 처리는 adventure/service.py에 맡긴다.
"""
from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import owned_child, require_student, require_therapist
from ..db import get_db
from ..models import Account, ActivityLease, Therapist, TrainingSession
from ..session_state import activity_state, state_guard
from . import service
from .schemas import ClaimInput, CraftInput, SkillDecisionInput

router = APIRouter(prefix="/api")


@router.get("/children/{child_id}/skills/magic_beam")
def get_skill(child_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """담당 아동의 매직빔 스킬 승인 상태와 승인·철회 이력을 돌려준다.
    치료사 화면이 스킬 카드를 그릴 때 호출한다. 담당 아동이 아니면 owned_child가 막는다.
    """
    owned_child(db, child_id, therapist)
    return service.skill_status(db, child_id)


def _decision(child_id, body, db, therapist, action):
    """스킬 승인·철회 요청의 공통 처리. 아동 단위로 직렬화하고 소유권을 확인한 뒤 결정을 저장한다.
    grant_skill·revoke_skill이 action 값만 바꿔 함께 쓴다.
    """
    service.serialize_child(db, child_id)
    owned_child(db, child_id, therapist)
    result = service.decide_skill(db, child_id, therapist, action, body)
    db.commit()
    return result


@router.post("/children/{child_id}/skills/magic_beam/grant")
def grant_skill(child_id: str, body: SkillDecisionInput, db: Session = Depends(get_db),
                therapist: Therapist = Depends(require_therapist)):
    """치료사가 아동에게 매직빔 스킬을 승인한다.
    치료사 화면의 스킬 승인 버튼이 호출하며, 결과로 바뀐 스킬 상태를 돌려준다.
    """
    return _decision(child_id, body, db, therapist, "GRANT")


@router.post("/children/{child_id}/skills/magic_beam/revoke")
def revoke_skill(child_id: str, body: SkillDecisionInput, db: Session = Depends(get_db),
                 therapist: Therapist = Depends(require_therapist)):
    """치료사가 아동의 매직빔 스킬 승인을 철회한다.
    치료사 화면의 철회 버튼이 호출한다. 철회는 진행 중 라운드가 끝난 뒤부터 적용된다.
    """
    return _decision(child_id, body, db, therapist, "REVOKE")


@router.get("/me/skills")
def my_skills(db: Session = Depends(get_db), account: Account = Depends(require_student)):
    """로그인한 아동이 매직빔 스킬을 쓸 수 있는지 돌려준다.
    아동 화면이 게임 선택지에 매직빔을 보여 줄지 정할 때 호출한다.
    """
    return {"magicBeam": service.has_magic_beam(db, account.child_id)}


@router.get("/me/adventure")
def my_adventure(db: Session = Depends(get_db), account: Account = Depends(require_student)):
    """로그인한 아동의 소지품(밥·참치)과 만든 음식(참치초밥) 개수를 돌려준다.
    아동 화면의 모험(음식 장면)이 소지품을 그릴 때 호출한다.
    """
    return service.inventory(db, account.child_id)


@router.post("/me/adventure/craft")
def make_food(body: CraftInput, db: Session = Depends(get_db), account: Account = Depends(require_student)):
    """아동이 모은 재료로 음식을 만든다(밥 1 + 참치 1 → 참치초밥 1).
    아동 화면의 만들기 버튼이 호출하며, 같은 요청 ID로 다시 보내도 재료를 두 번 쓰지 않는다.
    """
    child_id = account.child_id
    service.serialize_child(db, child_id)
    result = service.craft(db, child_id, body)
    db.commit()
    return result


@router.get("/me/activities/active")
@state_guard
def active_activities(db: Session = Depends(get_db), account: Account = Depends(require_student)):
    """로그인한 아동의 진행 중인 5라운드 게임 목록(현재 라운드, 완료 라운드, 중단 여부)을 돌려준다.
    아동 화면이 "이어 하기" 목록을 보여 줄 때 호출한다.
    """
    sessions = db.scalars(select(TrainingSession).where(TrainingSession.child_id == account.child_id,
                                                       TrainingSession.status == "active")
                          .order_by(TrainingSession.started_at.desc())).all()
    activities = []
    for session in sessions:
        if "activityGame" not in (session.runtime_state or {}):
            continue
        state = activity_state(session)
        complete = bool(state.get("roundsComplete"))
        activities.append({"sessionId": session.id, "game": state["activityGame"], "mode": session.mode,
                           "roundIndex": state["roundIndex"],
                           "completedRounds": list(range(1, 6 if complete else state["roundIndex"])),
                           "paused": service.lease_paused(db.get(ActivityLease, session.id))})
    return {"activities": activities}


@router.post("/activities/{session_id}/claim")
@state_guard
def claim(session_id: str, body: ClaimInput, db: Session = Depends(get_db),
          account: Account = Depends(require_student), x_activity_lease: str | None = Header(default=None)):
    """이 기기가 게임 진행권(lease)을 가져가고 새 lease 토큰과 현재 게임 상태를 돌려준다.
    아동이 게임을 열거나 다른 기기에서 하던 게임을 이어받을 때(takeover) 호출한다.
    """
    child_id = account.child_id
    service.serialize_child(db, child_id)
    session = service.owned_activity(db, session_id, child_id)
    result = service.claim_activity(db, session, x_activity_lease, body.takeover)
    db.commit()
    return result


@router.post("/activities/{session_id}/heartbeat")
@state_guard
def heartbeat(session_id: str, db: Session = Depends(get_db), account: Account = Depends(require_student),
              x_activity_lease: str | None = Header(default=None)):
    """진행 중인 기기가 아직 살아 있음을 알리고 실제 참여 시간을 누적한다.
    아동 화면이 게임 중 주기적으로 호출한다. 다른 기기의 토큰이면 409다.
    """
    child_id = account.child_id
    service.serialize_child(db, child_id)
    session = service.owned_activity(db, session_id, child_id)
    service.heartbeat(db, session, x_activity_lease)
    db.commit()
    return {"ok": True}


@router.post("/activities/{session_id}/pause")
@state_guard
def pause(session_id: str, db: Session = Depends(get_db), account: Account = Depends(require_student),
          x_activity_lease: str | None = Header(default=None)):
    """게임을 중단 상태로 표시하고 그때까지의 참여 시간을 정리한다.
    아동 화면이 게임을 벗어나거나 숨겨질 때 호출한다.
    """
    child_id = account.child_id
    service.serialize_child(db, child_id)
    session = service.owned_activity(db, session_id, child_id)
    service.pause(db, session, x_activity_lease)
    db.commit()
    return {"ok": True}
