"""아동별 게임 설정(대구 횡단보도 시작 BPM 76~100·기본 84, 빨라지기 허용)의 입력 형식·DB 표(child_game_settings)와 읽기·저장 함수.
설정 API·회기 상태 박자 검사·게임 시작 처리가 함께 쓴다. 임상 판정과는 관계없는 화면 설정이며, 기존 아동·회기 표를 바꾸지 않고 새 표만 추가한다.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..models import AuditEvent, Therapist, now


MIN_START_BPM = 76
MAX_START_BPM = 100
DEFAULT_START_BPM = 84
DEFAULT_ALLOW_FASTER = True


class DaeguCrossingRhythm(BaseModel):
    """대구 횡단보도 게임의 박자 설정 형식(시작 BPM과 빨라지기 허용 여부). 모르는 키는 거절한다."""
    model_config = ConfigDict(extra="forbid", strict=True)
    startBpm: StrictInt = Field(ge=MIN_START_BPM, le=MAX_START_BPM)
    allowFaster: StrictBool


class GameSettingsInput(BaseModel):
    """게임 설정 저장 요청 본문 형식. 지금은 대구 횡단보도 박자 설정 하나만 담는다."""
    model_config = ConfigDict(extra="forbid", strict=True)
    daeguCrossing: DaeguCrossingRhythm


class ChildGameSettings(Base):
    """아동별 대구 횡단보도 게임 박자 설정(시작 BPM, 빨라지기 허용)과 마지막 수정자. 새 표만 추가하며 기존 아동·회기 표의 열과 의미는 유지한다."""
    __tablename__ = "child_game_settings"
    __table_args__ = (CheckConstraint(f"start_bpm >= {MIN_START_BPM} AND start_bpm <= {MAX_START_BPM}",
                                     name="ck_game_settings_start_bpm"),)

    child_id: Mapped[str] = mapped_column(ForeignKey("children.id"), primary_key=True)
    start_bpm: Mapped[int] = mapped_column(Integer, default=DEFAULT_START_BPM)
    allow_faster: Mapped[bool] = mapped_column(Boolean, default=DEFAULT_ALLOW_FASTER)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_by: Mapped[str] = mapped_column(ForeignKey("therapists.id"))


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
