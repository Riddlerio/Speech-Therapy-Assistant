"""호야 대화 제공자 공통 인터페이스와 제공자 오류를 정의한다.
제공자만 바꾸면 OpenAI·Anthropic·Gemini·로컬 LLM을 붙일 수 있게 하려고 있다.
"""
from typing import Protocol

from ..prompt.prompt_builder import HoyaDialogueContext
from ..schemas import ProviderOutput


class ProviderError(Exception):
    """제공자 실패를 알리는 예외. reason에는 아동 발화·key·프롬프트 같은 내용을 넣지 않는다.
    hoya/service.py가 잡아서 DEMO 응답으로 대체하고 사유 코드만 남긴다.
    """

    def __init__(self, reason: str):
        """실패 사유 코드를 예외 메시지와 reason 속성에 저장한다.
        서비스가 대체 사유(fallback_reason)로 그대로 기록할 수 있게 한다.
        """
        super().__init__(reason)
        self.reason = reason


class HoyaDialogueProvider(Protocol):
    """제공자가 갖춰야 할 모양: 이름(name), 모델 이름(model), 그리고 context를 받아 응답을 돌려주는 비동기 reply.
    OpenAIProvider·DemoProvider가 이 모양을 따른다.
    """
    name: str
    model: str | None

    async def reply(self, context: HoyaDialogueContext) -> ProviderOutput: ...
