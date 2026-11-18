"""아동별 게임 설정을 DB에서 읽고 저장한다(설정이 없으면 기본값).
game_settings/api.py와 main.py의 게임 시작 처리가 부른다. 임상 판정과는 관계없는 화면 설정이다.
"""

from datetime import timezone

from ..models import AuditEvent, Therapist, now
from .models import ChildGameSettings
from .schemas import DEFAULT_ALLOW_FASTER, DEFAULT_START_BPM


def rhythm_snapshot(db, child_id):
    """아동의 현재 박자 설정(startBpm, allowFaster)을 dict로 돌려준다. 저장된 설정이 없으면 기본값이다.
    main.py가 횡단보도 게임을 시작할 때 이 값을 회기 상태에 복사해 두어, 나중에 설정이 바뀌어도 그 회기 기록은 그대로 남는다.
    """
    row = db.get(ChildGameSettings, child_id)
    return {"startBpm": row.start_bpm if row else DEFAULT_START_BPM,
            "allowFaster": row.allow_faster if row else DEFAULT_ALLOW_FASTER}


def game_settings(db, child_id):
    """박자 설정에 마지막 수정 시각과 수정한 치료사 이름을 더해 화면용 dict로 만든다.
    설정 조회 API와 저장 API 응답에 쓴다.
    """
    row = db.get(ChildGameSettings, child_id)
    therapist = db.get(Therapist, row.updated_by) if row else None
    at = row.updated_at.replace(tzinfo=timezone.utc) if row and row.updated_at.tzinfo is None else row.updated_at if row else None
    return {"daeguCrossing": {**rhythm_snapshot(db, child_id),
                              "updatedAt": at.isoformat() if at else None,
                              "updatedBy": therapist.display_name if therapist else None}}


def update_game_settings(db, child_id, therapist, rhythm):
    """아동의 박자 설정을 저장하고 감사 기록을 남긴 뒤 새 설정을 돌려준다. 값이 그대로면 아무것도 저장하지 않는다.
    치료사 화면의 설정 저장 API가 부른다.
    """
    therapist_id = therapist.id
    row = db.get(ChildGameSettings, child_id)
    if row is not None and row.start_bpm == rhythm.startBpm and row.allow_faster == rhythm.allowFaster:
        return game_settings(db, child_id)
    if row is None:
        row = ChildGameSettings(child_id=child_id)
        db.add(row)
    row.start_bpm = rhythm.startBpm
    row.allow_faster = rhythm.allowFaster
    row.updated_at = now()
    row.updated_by = therapist_id
    db.add(AuditEvent(actor_id=therapist_id, action="GAME_SETTINGS_UPDATE", resource_id=child_id, result="SUCCESS"))
    db.flush()
    return game_settings(db, child_id)
