"""발화 하나를 판정하는 Speech Engine: 매직빔은 음향 수치로, 단어 문항은 인식 문장의 음소 정렬로 점수와 결과를 낸다.
흐름: 아동 화면 → 발화 API → (여기) 판정 → 관찰 → 치료사 확인. 치료사 규칙(대치·변이 허용)도 여기서 반영한다.
"""

from dataclasses import dataclass
from dataclasses import asdict
from math import isfinite

from .normalization import normalize
from .g2p import SimpleKoreanG2P
from .alignment import align
from ..pronunciation.audio_quality import assess_audio_quality


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
