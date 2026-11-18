"""발화 하나를 판정하는 Speech Engine: 인식 문장 정규화 → 간단한 G2P 음소 변환 → 편집 거리 음소 정렬 → 점수·결과 판정 순으로 이어진다.
흐름: 아동 화면 → 발화 API → (여기) 판정 → 관찰 → 치료사 확인. 매직빔은 음향 수치로 판정하고, 치료사 규칙(대치·변이 허용)도 여기서 반영한다.
"""

import re
import unicodedata
from dataclasses import asdict, dataclass
from math import isfinite

from ..pronunciation.audio_quality import assess_audio_quality


def normalize(text: str | None) -> str | None:
    """유니코드 NFC로 맞춘 뒤 한글 음절(가~힣)만 남긴다. 남는 글자가 없으면 None이다.
    None을 돌려주면 판정에서는 무발화로 처리된다.
    """
    value = re.sub(r"[^가-힣]", "", unicodedata.normalize("NFC", text or ""))
    return value or None


INITIAL = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
MEDIAL = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
FINAL = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]
NEUTRAL = {"ㄲ": "ㄱ", "ㅋ": "ㄱ", "ㄳ": "ㄱ", "ㄺ": "ㄱ", "ㅅ": "ㄷ", "ㅆ": "ㄷ", "ㅈ": "ㄷ", "ㅊ": "ㄷ", "ㅌ": "ㄷ", "ㅎ": "ㄷ", "ㅍ": "ㅂ", "ㅄ": "ㅂ", "ㄵ": "ㄴ", "ㄶ": "ㄴ", "ㄻ": "ㅁ", "ㄼ": "ㄹ", "ㄽ": "ㄹ", "ㄾ": "ㄹ", "ㄿ": "ㅂ", "ㅀ": "ㄹ"}
TENSE = {"ㄱ": "ㄲ", "ㄷ": "ㄸ", "ㅂ": "ㅃ", "ㅅ": "ㅆ", "ㅈ": "ㅉ"}
CLUSTERS = {"ㄳ": ("ㄱ", "ㅅ"), "ㄵ": ("ㄴ", "ㅈ"), "ㄺ": ("ㄹ", "ㄱ"), "ㄻ": ("ㄹ", "ㅁ"), "ㄼ": ("ㄹ", "ㅂ"), "ㄽ": ("ㄹ", "ㅅ"), "ㄾ": ("ㄹ", "ㅌ"), "ㄿ": ("ㄹ", "ㅍ"), "ㅄ": ("ㅂ", "ㅅ")}


@dataclass(frozen=True)
class Phone:
    """음소 하나. 음절 안 자리(I 초성·M 중성·F 종성), 기호, 몇 번째 음절인지를 담는다."""
    slot: str
    symbol: str
    syllable_index: int


