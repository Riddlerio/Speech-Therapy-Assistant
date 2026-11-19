"""대구대 건너기 회기의 진행 기기(lease)·참여 시간·화면 상태 같은 비임상 진행 상태를 저장하고 계산한다.
임상 평가와 분리하며, adventure/api.py와 main.py의 게임 API가 부른다.
"""
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..games.rounds import GAME_ROUNDS
from ..games import crossing
from ..models import ActivityLease, Child, RoundMaterialReward, TrainingSession, now
from ..security import hash_token
from ..session_state import activity_state

LEASE_TIMEOUT_SEC = 45


def utc(value):
    """시간대가 없는 시각은 UTC로 간주하고, 있는 시각은 UTC로 바꿔 돌려준다.
    SQLite에서 읽은 시각은 시간대가 빠질 수 있어서 시간 차이를 계산하기 전에 맞춘다.
    """
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def serialize_child(db: Session, child_id: str):
    """같은 아동에 대한 동시 요청이 차례로 처리되게 잠근다. SQLite는 읽기 전에 쓰기 예약을 잡고, 다른 DB는 아동 행 잠금을 쓴다.
    lease·발화 제출·완료처럼 두 요청이 동시에 오면 값이 꼬일 수 있는 API가 처리 시작 때 부른다.
    """
    connection = db.connection()
    if connection.dialect.name == "sqlite":
        # 인증 의존성의 SELECT는 SQLAlchemy 논리 transaction만 연다.
        # 이미 실제 transaction이면 쓰기 예약을 보유한 호출자의 transaction을 유지한다.
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        db.execute(select(Child).where(Child.id == child_id).with_for_update())
    db.expire_all()


def owned_activity(db, session_id, child_id):
    """세션이 이 아동의 진행 중인 5라운드 게임인지 확인하고 돌려준다. 아니면 404·409다.
    claim·heartbeat·pause API가 세션을 다루기 전에 부른다.
    """
    session = db.get(TrainingSession, session_id)
    if not session or session.child_id != child_id:
        raise HTTPException(404, "세션을 찾을 수 없습니다")
    activity_state(session)
    if session.status != "active":
        raise HTTPException(409, "활성 게임이 아닙니다")
    return session


def lease_paused(lease, at=None):
    """lease가 중단 표시됐거나 마지막 생존 신호 뒤 LEASE_TIMEOUT_SEC(45초)가 지났으면 중단으로 본다.
    이어 하기 목록과 lease 검사에서 기기 연결이 끊겼는지 판단할 때 쓴다.
    """
    at = at or now()
    return bool(lease and (lease.paused_at is not None or
                           (utc(at) - utc(lease.heartbeat_at)).total_seconds() >= LEASE_TIMEOUT_SEC))


def check_lease(db, session, token, *, allow_paused=False):
    """요청한 기기의 lease 토큰이 현재 진행 기기와 같은지 확인한다. lease가 없으면(이전 API·개발 DB) 검사를 건너뛴다.
    두 기기가 같은 게임을 동시에 진행하지 못하게 하려고 발화 제출·완료 같은 API가 부른다.
    """
    lease = db.get(ActivityLease, session.id)
    if lease is None:
        return None  # 이전 API·개발 DB는 claim 이후에 한 기기 진행 계약을 적용한다.
    if not token or not hmac.compare_digest(hash_token(token), lease.token_hash):
        raise HTTPException(409, "다른 기기에서 진행 중입니다. 이어받기를 선택해 주세요")
    if not allow_paused and lease_paused(lease):
        raise HTTPException(409, "중단된 활동을 다시 이어받아 주세요")
    return lease


def accrue_active_time(lease, session, at):
    """마지막 생존 신호 뒤 지난 시간을 실제 참여 시간(active_elapsed_sec)에 더한다.
    중단 중이거나 공백이 45초 이상이면 더하지 않아, 쉬던 시간이 참여 시간으로 잡히지 않게 한다.
    """
    if lease.paused_at is None and (not session.runtime_state.get("roundsComplete") or crossing.probe_active(session.runtime_state)):
        elapsed = (utc(at) - utc(lease.heartbeat_at)).total_seconds()
        if 0 <= elapsed < LEASE_TIMEOUT_SEC:
            lease.active_elapsed_sec = (lease.active_elapsed_sec or 0.0) + elapsed


