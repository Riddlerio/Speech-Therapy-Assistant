"""호야 대화를 언제 끝내고 게임(대구대 건너기)으로 안내할지 정하는 제품 규칙.
목표 관찰 횟수는 발음 정확도가 아니며, 대화 시간·무발화 횟수와 함께 전환 시점만 정한다.
"""
from .schemas import SpeechEvidence

TARGET_ATTEMPTS = 10
MIN_CONVERSATION_SECONDS = 120
MAX_CONVERSATION_SECONDS = 300
EASY_QUESTION_NO_SPEECH_TURNS = 3
TRANSITION_NO_SPEECH_TURNS = 4
NEXT_ACTIVITY = "daegu_crossing"
TRANSITION_TEXT = "우리 게임 해 볼까? 아래 '대구대 건너기'를 눌러 볼래?"


def consecutive_no_speech(evidence: list[SpeechEvidence]) -> int:
    """근거 목록 끝에서부터 무발화(NO_SPEECH)가 몇 번 연속인지 센다.
    대화 정책의 "쉬운 질문" 전환과 게임 안내 전환 판단에 쓴다.
    """
    count = 0
    for value in reversed(evidence):
        if value != "NO_SPEECH":
            break
        count += 1
    return count


def should_transition(evidence: list[SpeechEvidence], elapsed_seconds: float) -> bool:
    """목표 관찰 10회 이상이면서 2분 이상 대화했거나, 5분이 지났거나, 무발화가 4번 연속이면 게임으로 안내해야 한다고 판단한다.
    현재 턴까지의 서버 근거와 서버 시각만 쓰며, 불확실은 무발화로 세지 않는다.
    """
    return (
        (evidence.count("TARGET_OBSERVED") >= TARGET_ATTEMPTS and elapsed_seconds >= MIN_CONVERSATION_SECONDS)
        or elapsed_seconds >= MAX_CONVERSATION_SECONDS
        or consecutive_no_speech(evidence) >= TRANSITION_NO_SPEECH_TURNS
    )
