"""호야 대화 서비스: 제공자(OpenAI 또는 DEMO) 선택, 응답 검증, 실패 시 DemoProvider 대체.
대체 응답은 대화를 이어 가기 위한 것이며 아동 발음의 성공·실패를 기록하지 않는다. hoya/api.py가 사용한다.
"""
import logging

from ..config import Settings
from .prompt.prompt_builder import HoyaDialogueContext
from .providers.base import HoyaDialogueProvider, ProviderError
from .providers.demo_provider import DemoProvider
from .schemas import HoyaDialogueResponse
from .validator import ValidationFailure, validate_output

log = logging.getLogger(__name__)


def generates_replies(config: Settings) -> bool:
    """외부 제공자가 두두의 답을 만들 설정인지 판단한다. 그런 대화는 미리 녹음한 두두 음성 파일로 말할 수 없으므로,
    아동 화면은 이 값을 보고 인사부터 끝까지 같은 브라우저 음성으로 말한다.
    """
    return (config.hoya_chat_enabled and config.hoya_chat_provider == "openai"
            and bool(config.openai_api_key.get_secret_value().strip()) and bool(config.hoya_chat_model.strip()))


def select_provider(config: Settings) -> tuple[HoyaDialogueProvider | None, str | None]:
    """설정에 맞는 외부 제공자와, 쓸 수 없을 때의 사유(UNSUPPORTED_PROVIDER·NOT_CONFIGURED)를 돌려준다.
    None이면 DemoProvider만 쓴다. 대화 서비스를 만들 때 한 번 부른다.
    """
    if not config.hoya_chat_enabled:
        return None, None
    if config.hoya_chat_provider != "openai":
        return None, "UNSUPPORTED_PROVIDER"
    key = config.openai_api_key.get_secret_value().strip()
    if not key or not config.hoya_chat_model.strip():
        return None, "NOT_CONFIGURED"
    try:
        from .providers.openai_provider import OpenAIProvider
        return OpenAIProvider(key, config.hoya_chat_model.strip(), config.hoya_chat_timeout_sec), None
    except (ProviderError, ImportError):
        return None, "NOT_CONFIGURED"


class HoyaDialogueService:
    """호야 응답 하나를 만드는 서비스. 외부 제공자를 부르고 검증한 뒤, 어떤 실패든 DEMO 응답으로 대체해 아동 화면이 깨지지 않게 한다.
    hoya/api.py의 대화 턴 처리가 사용한다.
    """
    def __init__(self, provider: HoyaDialogueProvider | None = None, unavailable_reason: str | None = None):
        """외부 제공자(없으면 None)와 쓸 수 없는 사유를 저장하고, 대체용 DemoProvider를 준비한다.
        select_provider 결과를 그대로 받아 만든다.
        """
        self.provider = provider
        self.unavailable_reason = unavailable_reason
        self.demo = DemoProvider()

    def _demo(self, context: HoyaDialogueContext, provider: str, reason: str | None) -> HoyaDialogueResponse:
        """DemoProvider로 응답을 만들고 provider 이름과 대체 사유를 붙여 돌려준다.
        정상 DEMO 모드와 실패 대체(DEMO_FALLBACK)가 같은 형식의 응답을 쓰도록 모아 둔 도우미다.
        """
        output = self.demo.reply_sync(context)
        return HoyaDialogueResponse(text=output.text, strategy=output.strategy, target_words=output.target_words,
                                    provider=provider, model=None, fallback_reason=reason)

    def fallback(self, context: HoyaDialogueContext, reason: str) -> HoyaDialogueResponse:
        """외부 제공자를 부르지 않고 DEMO 응답으로 turn을 마무리한다.
        처리 중(PROCESSING)으로 멈춘 예약 turn을 복구할 때처럼 제공자를 다시 부르면 안 될 때 쓴다.
        """
        return self._demo(context, "DEMO_FALLBACK", reason)

    async def reply(self, context: HoyaDialogueContext) -> HoyaDialogueResponse:
        """외부 제공자 응답을 받아 검증하고 돌려준다. 제공자가 없거나 실패·검증 실패·예상 못한 예외가 나면 DEMO 응답으로 대체한다.
        호야 대화 턴 API가 아동 발화마다 부르며, 로그에는 사유 코드만 남긴다.
        """
        if self.provider is None:
            return self._demo(context, "DEMO" if self.unavailable_reason is None else "DEMO_FALLBACK", self.unavailable_reason)
        try:
            output = validate_output(await self.provider.reply(context), context.strategy, context.target_lexicon)
        except ProviderError as error:
            return self._demo(context, "DEMO_FALLBACK", error.reason)
        except ValidationFailure as error:
            log.info("호야 대화 응답이 검증을 통과하지 못해 DEMO 응답을 씁니다: %s", error.reason)
            return self._demo(context, "DEMO_FALLBACK", f"INVALID_{error.reason}")
        except Exception:  # 예상하지 못한 제공자 오류도 아동 화면을 깨지 않는다.
            log.warning("호야 대화 제공자 예외")
            return self._demo(context, "DEMO_FALLBACK", "PROVIDER_ERROR")
        return HoyaDialogueResponse(text=output.text, strategy=output.strategy, target_words=output.target_words,
                                    provider=self.provider.name, model=self.provider.model)
