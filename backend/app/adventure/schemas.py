"""모험(마법 빔 스킬·활동 이어받기·소풍 요리) API의 요청 본문 모델.
adventure/api.py 라우트가 받는 입력을 검증하고 모르는 필드는 모두 거부한다."""
from typing import Literal

from pydantic import Field

from ..schemas_base import StrictApiModel


class SkillDecisionInput(StrictApiModel):
    """치료사가 마법 빔 스킬을 주거나 거둘 때의 근거 종류와 메모.
    스킬 grant/revoke 라우트에서 쓰며 근거가 앱 관찰인지 직접 관찰인지 남긴다."""
    evidence_kind: Literal["APP_OBSERVATION", "DIRECT_OBSERVATION"]
    note: str = Field(default="", max_length=500)


class ClaimInput(StrictApiModel):
    """다른 기기에서 하던 활동을 이 기기로 이어받을지 여부.
    활동 claim 라우트에서 쓰며 takeover가 참이면 기존 임대를 넘겨받는다."""
    takeover: bool = False


class CraftInput(StrictApiModel):
    """모은 재료로 소풍 음식을 만드는 요청(중복 방지 id와 요리법).
    요리 라우트에서 쓰며 request_id로 같은 요청이 두 번 처리되지 않게 한다."""
    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    recipe: Literal["tuna_sushi"]
