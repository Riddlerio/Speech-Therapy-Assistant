"""활동 진행 기기(lease) API: 이어받기(claim)·생존 신호(heartbeat)·중단(pause).
기존 인증·CSRF·소유권 의존성을 그대로 적용하며, 실제 처리는 adventure/service.py에 맡긴다.
"""
from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from ..auth import require_student
from ..db import get_db
from ..models import Account
from ..session_state import state_guard
from . import service
from .schemas import ClaimInput

router = APIRouter(prefix="/api")


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
