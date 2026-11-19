"""지표를 보고 다음 한 회기 계획 초안(시작 단계, 게임, 활동 순서)을 규칙으로 만든다.
LLM이 치료 계획을 만들지 않으며 목표(TrainingGoal)도 바꾸지 않는다. 최종 결정은 치료사가 한다.
"""
from .evidence import MIN_EVALUABLE

START_LEVELS = ("phoneme", "syllable", "word", "short_sentence")
TARGET_LEVELS = ("syllable", "word", "short_sentence")
CROSSING_LEVELS = ("syllable", "word")


def crossing_supported(goal: dict, start: str) -> bool:
    """대구대 건너기로 이 목표·시작 단계를 연습할 수 있는지 본다(어두 /ㅅ/, 목표와 시작 단계가 음절·낱말).
    games/crossing.build_stages가 받는 목표와 같은 범위라서, 아동 화면에서 실제로 시작할 수 있는 게임만 제안한다.
    """
    return (goal["targetPhoneme"] == "ㅅ" and goal["wordPosition"] == "initial"
            and goal["level"] in CROSSING_LEVELS and start in CROSSING_LEVELS)


def _level_row(metrics: dict, level: str) -> dict | None:
    """지표의 단계별 통계(byLevel)에서 원하는 단계의 행을 찾는다. 없으면 None.
    목표 단계의 성공률을 보고 시작 단계를 정할 때 쓴다.
    """
    return next((row for row in metrics["byLevel"] if row["level"] == level), None)


def propose_plan(goal: dict, metrics: dict) -> dict:
    """근거가 부족하면 유지, 목표 단계 성공률이 50% 미만이면 한 단계 낮게 시작, 80% 이상이면 다음 단계 검토 안내를 이유(rationale)와 함께 만든다.
    게임은 대구대 건너기 하나뿐이다. 건너기가 받는 목표(crossing_supported)면 대화 → 건너기 → 대화 3단계를, 아니면 대화 한 단계만 제안한다.
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
    conversation = {"stepType": "CONVERSATION", "activity": "hoya_conversation", "targetLevel": None, "parameters": {"durationMin": 2}}
    # 건너기가 지원하지 않는 목표(다른 음소·위치·단계)에는 실행할 수 없는 게임 단계를 넣지 않는다.
    steps = ([conversation,
              {"stepType": "GAME", "activity": "daegu_crossing", "targetLevel": start, "parameters": {"trials": goal["repetitionTarget"]}},
              dict(conversation)] if crossing_supported(goal, start) else [conversation])
    form = {
        "targetPhoneme": goal["targetPhoneme"], "wordPosition": goal["wordPosition"],
        "startLevel": start, "targetLevel": target,
        "durationMin": goal["sessionDurationMin"], "repetitionTarget": goal["repetitionTarget"],
        "preferredCue": goal["preferredCue"],
        "priorityTargets": list(goal["priorityTargets"]), "excludedWords": list(goal["excludedWords"]),
        "conversationTheme": "", "therapistNote": "",
        "steps": steps,
    }
    # activityHint는 응답 모양 호환용이다. 이전 활동 제안 기능이 없어져 항상 None이다.
    return {"status": status, "form": form, "rationale": rationale, "activityHint": None}
