"""스킬 권한·진행 기기(lease)·참여 재료·음식 만들기 같은 비임상 모험 상태를 저장하고 계산한다.
아이 동기 부여용 게임 요소라서 임상 평가와 분리하며, adventure/api.py와 main.py의 게임 API가 부른다.
"""
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..games.rounds import GAME_ROUNDS, public_round
from ..games import crossing
from ..models import (ActivityLease, AuditEvent, Child, CraftEvent, EpisodeProgress,
                      RoundMaterialReward, SkillGrant, Therapist, TrainingSession, now)
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
    재료 지급·제작·lease처럼 두 요청이 동시에 오면 값이 꼬일 수 있는 API가 처리 시작 때 부른다.
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


def skill_events(db, child_id):
    """아동의 매직빔 승인·철회 사건을 최신순으로 가져온다.
    승인 여부는 가장 최근 사건으로 정하므로 다른 스킬 함수들이 공통으로 쓴다.
    """
    return db.scalars(select(SkillGrant).where(SkillGrant.child_id == child_id,
                                              SkillGrant.skill == "magic_beam")
                      .order_by(SkillGrant.created_at.desc(), SkillGrant.id.desc())).all()


def has_magic_beam(db, child_id):
    """가장 최근 사건이 GRANT이면 아동이 지금 매직빔을 쓸 수 있다고 판단한다.
    아동 스킬 조회·라운드 시작·공격 검사에서 부른다.
    """
    events = skill_events(db, child_id)
    return bool(events and events[0].action == "GRANT")


def skill_status(db, child_id):
    """현재 승인 여부, 승인한 치료사 이름, 근거 종류·메모, 전체 이력을 화면용 dict로 만든다.
    치료사 화면의 스킬 카드와 승인·철회 응답에 쓴다.
    """
    events = skill_events(db, child_id)
    latest = events[0] if events else None
    granted = bool(latest and latest.action == "GRANT")
    def therapist_name(event):
        therapist = db.get(Therapist, event.therapist_id)
        return therapist.display_name if therapist else "치료사"
    return {"skill": "magic_beam", "granted": granted,
            "therapistName": therapist_name(latest) if latest else None,
            "grantedAt": utc(latest.created_at).isoformat() if granted else None,
            "evidenceKind": latest.evidence_kind if latest else None,
            "note": latest.note if latest else "",
            "history": [{"action": event.action, "therapistName": therapist_name(event),
                         "at": utc(event.created_at).isoformat(),
                         "evidenceKind": event.evidence_kind, "note": event.note} for event in events]}


def decide_skill(db, child_id, therapist, action, body):
    """치료사의 승인(GRANT)·철회(REVOKE) 사건과 감사 기록을 저장하고 새 상태를 돌려준다.
    이미 같은 상태면 아무것도 저장하지 않는다. adventure/api.py의 _decision이 부른다.
    """
    granted = has_magic_beam(db, child_id)
    if granted == (action == "GRANT"):
        return skill_status(db, child_id)
    event = SkillGrant(child_id=child_id, therapist_id=therapist.id, skill="magic_beam",
                       action=action, evidence_kind=body.evidence_kind, note=body.note)
    db.add(event)
    db.flush()
    db.add(AuditEvent(actor_id=therapist.id, action="SKILL_" + action,
                      resource_id=child_id, result="SUCCESS"))
    db.flush()
    return skill_status(db, child_id)


def round_magic_beam(db, state, child_id):
    """새 라운드를 열 때의 승인 상태를 정한다. 라운드가 끝날 때까지 이 값을 유지한다.
    main.py가 게임 시작·다음 라운드로 넘어갈 때 상태의 magicBeamRound에 저장한다.
    """
    return state.get("activityGame") == "monster_adventure" and has_magic_beam(db, child_id)


def magic_beam_available(db, state, child_id):
    """이번 라운드에서 매직빔을 고를 수 있는지 판단한다. 승인은 즉시, 철회는 다음 라운드부터 적용한다.
    아동 화면에 매직빔 버튼을 보여 줄지 정하는 응답 값에 쓴다.
    """
    return state.get("activityGame") == "monster_adventure" and (
        bool(state.get("magicBeamRound")) or has_magic_beam(db, child_id))