class SimpleKoreanG2P:
    """간단한 한국어 발음 변환기. 연음, 종성 대표음, 경음화 같은 기본 규칙만 적용한다."""
    def to_phonemes(self, text: str) -> list[Phone]:
        """문장을 음절로 나누고 연음·종성 중화·경음화를 적용한 뒤 Phone 목록으로 돌려준다. 한글이 아닌 글자는 건너뛴다.
        "사과"처럼 쓰인 글자가 아니라 실제 발음에 가까운 음소로 비교하려고 쓴다.
        """
        syllables = []
        for char in text:
            code = ord(char) - 0xAC00
            if 0 <= code < 11172:
                syllables.append([INITIAL[code // 588], MEDIAL[(code % 588) // 28], FINAL[code % 28]])
        for i in range(len(syllables) - 1):
            current, following = syllables[i], syllables[i + 1]
            if current[2] and current[2] != "ㅇ" and following[0] == "ㅇ":
                if current[2] in CLUSTERS:
                    current[2], following[0] = CLUSTERS[current[2]]
                else:
                    following[0] = current[2] if current[2] != "ㅎ" else "ㅇ"
                    current[2] = ""
        for syllable in syllables:
            syllable[2] = NEUTRAL.get(syllable[2], syllable[2])
        for i in range(len(syllables) - 1):
            if syllables[i][2] in ("ㄱ", "ㄷ", "ㅂ"):
                syllables[i + 1][0] = TENSE.get(syllables[i + 1][0], syllables[i + 1][0])
        result = []
        for i, (initial, medial, final) in enumerate(syllables):
            result.extend([Phone("I", initial if initial != "ㅇ" else "ㅇ0", i), Phone("M", medial, i)])
            if final:
                result.append(Phone("F", final, i))
        return result


SIMILAR = {frozenset(p) for p in [("ㅅ", "ㅆ"), ("ㅈ", "ㅉ"), ("ㄱ", "ㄲ"), ("ㄷ", "ㄸ"), ("ㅂ", "ㅃ")]}


@dataclass
class Operation:
    """정렬 결과 한 단계. 종류(match·sub·del·ins), 목표·인식 쪽 위치, 비용을 담는다."""
    kind: str
    target_index: int | None
    observed_index: int | None
    cost: float


def align(target: list[Phone], observed: list[Phone]) -> list[Operation]:
    """동적 계획법으로 두 음소열을 정렬해 Operation 목록을 돌려준다. 같은 자리(초성·중성·종성)끼리만 짝짓고, 비슷한 소리(ㅅ/ㅆ 등)는 비용 0.5로 본다.
    분석 결과의 정렬 기록과 대치 소리(substitution) 계산에 쓴다.
    """
    n, m = len(target), len(observed)
    dp = [[float("inf")] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0
    for i in range(n + 1):
        for j in range(m + 1):
            if i and dp[i - 1][j] + 1 < dp[i][j]:
                dp[i][j], back[i][j] = dp[i - 1][j] + 1, Operation("del", i - 1, None, 1)
            if j and dp[i][j - 1] + 1 < dp[i][j]:
                dp[i][j], back[i][j] = dp[i][j - 1] + 1, Operation("ins", None, j - 1, 1)
            if i and j and target[i - 1].slot == observed[j - 1].slot:
                a, b = target[i - 1].symbol, observed[j - 1].symbol
                cost = 0 if a == b else 0.5 if frozenset((a, b)) in SIMILAR else 1
                if dp[i - 1][j - 1] + cost <= dp[i][j]:
                    dp[i][j], back[i][j] = dp[i - 1][j - 1] + cost, Operation("match" if cost == 0 else "sub", i - 1, j - 1, cost)
    ops = []
    i, j = n, m
    while i or j:
        op = back[i][j]
        if op is None:
            raise ValueError("alignment failed")
        ops.append(op)
        i -= op.target_index is not None
        j -= op.observed_index is not None
    return list(reversed(ops))


@dataclass
class AnalysisResult:
    """판정 결과 묶음. 점수, 결과(success·retry 등), 목표 소리 상태, 대치 소리, 오류 유형 태그, 음소·정렬 기록, 적용된 치료사 규칙 id를 담는다."""
    score: int
    result: str
    target_status: str
    substitute_symbol: str | None
    pattern_tags: list[str]
    target_phones: list[dict]
    observed_phones: list[dict]
    alignment: list[dict]
    rule_applied_id: str | None = None


def acoustic_number(acoustic: dict, key: str, default: float = 0) -> float:
    """음향 측정값 dict에서 key 값을 실수로 꺼내되, 없거나 정상 숫자가 아니면 기본값을 돌려준다.
    측정값이 빠지거나 이상해도 판정 계산이 깨지지 않게 여러 판정 규칙에서 쓴다.
    """
    value = acoustic.get(key, default)
    return float(value) if type(value) in (int, float) and isfinite(value) else default


def analyze(item: dict, transcript: str | None, acoustic: dict, goal, rules=()) -> AnalysisResult:
    """매직빔은 최장 발성·마찰 비율로, 단어 문항은 목표·인식 음소 정렬로 점수와 결과를 정한다. 음질이 나쁘면 uncertain으로 보류한다.
    대치 유형(파열음화·파찰음화·경음화 등)을 태그로 남기고, 치료사가 만든 대치·단어 변이 허용 규칙이 맞으면 성공으로 바꾼다.
    """
    if item.get("game") == "magic_beam":
        target = item.get("beamTargetMs", 1500)
        if acoustic.get("source") == "keyboard":
            # 명시적 DEMO 입력은 실제 음향 근거로 해석하지 않는다.
            run_ms = acoustic_number(acoustic, "bestRunMs", acoustic_number(acoustic, "voicedMs"))
            passed = run_ms >= 0.8 * target
            return AnalysisResult(min(100, round(run_ms / target * 100)), "success" if passed else "retry", "correct" if passed else "omitted", None, ["DEMO_INPUT"], [], [], [])
        active_ms = acoustic_number(acoustic, "activeMs", acoustic_number(acoustic, "voicedMs"))
        if active_ms <= 0:
            return AnalysisResult(0, "no_speech", "unknown", None, ["NO_SPEECH"], [], [], [])
        quality = assess_audio_quality(acoustic.get("noiseFloorDb"), acoustic.get("meanRmsDb"),
                                       acoustic.get("durationMs"), acoustic.get("clippingRatio"), acoustic.get("snrDb"))
        if quality["level"] == "POOR":
            return AnalysisResult(0, "uncertain", "unknown", None, ["POOR_AUDIO"], [], [], [])
        if quality["level"] == "UNKNOWN":
            return AnalysisResult(0, "uncertain", "unknown", None, ["NO_ACOUSTIC_EVIDENCE"], [], [], [])
        run_ms = acoustic_number(acoustic, "bestRunMs")
        frication_ms = acoustic_number(acoustic, "fricationMs")
        passed = run_ms >= 0.8 * target and frication_ms >= 0.6 * active_ms
        tags = ["VOICED_NOT_FRICATIVE"] if not passed and active_ms >= 0.8 * target and frication_ms < 0.6 * active_ms else []
        return AnalysisResult(min(100, round(run_ms / target * 100)), "success" if passed else "retry", "correct" if passed else "omitted", None, tags, [], [], [])
    observed = normalize(transcript)
    if not observed:
        return AnalysisResult(0, "no_speech", "omitted", None, [], [], [], [])
    g2p = SimpleKoreanG2P()
    target_phones = g2p.to_phonemes(normalize(item["displayText"]) or "")
    observed_phones = g2p.to_phonemes(observed)
    ops = align(target_phones, observed_phones)
    symbol = goal.target_phoneme
    eligible = [i for i, p in enumerate(target_phones) if p.symbol == symbol and p.slot == ("F" if goal.word_position == "final" else "I") and (goal.word_position != "initial" or p.syllable_index == 0) and (goal.word_position != "medial" or p.syllable_index > 0)]
    if not eligible:
        eligible = [i for i, p in enumerate(target_phones) if p.symbol == symbol]
    target_ops = [op for op in ops if op.target_index in eligible]
    target_status = "correct" if target_ops and all(op.kind == "match" for op in target_ops) else "omitted" if any(op.kind == "del" for op in target_ops) else "substituted"
    substitution = next((observed_phones[op.observed_index].symbol for op in target_ops if op.kind == "sub" and op.observed_index is not None), None)
    total = sum(3 if i in eligible else 1 for i in range(len(target_phones))) or 1
    loss = sum(op.cost * (3 if op.target_index in eligible else 1) for op in ops)
    score = max(0, round(100 * (1 - loss / total)))
    tags = []
    if target_status == "omitted":
        tags.append("OMISSION")
    elif substitution:
        if symbol in "ㅅㅆㅈ" and substitution in "ㄷㄸㅌ":
            tags.append("STOPPING")
        elif symbol in "ㅅㅆ" and substitution in "ㅈㅉㅊ":
            tags.append("AFFRICATION")
        elif (symbol, substitution) in [("ㅅ", "ㅆ"), ("ㅈ", "ㅉ")]:
            tags.append("TENSING")
        else:
            tags.append("OTHER_SUBSTITUTION")
    threshold = goal.pass_threshold or {"accuracy": 85, "balanced": 75, "speed": 70}[goal.priority]
    passed = target_status == "correct" and score >= threshold
    rule_id = None
    for rule in rules:
        params = rule.params
        if rule.rule_type == "ACCEPT_WORD_VARIANT" and params.get("item_text") == item["displayText"] and normalize(params.get("variant")) == observed:
            passed, rule_id = True, rule.id
        if rule.rule_type == "ACCEPT_SUBSTITUTION" and params.get("phoneme") == symbol and params.get("substitute") == substitution:
            passed, rule_id = True, rule.id
    return AnalysisResult(score, "success" if passed else "retry", target_status, substitution, tags, [asdict(p) for p in target_phones], [asdict(p) for p in observed_phones], [asdict(op) for op in ops], rule_id)
