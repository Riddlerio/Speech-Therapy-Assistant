"""호야와 자유대화 중 아동 발화 하나를 기존 Speech Engine 규칙으로 요약해 음성 근거(TARGET_OBSERVED 등)로 바꾼다.
발음 정오를 판정하지 않으며, 이 근거로 대화 정책(policy.py)이 호야의 다음 전략을 정한다.
"""
from ..games.evaluation import evaluate_round
from ..games.rounds import RoundDefinition
from ..speech.pipeline import acoustic_number
from .schemas import SpeechEvidence

# 자발 대화용 CONVERSATION 규칙(음질 게이트 + 목표 음소 검출). 판정은 rule만 읽는다.
_CONVERSATION_ROUND = RoundDefinition("hoya.conversation", 1, "두두와 대화", "", "자유 대화 중 자발 산출",
                                      "CONTEXTUAL_PRODUCTION", "SPONTANEOUS", "SPONTANEOUS", "OPEN_QUESTION",
                                      2, 90, "CONVERSATION")
_MAP = {"target_observed": "TARGET_OBSERVED", "not_target_attempt": "TARGET_NOT_OBSERVED",
        "uncertain": "UNCERTAIN", "no_speech": "NO_SPEECH"}


def classify_speech(transcript: str | None, acoustic: dict, goal) -> SpeechEvidence:
    """CONVERSATION 규칙(음질 게이트 + 목표 음소 검출)으로 발화를 평가해 근거 값 하나를 돌려준다.
    마이크가 소리를 들었는데 인식 문장이 없으면 무발화가 아니라 불확실(UNCERTAIN)로 본다. 호야 대화 턴 API가 부른다.
    """
    result = evaluate_round(_CONVERSATION_ROUND, {}, transcript, acoustic, goal).result
    # 마이크가 소리를 들었는데 인식 문장이 없으면(ASR 실패·미지원) 무발화가 아니라 불확실이다.
    if result == "no_speech" and acoustic.get("source") == "microphone" \
            and acoustic_number(acoustic, "activeMs", acoustic_number(acoustic, "voicedMs")) > 0:
        return "UNCERTAIN"
    return _MAP.get(result, "UNCERTAIN")
