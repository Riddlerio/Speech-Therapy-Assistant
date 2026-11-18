"""OpenAI Responses API로 호야 응답을 만드는 제공자. 텍스트 생성 전용이며 tools를 넘기지 않는다.
모델 이름은 HOYA_CHAT_MODEL로만 정한다. key·프롬프트·아동 발화는 로그에 남기지 않는다.
"""
import asyncio
import logging
from functools import lru_cache
from typing import Literal

from pydantic import create_model

from ..prompt.prompt_builder import HoyaDialogueContext, build_messages, system_prompt
from ..schemas import ProviderOutput, Strategy
from .base import ProviderError

log = logging.getLogger(__name__)


@lru_cache
def output_format(strategy: Strategy) -> type[ProviderOutput]:
    """출력 schema의 strategy를 서버가 정한 값 하나로 묶은 응답 모델을 만든다(전략별로 캐시).
    모델이 다른 전략 이름을 적어 검증에서 걸러지는 일을 막는다.
    """
    return create_model(f"DuduReply{strategy.title().replace('_', '')}", __base__=ProviderOutput,
                        strategy=(Literal[strategy], ...))


class OpenAIProvider:
    """OpenAI 모델을 부르는 호야 대화 제공자. 정해진 시간 안에 구조화된 응답(ProviderOutput)을 받아 온다.
    hoya/service.py의 select_provider가 설정이 갖춰졌을 때 만든다.
    """
    name = "OPENAI"

    def __init__(self, api_key: str, model: str, timeout_sec: float = 8.0, client=None):
        """API key·모델 이름·제한 시간을 저장하고 OpenAI 클라이언트를 만든다. key나 모델이 없으면 ProviderError다.
        재시도하지 않게 설정해, 실패하면 서비스가 곧바로 DemoProvider로 대체하게 한다. 테스트는 client를 직접 넘길 수 있다.
        """
        if not api_key or not model:
            raise ProviderError("NOT_CONFIGURED")
        self.model = model
        self.timeout_sec = timeout_sec
        if client is None:
            from openai import AsyncOpenAI
            # 재시도하지 않는다. 실패하면 서비스가 곧바로 DemoProvider로 대체한다.
            client = AsyncOpenAI(api_key=api_key, timeout=timeout_sec, max_retries=0)
        self._client = client

    async def reply(self, context: HoyaDialogueContext) -> ProviderOutput:
        """system prompt와 메시지로 모델을 호출하고 파싱된 응답을 돌려준다(저장 안 함, 최대 400토큰).
        시간 초과·SDK 오류·빈 응답은 사유 코드만 담은 ProviderError로 바꿔 던진다.
        """
        try:
            response = await asyncio.wait_for(self._client.responses.parse(
                model=self.model, instructions=system_prompt(), input=build_messages(context),
                text_format=output_format(context.strategy), max_output_tokens=400, store=False,
            ), timeout=self.timeout_sec)
        except asyncio.TimeoutError as error:
            raise ProviderError("TIMEOUT") from error
        except Exception as error:  # SDK 예외(연결·상태 코드·응답 형식)의 종류만 남긴다.
            reason = _reason(error)
            log.warning("호야 대화 제공자 호출 실패: %s", reason)
            raise ProviderError(reason) from error
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise ProviderError("EMPTY_RESPONSE")
        return parsed if isinstance(parsed, ProviderOutput) else ProviderOutput.model_validate(parsed)


def _reason(error: Exception) -> str:
    """OpenAI SDK 예외를 짧은 사유 코드(TIMEOUT, HTTP_상태코드, CONNECTION, INVALID_OUTPUT, PROVIDER_ERROR)로 바꾼다.
    오류 내용에 섞일 수 있는 민감한 정보 대신 종류만 로그와 응답에 남기려고 있다.
    """
    try:
        import openai
    except ImportError:  # pragma: no cover
        return "PROVIDER_ERROR"
    if isinstance(error, openai.APITimeoutError):
        return "TIMEOUT"
    if isinstance(error, openai.APIStatusError):
        return f"HTTP_{error.status_code}"
    if isinstance(error, openai.APIConnectionError):
        return "CONNECTION"
    if isinstance(error, ValueError):  # JSON·schema 검증 실패(pydantic ValidationError 포함)
        return "INVALID_OUTPUT"
    return "PROVIDER_ERROR"
