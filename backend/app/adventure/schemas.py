"""활동 이어받기(claim) API의 요청 본문 모델.
adventure/api.py 라우트가 받는 입력을 검증하고 모르는 필드는 모두 거부한다."""
from ..schemas_base import StrictApiModel


class ClaimInput(StrictApiModel):
    """다른 기기에서 하던 활동을 이 기기로 이어받을지 여부.
    활동 claim 라우트에서 쓰며 takeover가 참이면 기존 임대를 넘겨받는다."""
    takeover: bool = False
