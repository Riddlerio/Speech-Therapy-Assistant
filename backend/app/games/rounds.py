"""대구대 건너기 5라운드 정의(제목·안내 문장·임상 초점·판정 규칙 등)를 담는다.
게임 시작·진행·판정·화면 응답이 모두 이 표(GAME_ROUNDS)를 기준으로 동작한다.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RoundDefinition:
    """라운드 하나의 고정 정의. 아동 화면에 보일 문구, 치료사용 임상 정보, 판정 규칙(rule)과 목표 시간(target_ms)을 가진다.
    frozen이라 바꿀 수 없고, 바꾼 정의가 필요하면 dataclasses.replace로 복사본을 만든다.
    """
    id: str
    index: int
    child_title: str
    child_prompt: str
    clinical_focus: str
    task_type: str
    generalization_level: str
    elicitation_type: str
    prompt_type: str
    attempts: int
    time_limit_sec: int
    rule: str
    target_ms: int = 0
    # 모든 라운드 항목은 기존 훈련 단어 목록에서 고른다. 미훈련(novel) 단어를 보장하지 않는다.
    item_source: str = "TRAINING_BANK"
    # 발화 끝 판단 전 기다리는 시간.
    end_hold_ms: int = 700


def _round(game, index, title, prompt, focus, task, level, elicitation, prompt_type, attempts, rule):
    """GAME_ROUNDS 표를 짧게 쓰기 위한 RoundDefinition 생성 도우미. id를 "게임.r번호"로 만들고 제한 시간 60초를 채운다.
    이 모듈 안에서 라운드 표를 정의할 때만 쓴다.
    """
    return RoundDefinition(f"{game}.r{index}", index, title, prompt, focus, task, level,
                           elicitation, prompt_type, attempts, 60, rule)


GAME_ROUNDS = {
    "daegu_crossing": (
        _round("daegu_crossing", 1, "두두 따라 건너기", "두두를 따라 말하고 건너자!", "모델에 따른 /ㅅ/ 산출 · 음향 근사", "TARGET_PRODUCTION", "SYLLABLE", "DIRECT_IMITATION", "AUDITORY_MODEL", 3, "ONSET_FRICATION"),
        _round("daegu_crossing", 2, "혼자 건너기", "이번에는 혼자 말해 볼까?", "독립 산출 · 음향 근사", "TARGET_PRODUCTION", "SYLLABLE", "PROMPTED_PRODUCTION", "VISUAL", 3, "ONSET_FRICATION"),
        _round("daegu_crossing", 3, "모음 바꿔 건너기", "다른 소리로 한 줄씩 건너자!", "모음 환경 전이 · 음향 근사", "TARGET_PRODUCTION", "SYLLABLE", "PROMPTED_PRODUCTION", "VISUAL", 3, "ONSET_FRICATION"),
        _round("daegu_crossing", 4, "낱말 건너기", "그림의 이름을 말해 줘!", "낱말 수준 산출 · 음향 근사", "TARGET_PRODUCTION", "WORD", "SELF_GENERATED", "PICTURE_PROMPT", 3, "ONSET_FRICATION"),
        _round("daegu_crossing", 5, "정문까지", "정문까지 한 줄씩 가 보자!", "음절·낱말 혼합 · 음향 근사", "TARGET_PRODUCTION", "WORD", "PROMPTED_PRODUCTION", "PICTURE_PROMPT", 3, "ONSET_FRICATION"),
    ),
}


def public_round(round_def: RoundDefinition) -> dict:
    """라운드 정의를 JSON 형식(camelCase)으로 바꾼다.
    계획 단계 기록과 관찰 스냅숏(games/crossing.py)이 라운드 정보를 저장할 때 쓴다.
    """
    return {"index": round_def.index, "id": round_def.id, "childTitle": round_def.child_title,
            "childPrompt": round_def.child_prompt, "clinicalFocus": round_def.clinical_focus,
            "generalizationLevel": round_def.generalization_level, "elicitationType": round_def.elicitation_type,
            "attempts": round_def.attempts, "targetMs": round_def.target_ms,
            "itemSource": round_def.item_source, "endHoldMs": round_def.end_hold_ms}