def check_attack(db, session, state, attack):
    """아동이 고른 공격(basic 또는 매직빔)이 지금 허용되는지 확인하고, 허용되면 이번 라운드 승인 상태를 고정한다.
    main.py의 발화 제출 API가 판정 전에 부르며, 몬스터 모험이 아니거나 승인되지 않았으면 422·403이다.
    """
    available = magic_beam_available(db, state, session.child_id)
    if available:
        # 라운드 도중 승인됐더라도 아이가 본 선택지는 그 라운드가 끝날 때까지 유지한다.
        state["magicBeamRound"] = True
    if attack == "basic":
        return
    if state.get("activityGame") != "monster_adventure":
        raise HTTPException(422, "매직빔 전투 스킬은 몬스터 모험에서 사용할 수 있습니다")
    if not available:
        raise HTTPException(403, "치료사가 승인한 스킬이 아닙니다")


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
        raise HTTPException(409, "중단된 모험을 다시 이어받아 주세요")
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
    """아동 화면이 게임을 그리는 데 필요한 상태(라운드 목록, 현재 라운드·문항, 완료 라운드 등)를 만든다.
    횡단보도 게임은 전용 형식(박자·줄 위치)으로 만든다. claim과 main.py의 게임 조회·시작 API가 돌려준다.
    """
    state = activity_state(session)
    game = state["activityGame"]
    child = db.get(Child, session.child_id)
    complete = bool(state.get("roundsComplete")) and not crossing.probe_active(state)
    if game == crossing.GAME:
        return {"sessionId": session.id, "game": game, "mode": session.mode,
                "heroName": child.hero_name,
                "rounds": [crossing.child_round(definition) for definition in GAME_ROUNDS[game]],
                "currentRound": None if complete else crossing.child_round(crossing.definition_for_item(state)),
                "firstItem": None if complete else state["currentItem"],
                "nextAttemptIndex": state["roundAttempt"],
                "completedRounds": list(range(1, 6 if state.get("roundsComplete") else state["roundIndex"])),
                **({"rhythm": state["rhythm"]} if state.get("rhythm") is not None else {}),
                **crossing.cursor(state)}
    return {"sessionId": session.id, "game": game, "mode": session.mode,
            "heroName": child.hero_name,
            "rounds": [public_round(definition) for definition in GAME_ROUNDS[game]],
            "currentRound": public_round(GAME_ROUNDS[game][state["roundIndex"] - 1], state["difficulty"]),
            "firstItem": state["currentItem"], "nextAttemptIndex": state["roundAttempt"],
            "completedRounds": list(range(1, 6 if complete else state["roundIndex"])),
            "sessionComplete": complete,
            "magicBeamAvailable": not complete and magic_beam_available(db, state, session.child_id)}


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
        raise HTTPException(409, "모험을 먼저 이어받아 주세요")
    at = now()
    accrue_active_time(lease, session, at)
    lease.heartbeat_at = at


def pause(db, session, token):
    """lease를 중단 상태로 바꾸고 그때까지의 참여 시간을 누적한다. 이미 연결이 끊겨 있었다면 마지막 신호 시각을 중단 시각으로 쓴다.
    아동 화면이 게임을 벗어날 때 adventure/api.py의 pause 엔드포인트가 부른다.
    """
    lease = check_lease(db, session, token, allow_paused=True)
    if lease is None:
        raise HTTPException(409, "모험을 먼저 이어받아 주세요")
    if lease.paused_at is None:
        at = now()
        expired = lease_paused(lease, at)
        accrue_active_time(lease, session, at)
        lease.paused_at = lease.heartbeat_at if expired else at
        lease.heartbeat_at = at


def progress(db, child_id, *, create=False):
    """아동의 모험 진행 행(EpisodeProgress)을 가져온다. create=True면 없을 때 빈 소지품으로 새로 만든다.
    소지품 조회·재료 지급·제작이 공통으로 쓴다.
    """
    row = db.get(EpisodeProgress, child_id)
    if row is None and create:
        row = EpisodeProgress(child_id=child_id, inventory_json={"rice": 0, "tuna": 0},
                              crafted_json={"tunaSushi": 0})
        db.add(row)
        db.flush()
    return row


