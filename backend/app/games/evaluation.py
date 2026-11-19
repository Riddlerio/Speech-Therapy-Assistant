"""대구대 건너기 발화와 두두 대화 발화가 라운드 규칙에 맞는지 판정한다(success·retry·uncertain·no_speech 등).
흐름: 아동 화면 → 발화 API → (여기) 판정 → 관찰 → 치료사 확인. main.py의 발화 제출과 호야 대화 근거(hoya/evidence.py)가 부른다.
"""

from ..pronunciation.audio_quality import assess_audio_quality
from ..speech.pipeline import AnalysisResult, SimpleKoreanG2P, acoustic_number, normalize


# 대구대 건너기 음향 근사 기준(2026-10-05 성인 1명 PC 마이크 실측 2회, 정답 확인 포함).
# 근거: documents/05_demo/MIC_MEASUREMENT_2026-10-05.md. 임상적으로 검증된 발음 정확도 기준이 아니다.
# - 바른 '사' 시작 마찰 60~99ms, '차·자' 39~59ms. 2026-10-05에는 70ms(20ms 프레임 4개)였으나, 2026-10-06 사용자 아이폰 시험에서
#   바르게 말한 '사과'가 자주 '다시'가 되어 60ms(3프레임)로 한 칸 내렸다. 3프레임짜리 짧은 '차·자'가 가끔 통과할 수 있어 치료사가 확인한다.
# - 잡음보다 15dB 미만으로 작은 소리는 판단하지 않는다(틀림이 아니라 불확실). 화면 crossingFlow.ONSET_RULE과 같은 값.
ONSET_FRICATION_MS = 60
VOICED_AFTER_FRICATION_MS = 80
ONSET_MIN_SNR_DB = 15


def _result(result: str, tags=()) -> AnalysisResult:
    """판정 결과 문자열과 태그로 점수 0짜리 AnalysisResult를 만든다.
    음향 기준 게임처럼 문장 정렬 점수가 없는 판정 결과를 같은 형식으로 돌려주기 위한 도우미다.
    """
    return AnalysisResult(0, result, "unknown" if result in {"uncertain", "no_speech", "target_observed", "not_target_attempt"} else "correct" if result == "success" else "omitted",
                          None, list(tags), [], [], [])


def evaluate_round(round_def, item: dict, transcript: str | None, acoustic: dict, goal) -> AnalysisResult:
    """라운드 규칙(rule)에 따라 발화를 판정한다. 대화(CONVERSATION)는 인식 문장에서 목표 음소를 찾고, 시작 마찰(ONSET_FRICATION)은 음향 수치를 기준값과 비교한다.
    마이크 입력의 음질이 나쁘거나 시작 마찰·뒤 유성 측정값이 빠지면 틀림 대신 uncertain으로 보류해 치료사가 확인하게 한다(item은 현재 규칙에서 쓰지 않는다).
    """
    if round_def.rule == "CONVERSATION" and (not transcript or not transcript.strip()):
        return _result("no_speech")
    if acoustic.get("source") == "microphone":
        if acoustic_number(acoustic, "activeMs", acoustic_number(acoustic, "voicedMs")) <= 0:
            return _result("no_speech", ["NO_SPEECH"])
        quality = assess_audio_quality(acoustic.get("noiseFloorDb"), acoustic.get("meanRmsDb"),
                                       acoustic.get("durationMs"), acoustic.get("clippingRatio"), acoustic.get("snrDb"))
        if quality["level"] in {"POOR", "UNKNOWN"}:
            return _result("uncertain", ["POOR_AUDIO" if quality["level"] == "POOR" else "NO_ACOUSTIC_EVIDENCE"])
    if round_def.rule == "CONVERSATION":
        if not any(phone.symbol == goal.target_phoneme for phone in SimpleKoreanG2P().to_phonemes(normalize(transcript))):
            return _result("not_target_attempt", ["TARGET_NOT_DETECTED"])
        return _result("target_observed", ["BASELINE_TARGET_DETECTED"])
    if acoustic_number(acoustic, "activeMs", acoustic_number(acoustic, "voicedMs")) <= 0:
        return _result("no_speech", ["NO_SPEECH"])
    # 남은 음향 규칙은 대구대 건너기의 시작 마찰(ONSET_FRICATION) 하나다.
    # 필드가 누락된 입력은 실패로 세지 않는다. 0은 측정된 부재이므로 retry다.
    if acoustic.get("onsetFricationMs") is None or acoustic.get("voicedAfterFricationMs") is None:
        return _result("uncertain", ["NO_ACOUSTIC_EVIDENCE"])
    noise, level = acoustic.get("noiseFloorDb"), acoustic.get("meanRmsDb")
    if acoustic.get("source") == "microphone" and noise is not None and level is not None and level - noise < ONSET_MIN_SNR_DB:
        return _result("uncertain", ["POOR_AUDIO"])
    passed = (acoustic_number(acoustic, "onsetFricationMs") >= ONSET_FRICATION_MS
              and acoustic_number(acoustic, "voicedAfterFricationMs") >= VOICED_AFTER_FRICATION_MS)
    return _result("success" if passed else "retry", ["DEMO_INPUT"] if acoustic.get("source") == "keyboard" else [])
