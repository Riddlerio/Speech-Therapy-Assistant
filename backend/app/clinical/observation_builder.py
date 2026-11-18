"""아동 발화 하나의 판정 결과를 치료사가 확인할 임상 관찰(ClinicalObservation) 행으로 만든다.
흐름: 아동 화면 → 발화 API → 판정 → (여기) 관찰 → 치료사 확인. main.py의 5라운드 발화 제출 처리가 부른다.
"""

from ..models import ClinicalObservation
from ..pronunciation.audio_quality import assess_audio_quality


def build_observation(session, utterance, goal, analysis, acoustic: dict, state: dict) -> ClinicalObservation:
    """음질 등급, AI 확신도, 라운드·단서·독립성, 근거(음향·목표 문장·확인 낱말 여부)와 출처 표시를 채운 관찰 객체를 만든다.
    DEMO 모드나 음질이 좋지 않으면 확신도를 낮추고, 각 값이 측정값인지 AI 추정인지 provenance에 적어 치료사가 판단하게 한다.
    """
    quality = (assess_audio_quality(acoustic.get("noiseFloorDb"), acoustic.get("meanRmsDb"),
                                    acoustic.get("durationMs"), acoustic.get("clippingRatio"),
                                    acoustic.get("snrDb"))["level"] if session.mode == "real" else "UNKNOWN")
    confidence = "UNCERTAIN" if analysis.result in {"uncertain", "no_speech"} else "LOW" if session.mode == "demo" or analysis.result == "target_observed" else "MEDIUM" if quality == "GOOD" else "LOW"
    round_index = max(1, min(5, int(state.get("roundIndex", 1))))
    round_def = state.get("roundDefinition", {})
    return ClinicalObservation(
        session_id=session.id, utterance_id=utterance.id, child_id=session.child_id,
        round_index=round_index, round_id=round_def.get("id", f"{utterance.game}.r{round_index}"), activity=utterance.game,
        difficulty=state.get("difficulty", 2),
        target_phoneme=goal.target_phoneme, word_position=goal.word_position,
        generalization_level=round_def.get("generalizationLevel", utterance.level.upper()), attempt_number=utterance.attempt_index,
        cue_type=state.get("currentCue", "UNKNOWN"), independence=round_def.get("independence", "UNKNOWN"),
        duration_ms=round(acoustic["durationMs"]) if "durationMs" in acoustic else None,
        audio_quality=quality, ai_result=analysis.result, ai_confidence=confidence,
        possible_error_pattern=analysis.pattern_tags,
        evidence={"acoustic": acoustic, "targetText": utterance.item_text, "targetStatus": analysis.target_status,
                  "clinicalFocus": round_def.get("clinicalFocus"), "elicitationType": round_def.get("elicitationType"),
                  **({"itemSource": "TRAINING_BANK", "itemIndexInRound": state["itemIndexInRound"],
                      "stripeIndex": state["stripeIndex"],
                      "assessmentMethod": "ONSET_FRICATION_ACOUSTIC_APPROXIMATION"}
                     if utterance.game == "daegu_crossing" else {}),
                  **({"probe": True, "probeIndex": state["probeIndex"], "itemSource": "UNPRACTICED_BANK"}
                     if state.get("probeStarted") and not state.get("probeComplete") else {})},
        provenance={"acoustic": "CLIENT_REPORTED", "target": "SYSTEM_MEASURED",
                    "aiResult": "AI_ESTIMATED", "cue": "SYSTEM_MEASURED"},
    )
