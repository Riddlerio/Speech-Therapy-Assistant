"""지표를 보고 다음 한 회기 계획 초안(시작 단계, 게임, 활동 순서)을 규칙으로 만든다.
LLM이 치료 계획을 만들지 않으며 목표(TrainingGoal)도 바꾸지 않는다. 최종 결정은 치료사가 한다.
"""
from .evidence import MIN_EVALUABLE

START_LEVELS = ("phoneme", "syllable", "word", "short_sentence")
TARGET_LEVELS = ("syllable", "word", "short_sentence")
MAIN_GAME_BY_LEVEL = {"phoneme": "sky_climb", "syllable": "monster_adventure", "word": "monster_adventure",
                      "short_sentence": "conversation_quest"}
GAMES = ("sky_climb", "monster_adventure", "conversation_quest")


def _level_row(metrics: dict, level: str) -> dict | None:
    """지표의 단계별 통계(byLevel)에서 원하는 단계의 행을 찾는다. 없으면 None.
    목표 단계의 성공률을 보고 시작 단계를 정할 때 쓴다.
    """
    return next((row for row in metrics["byLevel"] if row["level"] == level), None)


def propose_plan(goal: dict, metrics: dict, activity_hint: str | None) -> dict:
    """근거가 부족하면 유지, 목표 단계 성공률이 50% 미만이면 한 단계 낮게 시작, 80% 이상이면 다음 단계 검토 안내를 이유(rationale)와 함께 만든다.
    대화 → 게임 → 대화 3단계 계획 양식을 채워 돌려주며, 녹음 확인·검토 대기 안내도 덧붙인다. 계획 그래프의 제안 단계가 부른다.
    """
    target = goal["level"] if goal["level"] in TARGET_LEVELS else "word"
    minimum = goal["minLevel"] if goal["minLevel"] in START_LEVELS else "phoneme"
    start = target
    rationale = []
    if not metrics["sufficient"]:
        status = "INSUFFICIENT_DATA"
        rationale.append({"code": "INSUFFICIENT_DATA",
                          "text": f"치료사가 확인한 평가 가능 시도가 {metrics['evaluableN']}건으로 {MIN_EVALUABLE}건 미만입니다. "
                                  "현재 목표를 유지하고 추가 관찰이 필요합니다."})
    else:
        status = "READY"
        row = _level_row(metrics, target)
        if row is None or row["evaluableN"] < MIN_EVALUABLE:
            rationale.append({"code": "LIMITED_AT_TARGET_LEVEL",
                              "text": "목표 단계에서 확인된 평가 가능 시도가 적어 현재 목표 단계를 유지하며 관찰을 이어갑니다."})
        elif row["successRate"] < 50:
            lower = START_LEVELS[max(START_LEVELS.index(minimum), START_LEVELS.index(target) - 1)]
            start = lower
            rationale.append({"code": "SUPPORT_LOWER_START",
                              "text": f"목표 단계의 확인된 성공 비율이 {row['successRate']}%입니다. "
                                      "한 단계 낮은 단계에서 시작해 목표 단계로 올라가는 구성을 검토해 주세요."})
        elif row["successRate"] >= 80:
            rationale.append({"code": "REVIEW_NEXT_LEVEL",
                              "text": f"목표 단계의 확인된 성공 비율이 {row['successRate']}%입니다. "
                                      "목표 변경은 자동으로 하지 않으며, 다음 단계 검토 여부는 치료사가 판단합니다."})
        else:
            rationale.append({"code": "MAINTAIN", "text": "현재 목표 단계를 유지하는 구성을 제안합니다."})
    unsure = metrics["uncertainN"] + metrics["noSpeechN"]
    if metrics["verifiedN"] and unsure * 10 >= metrics["verifiedN"] * 3:
        rationale.append({"code": "CHECK_RECORDING",
                          "text": f"불확실 {metrics['uncertainN']}건·무발화 {metrics['noSpeechN']}건은 실패로 세지 않았습니다. "
                                  "녹음 환경 확인을 권합니다."})
    if metrics["pendingReviewN"]:
        rationale.append({"code": "PENDING_REVIEW",
                          "text": f"검토 대기 관찰 {metrics['pendingReviewN']}건은 근거에서 제외했습니다. 확인하면 근거로 쓸 수 있습니다."})
    # 기존 활동 제안(치료사 확인 근거 기반)이 있으면 그 게임을, 없으면 시작 단계에 맞는 게임을 쓴다.
    game = activity_hint if activity_hint in GAMES else MAIN_GAME_BY_LEVEL[start]
    form = {
        "targetPhoneme": goal["targetPhoneme"], "wordPosition": goal["wordPosition"],
        "startLevel": start, "targetLevel": target,
        "durationMin": goal["sessionDurationMin"], "repetitionTarget": goal["repetitionTarget"],
        "preferredCue": goal["preferredCue"],
        "priorityTargets": list(goal["priorityTargets"]), "excludedWords": list(goal["excludedWords"]),
        "conversationTheme": "", "therapistNote": "",
        "steps": [
            {"stepType": "CONVERSATION", "activity": "hoya_conversation", "targetLevel": None, "parameters": {"durationMin": 2}},
            {"stepType": "GAME", "activity": game, "targetLevel": start, "parameters": {"trials": goal["repetitionTarget"]}},
            {"stepType": "CONVERSATION", "activity": "hoya_conversation", "targetLevel": None, "parameters": {"durationMin": 2}},
        ],
    }
    return {"status": status, "form": form, "rationale": rationale, "activityHint": activity_hint}
