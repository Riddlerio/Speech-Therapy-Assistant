"""호야 대화의 요청·응답·제공자(LLM/DEMO) 출력 schema.
hoya/api.py 입력 검증과 제공자 출력 검증 기준을 한곳에 둔다."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from ..pronunciation.acoustic import AcousticSummary
from ..schemas_base import ApiModel, StrictApiModel


# 자유대화 근거 상태. 정해진 목표 단어가 없으므로 정오(success/failure)를 판정하지 않는다.
SpeechEvidence = Literal["TARGET_OBSERVED", "TARGET_NOT_OBSERVED", "UNCERTAIN", "NO_SPEECH"]
# 백엔드 정책이 정한 다음 대화 전략. LLM은 이 값을 바꿀 수 없다.
Strategy = Literal["CONTINUE_OR_EXPAND", "NATURAL_REELICITATION", "WAIT_OR_SIMPLIFY", "SIMPLIFY", "ALLOWED_CUE"]

MAX_TRANSCRIPT_CHARS = 80
MAX_HOYA_TEXT_CHARS = 120
RECENT_TURNS = 5


class HoyaChatStartInput(StrictApiModel):
    """호야 대화 시작 요청(real/demo 모드).
    대화 세션을 새로 열 때 쓰며 모르는 필드는 거부한다."""
    mode: Literal["real", "demo"] = "demo"


class HoyaChatTurnInput(StrictApiModel):
    """아동 발화 한 턴(턴 번호·재시도 id·인식 문장·음향 요약).
    대화 턴 라우트에서 쓰며 같은 발화의 재시도를 client_request_id로 알아본다."""
    turn_index: StrictInt = Field(ge=1, le=1000)
    # 발화 1회마다 브라우저가 새로 만드는 임의 값(UUID 등). 같은 발화의 재시도는 같은 값을 쓴다.
    client_request_id: str = Field(pattern=r"^[A-Za-z0-9-]{16,64}$")
    transcript: str | None = Field(default=None, max_length=MAX_TRANSCRIPT_CHARS)
    alternatives: list[str] = Field(default_factory=list, max_length=5)
    recognizer: Literal["web_speech", "demo_script"] = "demo_script"
    acoustic: AcousticSummary = Field(default_factory=AcousticSummary)

    @field_validator("alternatives")
    @classmethod
    def limit_alternatives(cls, values: list[str]) -> list[str]:
        """대체 인식 결과 각각이 MAX_TRANSCRIPT_CHARS를 넘지 않는지 검사한다.
        목록 길이 제한만으로는 항목 하나의 길이를 막지 못해서 둔다."""
        if any(len(value) > MAX_TRANSCRIPT_CHARS for value in values):
            raise ValueError("대체 인식 결과가 너무 깁니다")
        return values


class ProviderOutput(BaseModel):
    """대화 제공자(LLM 또는 DEMO)가 돌려주는 값. 검증기를 통과해야 아동에게 전달된다.
    모르는 필드는 거부한다. 호야의 표정·동작(THINKING·TALKING·LISTENING)은 브라우저의 대화 상태가 정하므로 제공자가 정하지 않는다."""
    model_config = ConfigDict(extra="forbid")
    text: str
    strategy: Strategy
    target_words: list[str] = Field(default_factory=list, max_length=6)


class HoyaDialogueResponse(ApiModel):
    """검증을 마친 호야 답변을 브라우저에 돌려주는 응답 모델.
    어떤 제공자·모델이 답했는지와 대체 응답 이유를 함께 알려 준다."""
    text: str
    strategy: Strategy
    target_words: list[str]
    provider: str
    model: str | None = None
    fallback_reason: str | None = None
