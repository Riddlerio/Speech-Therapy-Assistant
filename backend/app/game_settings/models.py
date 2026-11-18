"""아동별 게임 설정 테이블(child_game_settings)을 정의한다.
기존 아동·회기 표를 바꾸지 않고 새 표만 추가해 게임 설정을 따로 저장하려고 있다.
"""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..models import now
from .schemas import DEFAULT_ALLOW_FASTER, DEFAULT_START_BPM, MAX_START_BPM, MIN_START_BPM


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
