"""LLM 제공자가 만든 호야 응답이 안전한지 검사한다(길이, 전략 일치, 금지 표현, 지시문 노출 등).
안전을 system prompt 하나에만 맡기지 않으려고 있으며, 통과하지 못하면 서비스가 DemoProvider 응답으로 대체한다.
"""
import re

from .prompt.prompt_builder import system_prompt
from .schemas import MAX_HOYA_TEXT_CHARS, ProviderOutput, Strategy

# 평가·교정, 진단·효과 단정, 임의 치료 기법, 개인정보 요청, 지시문 노출에 해당하는 표현.
BLOCKED_PATTERNS = [
    (r"틀렸|잘못\s*(말|발음)|발음이\s*(이상|나빠|틀)|제대로\s*(말|발음)|다시\s*정확", "CORRECTION"),
    (r"진단|장애|치료\s*효과|나아졌|완치|정상\s*발음|발음\s*점수", "CLINICAL_CLAIM"),
    (r"혀|이빨|치아|잇몸|입술을|입\s*모양|입\s*운동|숨을|호흡|배에\s*힘|목을\s*만져|손을\s*대", "CLINICAL_CUE"),
    (r"주소|전화\s*번호|핸드폰|비밀\s*번호|아이디|계정|어느\s*학교|학교\s*이름|어디\s*살|사진", "PERSONAL_INFO"),
    (r"시스템\s*프롬프트|system\s*prompt|지시문|developer|규칙을\s*무시|instruction", "PROMPT_DISCLOSURE"),
    (r"바보|멍청|싫어해|혼나|못해|한심", "INAPPROPRIATE"),
]


class ValidationFailure(Exception):
    """응답 검증 실패를 알리는 예외. reason에 실패 종류(예: CORRECTION, TEXT_TOO_LONG)를 담는다.
    hoya/service.py가 이 예외를 잡아 DEMO 응답으로 바꾸고 사유를 기록한다.
    """
    def __init__(self, reason: str):
        """실패 사유 코드를 예외 메시지와 reason 속성에 함께 저장한다.
        로그·응답에 아동 발화 대신 짧은 사유 코드만 남기기 위해서다.
        """
        super().__init__(reason)
        self.reason = reason


def _prompt_fragments() -> list[str]:
    """system prompt의 각 줄에서 공백을 뺀 앞 24글자 조각 목록을 만든다.
    응답에 지시문 일부가 그대로 섞여 나왔는지(프롬프트 노출) 찾는 데 쓴다.
    """
    lines = [re.sub(r"\s+", "", line.strip("-[] ")) for line in system_prompt().splitlines()]
    return [line[:24] for line in lines if len(line) >= 24]


def validate_output(output: ProviderOutput, strategy: Strategy, lexicon: list[str]) -> ProviderOutput:
    """제공자 응답을 검사해 통과하면 앞뒤 공백을 정리한 응답을 돌려주고, 아니면 ValidationFailure를 던진다.
    빈 문장·너무 긴 문장·다른 전략·목표 어휘 밖 낱말·질문 2개 이상·교정/진단/개인정보 표현·지시문 노출을 막는다.
    """
    text = (output.text or "").strip()
    if not text:
        raise ValidationFailure("EMPTY_TEXT")
    if len(text) > MAX_HOYA_TEXT_CHARS:
        raise ValidationFailure("TEXT_TOO_LONG")
    if output.strategy != strategy:
        raise ValidationFailure("STRATEGY_MISMATCH")
    if any(word not in lexicon for word in output.target_words):
        raise ValidationFailure("TARGET_OUTSIDE_LEXICON")
    if text.count("?") > 1:
        raise ValidationFailure("MULTIPLE_QUESTIONS")
    for pattern, reason in BLOCKED_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            raise ValidationFailure(reason)
    compact = re.sub(r"\s+", "", text)
    if any(fragment in compact for fragment in _prompt_fragments()):
        raise ValidationFailure("PROMPT_DISCLOSURE")
    return output.model_copy(update={"text": text})