def activity_payload(db, session):
    """아동 화면이 대구대 건너기를 그리는 데 필요한 상태(라운드 목록, 현재 라운드·문항, 박자·줄 위치 등)를 만든다.
    claim과 main.py의 게임 조회·시작·판 추가·확인 낱말 API가 돌려준다.
    """
    state = activity_state(session)
    game = state["activityGame"]
    child = db.get(Child, session.child_id)
    complete = bool(state.get("roundsComplete")) and not crossing.probe_active(state)
    return {"sessionId": session.id, "game": game, "mode": session.mode,
            "heroName": child.hero_name,
            "rounds": [crossing.child_round(definition) for definition in GAME_ROUNDS[game]],
            "currentRound": None if complete else crossing.child_round(crossing.definition_for_item(state)),
            "firstItem": None if complete else state["currentItem"],
            "nextAttemptIndex": state["roundAttempt"],
            "completedRounds": list(range(1, 6 if state.get("roundsComplete") else state["roundIndex"])),
            **({"rhythm": state["rhythm"]} if state.get("rhythm") is not None else {}),
            **crossing.cursor(state)}


def claim_activity(db, session, token, takeover):
    """이 기기에 새 lease 토큰을 발급하고 게임 상태와 함께 돌려준다. 다른 기기가 진행 중이면 takeover일 때만 가져온다.
    이어받을 때는 끊겨 있던 시간을 라운드 시간에서 빼서, 쉰 시간이 라운드 소요 시간에 들어가지 않게 한다.
    """
    lease = db.get(ActivityLease, session.id)
    at = now()
    if lease is not None:
        matches = bool(token and hmac.compare_digest(hash_token(token), lease.token_hash))
        if not matches and not takeover:
            raise HTTPException(409, "다른 기기에서 진행 중입니다. 이어받기를 선택해 주세요")
        # 이어받기는 입력을 새로 여는 경계다. 마지막 서버 확인 이후 공백은
        # 짧은 연결 끊김이라도 참여 시간으로 추정하지 않는다.
        pause_start = lease.paused_at or lease.heartbeat_at
        if pause_start is not None and not session.runtime_state.get("roundsComplete"):
            state = activity_state(session)
            paused_seconds = max(0.0, (utc(at) - utc(pause_start)).total_seconds())
            started = datetime.fromisoformat(state["roundStartedAt"])
            state["roundStartedAt"] = (utc(started) + timedelta(seconds=paused_seconds)).isoformat()
            session.runtime_state = state
        lease.paused_at = None
        lease.heartbeat_at = at
    else:
        # 이전 버전에는 중단 시각이 없다. 승인된 정책대로 현재 라운드
        # 시간만 새로 시작하고 항목·시도·완료 라운드는 그대로 둔다.
        if not session.runtime_state.get("roundsComplete"):
            state = activity_state(session)
            state["roundStartedAt"] = at.isoformat()
            session.runtime_state = state
        lease = ActivityLease(session_id=session.id, child_id=session.child_id,
                              token_hash="", heartbeat_at=at, active_elapsed_sec=0.0)
        db.add(lease)
    issued = secrets.token_urlsafe(32)
    lease.token_hash = hash_token(issued)
    db.flush()
    return {**activity_payload(db, session), "leaseToken": issued}


def heartbeat(db, session, token):
    """lease 토큰을 확인하고 참여 시간을 누적한 뒤 마지막 생존 시각을 갱신한다.
    adventure/api.py의 heartbeat 엔드포인트와 main.py의 발화 제출 처리에서 부른다.
    """
    lease = check_lease(db, session, token)
    if lease is None:
        raise HTTPException(409, "활동을 먼저 이어받아 주세요")
    at = now()
    accrue_active_time(lease, session, at)
    lease.heartbeat_at = at


def pause(db, session, token):
    """lease를 중단 상태로 바꾸고 그때까지의 참여 시간을 누적한다. 이미 연결이 끊겨 있었다면 마지막 신호 시각을 중단 시각으로 쓴다.
    아동 화면이 게임을 벗어날 때 adventure/api.py의 pause 엔드포인트가 부른다.
    """
    lease = check_lease(db, session, token, allow_paused=True)
    if lease is None:
        raise HTTPException(409, "활동을 먼저 이어받아 주세요")
    if lease.paused_at is None:
        at = now()
        expired = lease_paused(lease, at)
        accrue_active_time(lease, session, at)
        lease.paused_at = lease.heartbeat_at if expired else at
        lease.heartbeat_at = at


def remove_session_links(db, session_ids):
    """발화 자료 삭제 API의 세션 삭제를 외래키가 막지 않도록 lease·(이전 게임의) 재료 지급 참조를 정리한다.
    therapist_api.py의 아동 음성 자료 삭제 처리가 TrainingSession을 지우기 직전에 부른다.
    """
    if session_ids:
        db.execute(delete(ActivityLease).where(ActivityLease.session_id.in_(session_ids)))
        db.execute(delete(RoundMaterialReward).where(RoundMaterialReward.session_id.in_(session_ids)))
