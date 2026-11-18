"""녹음 음질을 GOOD·FAIR·POOR·UNKNOWN으로 판정하는 규칙. 브라우저와 같은 규칙을 공유한다.
음질이 나쁜 발화를 틀림으로 판정하지 않고 "불확실"로 보류하기 위해 판정·관찰 단계에서 쓴다.
"""
from math import isfinite


def assess_audio_quality(noise_floor_db: float | None, mean_rms_db: float | None,
                         duration_ms: float | None, clipping_ratio: float | None,
                         snr_db: float | None = None) -> dict:
    """잡음 바닥·평균 음량·길이·클리핑·SNR로 음질 등급과 이유 목록을 돌려준다. 잘못된 숫자는 POOR, 잡음 값이 없으면 UNKNOWN이다.
    클리핑·너무 짧음/김·낮은 SNR이면 POOR, SNR 15dB 이상이고 문제가 없으면 GOOD, 그 밖은 FAIR다.
    """
    if any(value is not None and (type(value) not in (int, float) or not isfinite(value))
           for value in (noise_floor_db, mean_rms_db, duration_ms, clipping_ratio, snr_db)):
        return {"level": "POOR", "snrDb": None, "reasons": ["INVALID_ACOUSTIC"]}
    snr = (mean_rms_db - noise_floor_db if noise_floor_db is not None and mean_rms_db is not None
           else snr_db)
    if noise_floor_db is None:
        return {"level": "UNKNOWN", "snrDb": snr, "reasons": ["NO_NOISE_FLOOR"]}
    reasons = []
    if clipping_ratio is not None and clipping_ratio >= 0.01:
        reasons.append("CLIPPING")
    if duration_ms is not None and duration_ms < 300:
        reasons.append("TOO_SHORT")
    if duration_ms is not None and duration_ms > 8000:
        reasons.append("TOO_LONG")
    if snr is not None and snr < 8:
        reasons.append("LOW_SNR")
    if reasons:
        return {"level": "POOR", "snrDb": snr, "reasons": reasons}
    if noise_floor_db > -35:
        reasons.append("NOISY_FLOOR")
    level = "GOOD" if snr is not None and snr >= 15 and not reasons else "FAIR"
    return {"level": level, "snrDb": snr, "reasons": reasons}
