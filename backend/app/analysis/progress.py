"""회기 하나의 발화·판정·결정 기록을 모아 경과 지표(ProgressMetric)를 다시 계산한다.
회기 완료 때와 치료사가 발화 판정을 고쳤을 때 main.py가 불러, 치료사 화면의 경과 수치가 최신 판정을 따르게 한다.
"""

from sqlalchemy import select, delete
from math import isfinite

from ..models import Utterance, SpeechAnalysis, TrainingDecision, ProgressMetric, ClinicalObservation
from ..games.crossing import is_probe_observation


def _num(acoustic: dict, key: str) -> float:
    """음향 측정값 dict에서 key 값을 꺼내되, 정상적인 숫자가 아니면 0으로 바꾼다.
    아동 화면이 보낸 값에 NaN·문자열이 섞여도 지표 계산이 깨지지 않게 한다.
    """
    value = acoustic.get(key)
    return value if type(value) in (int, float) and isfinite(value) else 0


def recompute(db, session, goal, elapsed_sec=0):
    """회기의 기존 지표를 지우고 시도·성공·재시도·힌트 수, 첫 시도 성공률, 평균 점수 등을 새로 계산해 저장한다.
    횡단보도 게임의 확인 낱말(probe) 결과는 연습 지표에 섞지 않는다. 회기 완료와 치료사 판정 수정 뒤에 부른다.
    """
    db.execute(delete(ProgressMetric).where(ProgressMetric.session_id == session.id))
    rows = db.execute(select(Utterance, SpeechAnalysis).join(SpeechAnalysis, SpeechAnalysis.utterance_id == Utterance.id).where(Utterance.session_id == session.id)).all()
    # 확인 낱말의 자동 결과를 기존 연습 지표에 섞지 않는다. 발화·관찰 원본은 그대로 남긴다.
    if session.runtime_state.get("activityGame") == "daegu_crossing":
        observations = db.scalars(select(ClinicalObservation).where(ClinicalObservation.session_id == session.id,
                                                                  ClinicalObservation.child_id == session.child_id)).all()
        probe_utterances = {row.utterance_id for row in observations if is_probe_observation(row)}
        rows = [(u, a) for u, a in rows if u.id not in probe_utterances]
    decisions = db.scalars(select(TrainingDecision).where(TrainingDecision.session_id == session.id)).all()
    counts = {"success": 0, "retry": 0, "no_speech": 0, "uncertain": 0}
    for _, a in rows:
        counts[a.final_result] = counts.get(a.final_result, 0) + 1
    attempts = counts["success"] + counts["retry"]
    voiced = [_num(u.acoustic or {}, "bestRunMs") for u, a in rows if u.game == "magic_beam" and a.final_result in {"success", "retry"}]
    scores = [a.final_score for u, a in rows if u.game != "magic_beam" and a.final_result in {"success", "retry"}]
    ai_scores = [a.ai_score for u, a in rows if u.game != "magic_beam" and a.ai_result in {"success", "retry"}]
    presented = {u.item_id for u, a in rows if u.game != "magic_beam" and a.final_result in {"success", "retry"}}
    first = sum(1 for u, a in rows if u.game != "magic_beam" and u.attempt_index == 1 and a.final_result == "success")
    metric = ProgressMetric(child_id=session.child_id, session_id=session.id, goal_id=goal.id, phoneme=goal.target_phoneme, position=goal.word_position, level="all", attempts=attempts, successes=counts["success"], retries=counts["retry"], no_speech=counts["no_speech"], hints=sum(d.decision_type == "HINT" for d in decisions), first_try_success_rate=round(100 * first / max(1, len(presented)), 1), success_rate=round(100 * counts["success"] / max(1, attempts), 1), mean_score=round(sum(scores) / max(1, len(scores)), 1), ai_mean_score=round(sum(ai_scores) / max(1, len(ai_scores)), 1), level_down_count=sum(d.decision_type == "LEVEL_DOWN" for d in decisions), level_up_count=sum(d.decision_type == "LEVEL_UP" for d in decisions), duration_sec=elapsed_sec, max_beam_ms=max(voiced, default=0))
    db.add(metric)
    db.flush()
    return metric
