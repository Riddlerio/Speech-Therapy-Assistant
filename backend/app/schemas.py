"""main.py와 therapist_api.py 라우트가 받는 요청 본문 모델.
입력 검증(길이·허용값·모르는 필드 거부)을 라우트 코드 밖 한곳에 모은다."""
from typing import Literal

from pydantic import Field, field_validator
from .schemas_base import ApiModel, StrictApiModel
from .pronunciation.acoustic import AcousticSummary


class LoginInput(ApiModel):
    """아이디·비밀번호 로그인 요청.
    /api/auth/login에서 쓰며 길이 상한으로 과도한 입력을 막는다."""
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class DemoLoginInput(StrictApiModel):
    """DEMO 샘플 계정 로그인 요청(역할만 받는다).
    DEMO 모드의 /api/auth/demo-login에서 비밀번호 없이 쓴다."""
    role: Literal["THERAPIST", "STUDENT"]


class GoalInput(ApiModel):
    """치료사가 정하는 훈련 목표 값과 기본값.
    목표 생성 때 필드 목록과 기본값의 기준으로도 쓴다."""
    target_phoneme: Literal["ㅅ", "ㅈ", "ㄹ"] = "ㅅ"
    target_sound: str = "사"
    word_position: Literal["initial", "medial", "final"] = "initial"
    level: Literal["syllable", "word", "short_sentence"] = "word"
    min_level: Literal["phoneme", "syllable", "word", "short_sentence"] = "syllable"
    session_duration_min: Literal[5, 10, 15] = 10
    repetition_target: int = Field(default=30, ge=10, le=60)
    priority: Literal["accuracy", "balanced", "speed"] = "accuracy"
    preferred_cue: Literal["none", "visual_mouth", "auditory_model", "tactile_description"] = "visual_mouth"
    excluded_words: list[str] = Field(default_factory=list)
    excluded_games: list[str] = Field(default_factory=list)
    priority_targets: list[str] = Field(default_factory=list)
    pass_threshold: int | None = None
    note: str = ""


class StartActivityInput(ApiModel):
    """아동이 고른 게임(대구대 건너기만 지원)과 모드(real/demo).
    /api/activities로 새 5라운드 활동을 시작할 때 쓴다."""
    game: Literal["daegu_crossing"]
    mode: Literal["real", "demo"] = "demo"


class ChildInput(ApiModel):
    """치료사가 새 아동을 등록할 때 보내는 값.
    /api/children POST에서 쓰며 보호자 동의 여부를 함께 받는다."""
    hero_name: str = Field(min_length=1, max_length=20)
    age_band: Literal["4-5", "6-7"] = "4-5"
    guardian_consent: bool = False


class UtteranceInput(StrictApiModel):
    """한 번의 발화 시도(인식 문장·음향 요약·라운드 위치).
    활동 중 /api/activities/{id}/utterances에서 쓰며 모르는 필드는 거부한다."""
    item_id: str
    round_index: int | None = Field(default=None, ge=1, le=5)
    attempt_index: int = Field(default=1, ge=0, le=100)
    transcript: str | None = Field(default=None, max_length=50)
    alternatives: list[str] = Field(default_factory=list, max_length=5)
    recognizer: str = "demo_script"
    acoustic: AcousticSummary = Field(default_factory=AcousticSummary)
    elapsed_sec: int = Field(default=0, ge=0, le=600)

    @field_validator("alternatives")
    @classmethod
    def limit_alternatives(cls, values: list[str]) -> list[str]:
        """대체 인식 결과 각각이 50자를 넘지 않는지 검사한다.
        목록 길이 제한만으로는 항목 하나의 길이를 막지 못해서 둔다."""
        if any(len(value) > 50 for value in values):
            raise ValueError("대체 인식 결과는 50자 이하여야 합니다")
        return values


class CompleteInput(ApiModel):
    """활동 완료 요청(브라우저가 잰 경과 시간).
    /api/play/sessions/{id}/complete에서 쓰며 보호된 V2 회기는 서버 활동 시간으로 대신한다."""
    elapsed_sec: int = 0


class FeedbackInput(ApiModel):
    """발화 하나에 대한 치료사 교정·메모.
    /api/utterances/{id}/feedback에서 쓰며 동작에 따라 아동별 규칙이 생길 수 있다."""
    action: str
    cue: str | None = None
    score: int | None = None
    note: str = ""
    create_rule: bool = False


class ObservationDecisionInput(StrictApiModel):
    """임상 관찰에 대한 치료사 확인·교정·거부.
    /api/observations/{id}/decision에서 쓰며 모르는 필드는 거부한다."""
    action: Literal["confirm", "correct", "reject"]
    corrected_result: Literal["success", "retry", "uncertain", "no_speech"] | None = None
    note: str = Field(default="", max_length=500)
