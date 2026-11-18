"""여러 모듈이 같이 쓰는 훈련 단계(음소~자발화) 열거형을 정의한다.
목표 단계 비교·다음 단계 추천(analysis/recommendation.py)에서 순서를 알기 위해 쓴다.
"""

from enum import StrEnum


class TrainingLevel(StrEnum):
    """말하기 훈련 단계. 음소 → 음절 → 단어 → 짧은 문장 → 문장 → 자발화 순서로 어려워진다.
    LEVEL_ORDER가 이 정의 순서를 그대로 단계 순서로 쓴다.
    """
    phoneme = "phoneme"
    syllable = "syllable"
    word = "word"
    short_sentence = "short_sentence"
    sentence = "sentence"
    spontaneous = "spontaneous"


LEVEL_ORDER = list(TrainingLevel)