def inventory(db, child_id):
    """아동의 소지품(밥·참치)과 만든 음식(참치초밥) 개수를 화면용 dict로 돌려준다.
    모험 화면 조회와 제작 결과 응답에 쓴다.
    """
    row = progress(db, child_id)
    return {"inventory": {"rice": (row.inventory_json if row else {}).get("rice", 0),
                          "tuna": (row.inventory_json if row else {}).get("tuna", 0)},
            "crafted": {"tunaSushi": (row.crafted_json if row else {}).get("tunaSushi", 0)}}


def award_material(db, session, round_index):
    """라운드 2·4를 마치면 게임에 따라 재료(매직빔→밥, 몬스터 모험→참치)를 한 번 지급한다.
    main.py가 라운드 완료 때 부르며, 같은 라운드에 대해 다시 불려도 두 번 주지 않는다(원장 기록).
    """
    material = {"magic_beam": "rice", "monster_adventure": "tuna"}.get(session.runtime_state["activityGame"])
    if round_index not in {2, 4} or material is None:
        return None
    previous = db.scalar(select(RoundMaterialReward).where(
        RoundMaterialReward.session_id == session.id, RoundMaterialReward.round_index == round_index))
    if previous:
        return previous.material
    row = progress(db, session.child_id, create=True)
    row.inventory_json = {**row.inventory_json, material: row.inventory_json.get(material, 0) + 1}
    db.add(RoundMaterialReward(child_id=session.child_id, session_id=session.id,
                              round_index=round_index, material=material))
    db.flush()
    return material


def materials_earned(db, session_id):
    """한 회기에서 받은 밥·참치 개수를 센다.
    main.py의 회기 완료 응답에 "이번에 모은 재료"로 보여 주려고 쓴다.
    """
    rows = db.scalars(select(RoundMaterialReward).where(RoundMaterialReward.session_id == session_id)).all()
    return {"rice": sum(row.material == "rice" for row in rows), "tuna": sum(row.material == "tuna" for row in rows)}


def remove_session_links(db, session_ids):
    """기존 발화 자료 삭제 API의 세션 삭제를 새 외래키가 막지 않도록 lease·재료 지급 참조만 정리한다.
    main.py의 아동 음성 자료 삭제 처리가 TrainingSession을 지우기 직전에 부른다.
    """
    if session_ids:
        db.execute(delete(ActivityLease).where(ActivityLease.session_id.in_(session_ids)))
        db.execute(delete(RoundMaterialReward).where(RoundMaterialReward.session_id.in_(session_ids)))


def craft(db, child_id, body):
    """밥 1개와 참치 1개를 써서 참치초밥 1개를 만들고 결과를 저장한다. 재료가 모자라면 409다.
    같은 요청 ID가 다시 오면 저장된 결과를 돌려줘 재료를 두 번 쓰지 않는다(다른 물건이면 409).
    """
    previous = db.scalar(select(CraftEvent).where(CraftEvent.child_id == child_id,
                                                 CraftEvent.request_id == body.request_id))
    if previous:
        if previous.recipe != body.recipe:
            raise HTTPException(409, "같은 제작 요청 ID를 다른 물건에 사용할 수 없습니다")
        return previous.result_json
    row = progress(db, child_id, create=True)
    if row.inventory_json.get("rice", 0) < 1 or row.inventory_json.get("tuna", 0) < 1:
        raise HTTPException(409, "밥과 참치가 하나씩 필요해요")
    row.inventory_json = {**row.inventory_json, "rice": row.inventory_json["rice"] - 1,
                          "tuna": row.inventory_json["tuna"] - 1}
    row.crafted_json = {**row.crafted_json, "tunaSushi": row.crafted_json.get("tunaSushi", 0) + 1}
    result = inventory(db, child_id)
    db.add(CraftEvent(child_id=child_id, request_id=body.request_id, recipe=body.recipe,
                     result_json=result))
    db.flush()
    return result
