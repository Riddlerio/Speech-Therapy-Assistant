"""치료사 회기 계획 form과 요약 AI 출력의 schema.
계획 입력 검증 규칙(단계 순서·단어 중복·활동 유형)을 라우트 밖 한곳에 둔다."""
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..schemas_base import ApiModel, StrictApiModel

StartLevel = Literal["phoneme", "syllable", "word", "short_sentence"]
TargetLevel = Literal["syllable", "word", "short_sentence"]
# 새 계획은 daegu_crossing만 쓴다. 이전 게임 이름은 저장된 옛 계획을 읽기·복제·수정할 때 400이 나지 않게 계속 받는다.
LEGACY_GAME_ACTIVITIES = {"magic_beam", "sky_climb", "monster_adventure", "conversation_quest"}
GAME_ACTIVITIES = {"daegu_crossing"} | LEGACY_GAME_ACTIVITIES
STEP_ACTIVITIES = {"CONVERSATION": {"hoya_conversation"}, "GAME": GAME_ACTIVITIES, "PRACTICE": GAME_ACTIVITIES}
LEVELS = ("phoneme", "syllable", "word", "short_sentence")


class StepParameters(StrictApiModel):
    """계획 단계 하나의 반복 횟수·시간 매개변수.
    StepInput 안에서 쓰며 범위를 벗어난 값은 거부한다."""
    trials: int | None = Field(default=None, ge=1, le=60)
    duration_min: int | None = Field(default=None, ge=1, le=15)


class StepInput(StrictApiModel):
    """회기 계획의 단계 하나(대화·게임·연습 중 무엇을 할지).
    SessionPlanInput.steps의 항목이며 활동이 단계 유형과 맞는지 검사한다."""
    step_type: Literal["CONVERSATION", "GAME", "PRACTICE"]
    activity: Literal["hoya_conversation", "daegu_crossing", "magic_beam", "sky_climb", "monster_adventure", "conversation_quest"]
    target_level: StartLevel | None = None
    parameters: StepParameters = Field(default_factory=StepParameters)

    @model_validator(mode="after")
    def activity_matches_type(self):
        """활동이 단계 유형에서 허용된 것인지 확인한다.
        예: CONVERSATION 단계에 게임 활동이 들어오는 것을 막는다."""
        if self.activity not in STEP_ACTIVITIES[self.step_type]:
            raise ValueError("활동 유형과 활동이 맞지 않습니다")
        return self


def _clean_words(values: list[str]) -> list[str]:
    """단어 목록의 앞뒤 공백을 지우고 중복을 빼며 1–20자인지 검사한다.
    우선 단어·제외 단어 검증에서 함께 쓴다."""
    words = []
    for value in values:
        word = value.strip()
        if not word or len(word) > 20:
            raise ValueError("단어는 1–20자여야 합니다")
        if word not in words:
            words.append(word)
    return words


class SessionPlanInput(StrictApiModel):
    """치료사가 작성하는 구조화 회기 계획 form.
    자유 입력은 therapist_note 하나뿐이며 단계·단어 규칙을 함께 검사한다."""
    target_phoneme: Literal["ㅅ", "ㅈ", "ㄹ"]
    word_position: Literal["initial", "medial", "final"]
    start_level: StartLevel
    target_level: TargetLevel
    duration_min: Literal[5, 10, 15]
    repetition_target: int = Field(ge=10, le=60)
    preferred_cue: Literal["none", "visual_mouth", "auditory_model", "tactile_description"]
    priority_targets: list[str] = Field(default_factory=list, max_length=20)
    excluded_words: list[str] = Field(default_factory=list, max_length=20)
    conversation_theme: str = Field(default="", max_length=100)
    therapist_note: str = Field(default="", max_length=500)
    steps: list[StepInput] = Field(min_length=1, max_length=8)

    @field_validator("priority_targets", "excluded_words")
    @classmethod
    def clean_words(cls, values: list[str]) -> list[str]:
        """우선 단어·제외 단어 목록을 _clean_words로 정리한다.
        두 필드에 같은 규칙을 적용하려고 둔다."""
        return _clean_words(values)

    @model_validator(mode="after")
    def levels_in_order(self):
        """시작≤목표 단계, 우선·제외 단어 겹침 없음, 각 단계≤목표 단계를 확인한다.
        필드 여러 개를 함께 봐야 하는 규칙이라 모델 검증으로 둔다."""
        if LEVELS.index(self.start_level) > LEVELS.index(self.target_level):
            raise ValueError("시작 단계가 목표 단계보다 높습니다")
        if set(self.priority_targets) & set(self.excluded_words):
            raise ValueError("우선 단어와 제외 단어가 겹칩니다")
        if any(step.target_level and LEVELS.index(step.target_level) > LEVELS.index(self.target_level) for step in self.steps):
            raise ValueError("활동 단계가 목표 단계보다 높습니다")
        return self


class SummaryOutput(ApiModel):
    """요약 AI의 structured output. 관찰 사실(evidencePoints)과 제안(nextSessionSuggestion)을 나눈다.

    길이·표현 제한은 제공자 schema 호환을 위해 여기 두지 않고 ValidateOutput 단계에서 검사한다.
    """
    observation_summary: str
    evidence_points: list[str]
    next_session_suggestion: str
    limitations: list[str]
