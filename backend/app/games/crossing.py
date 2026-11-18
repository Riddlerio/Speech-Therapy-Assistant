"""대구대 건너기(횡단보도) 게임의 10줄(5라운드 × 2낱말) 진행, 판 반복, 피드백 없는 확인 낱말(probe)을 관리한다.
게임 이동(화면 진행)과 음향 근사 관찰(판정)을 분리하며, main.py의 게임 시작·발화·판 추가·확인 낱말 API가 부른다.
"""
import secrets
from dataclasses import replace

from fastapi import HTTPException

from ..training.content import items
from .rounds import GAME_ROUNDS, public_round

GAME = "daegu_crossing"
ITEMS_PER_ROUND = 2
MAX_TRIES = 3
MAX_NEUTRAL_STREAK = 3
# 한 판(5라운드 10줄)을 마치면 같은 회기에서 처음부터 한 판 더 건널 수 있다(제품 규칙, 연습량 늘리기).
# 판마다 새 회기를 만들지 않으므로 치료사 화면의 회기·숙달 계산은 판 수만큼 늘지 않는다.
MAX_LAPS = 3
# 피드백 없는 확인 낱말은 은행 순서로 최대 세 개, 보류일 때만 같은 카드를 한 번 더 듣는다.
MAX_PROBE_WORDS = 3
MAX_PROBE_TRIES = 2
PRACTICE_WORDS = frozenset({"사과", "수박", "시소", "소리"})


def build_stages(goal):
    """목표(어두 /ㅅ/, 음절·낱말 단계)에 맞춰 5라운드 각각에 쓸 연습 항목 2개씩을 고른다. 제외 낱말은 같은 단계의 다른 항목으로 바꾼다.
    게임을 시작할 때 부르며, 이 근사 판정은 어두 /ㅅ/만 지원하므로 다른 목표면 422로 거절한다.
    """
    # 이 근사는 /ㅅ/ 어두만 지원한다. 다른 목표에 같은 판정을 붙이지 않는다.
    if goal.target_phoneme != "ㅅ" or goal.word_position != "initial" or goal.level not in {"syllable", "word"}:
        raise HTTPException(422, "대구대 건너기는 어두 /ㅅ/의 음절·낱말 목표를 지원합니다")
    excluded = set(goal.excluded_words or ())
    syllables = [row for row in items("ㅅ", "syllable") if row["displayText"] not in excluded]
    words = [row for row in items("ㅅ", "word") if row["displayText"] not in excluded]
    if not words or (goal.level == "syllable" and not syllables):
        raise HTTPException(422, "목표의 제외 낱말 설정에서 사용할 연습 항목이 없습니다")

    def choose(bank, text):
        # 제외 항목은 다시 넣지 않고 같은 단계의 기존 항목으로 대체한다.
        return next((row for row in bank if row["displayText"] == text), bank[0])

    if goal.level == "word":
        # 낱말 목표에서는 마지막 혼합 구간도 목표 단계 아래로 낮추지 않는다.
        texts = (("사과", "사과"), ("사과", "사과"), ("소리", "시소"),
                 ("사과", "수박"), ("수박", "시소"))
        rows = [[choose(words, text) for text in pair] for pair in texts]
    else:
        rows = [[choose(syllables, text) for text in pair]
                for pair in (("사", "사"), ("사", "사"), ("소", "시"))]
        rows.extend([[choose(words, "사과"), choose(words, "수박")],
                     [choose(syllables, "수"), choose(words, "시소")]])
    stages = []
    for definition, selected in zip(GAME_ROUNDS[GAME], rows):
        stage_items = [{**row, "itemId": secrets.token_hex(8), "game": GAME} for row in selected]
        stages.append({"game": GAME, "level": stage_items[0]["level"],
                       "round": public_round(definition), "items": stage_items})
    return stages


def definition_for_item(state):
    """현재 상태에 맞는 라운드 정의를 돌려준다. 확인 낱말 중이면 "새 낱말 확인"용 정의로 바꾸고, 아니면 현재 항목 단계를 일반화 단계로 쓴다.
    단서 갱신·화면 응답·관찰 기록이 같은 라운드 정보를 쓰게 하려고 있다.
    """
    definition = GAME_ROUNDS[GAME][state["roundIndex"] - 1]
    if probe_active(state):
        return replace(definition, id=f"{GAME}.probe", child_title="새 낱말 확인",
                       child_prompt="그림의 이름을 들려줘!", generalization_level="WORD",
                       clinical_focus="연습하지 않은 낱말의 일반화 확인 · 피드백 없음",
                       elicitation_type="GENERALIZATION_PROBE", prompt_type="PICTURE_PROMPT",
                       attempts=MAX_PROBE_TRIES, item_source="UNPRACTICED_BANK")
    return replace(definition, generalization_level=state["currentItem"]["level"].upper())


def update_cue(state, *, model):
    """다음 표시 항목의 단서(시범 듣기 또는 그림·글자)와 관찰 스냅숏(유도 방식·독립성)을 함께 바꾼다.
    두 값을 한곳에서 같이 바꿔, 관찰 기록의 단서 정보가 실제 화면과 어긋나지 않게 한다.
    """
    definition = definition_for_item(state)
    state["modelCue"] = model
    cue = "AUDITORY_MODEL" if model else definition.prompt_type
    state["currentCue"] = cue
    state["roundDefinition"] = {**public_round(definition),
                                "elicitationType": "DIRECT_IMITATION" if model else definition.elicitation_type,
                                "independence": "MODELED" if model else "INDEPENDENT"}


def child_round(definition):
    """아동 화면에 보낼 라운드 정보(제목·안내 문장·시도 수)만 만든다.
    기존 4게임의 payload는 유지하고, 이 게임에서는 임상 근거·판정 기준을 아이 화면에 내보내지 않으려고 따로 둔다.
    """
    # 기존 4게임의 payload는 유지하고 이 게임에서는 임상 근거·판정 기준을 내보내지 않는다.
    return {"index": definition.index, "id": definition.id, "childTitle": definition.child_title,
            "childPrompt": definition.child_prompt,
            "attempts": MAX_PROBE_TRIES if definition.elicitation_type == "GENERALIZATION_PROBE" else MAX_TRIES,
            "targetMs": 0, "endHoldMs": definition.end_hold_ms}


def cursor(state):
    """현재 진행 위치(라운드·줄·남은 시도·시범 여부·판 번호·확인 낱말 진행)를 화면용 dict로 만든다.
    게임 상태 응답(activity_payload)에 합쳐 아동 화면이 캐릭터 위치와 버튼을 그리게 한다.
    """
    active_probe = probe_active(state)
    complete = bool(state.get("roundsComplete")) and not active_probe
    return {"roundIndex": state["roundIndex"], "itemIndexInRound": state["itemIndexInRound"],
            "stripeIndex": state["stripeIndex"],
            "triesLeft": MAX_PROBE_TRIES - state.get("probeAttemptsUsed", 0) if active_probe else
            0 if complete else MAX_TRIES - state["itemAttemptsUsed"],
            "modelCue": False if complete or active_probe else state["modelCue"], "sessionComplete": complete,
            "lap": state.get("lap", 1), "maxLaps": MAX_LAPS,
            "probeStarted": state.get("probeStarted", False), "probeComplete": state.get("probeComplete", False),
            "probeIndex": state.get("probeIndex", 0), "probeTotal": len(state.get("probeItems", []))}


def probe_active(state):
    """확인 낱말 단계가 시작됐고 아직 끝나지 않았는지 판단한다.
    진행·화면·참여 시간 계산이 지금 확인 낱말 중인지에 따라 다르게 동작해야 해서 여러 곳에서 쓴다.
    """
    return bool(state.get("probeStarted")) and not state.get("probeComplete", False)


def is_probe_observation(row):
    """관찰 행이 확인 낱말(연습하지 않은 낱말) 기록인지 근거(evidence)로 판단한다.
    경과 지표·치료사 분석이 확인 낱말 결과를 연습 결과와 섞지 않으려고 쓴다.
    """
    evidence = row.evidence if isinstance(row.evidence, dict) else {}
    return evidence.get("probe") is True or evidence.get("elicitationType") == "GENERALIZATION_PROBE"


def build_probes(goal, stages):
    """연습한 낱말과 제외 낱말을 뺀 /ㅅ/ 낱말 중 앞에서부터 최대 3개를 확인 낱말로 고른다.
    연습하지 않은 낱말에서도 소리가 나오는지(일반화) 보려고, 한 판을 마친 뒤 확인 낱말을 시작할 때 부른다.
    """
    # 고정 연습 낱말 전체와 제외 설정 때문에 대체되어 실제 연습한 낱말도 모두 뺀다.
    practiced = PRACTICE_WORDS | {row["displayText"] for stage in stages for row in stage["items"]}
    excluded = set(goal.excluded_words or ())
    candidates = [row for row in items("ㅅ", "word") if row["displayText"] not in practiced | excluded]
    return [{**row, "itemId": secrets.token_hex(8), "game": GAME} for row in candidates[:MAX_PROBE_WORDS]]


def start_probes(state, selected):
    """상태를 확인 낱말 단계로 바꾸고 첫 낱말을 띄운다. 고를 낱말이 없으면 바로 완료로 표시한다.
    기록할 사건(PROBE_START 등) 초안 목록을 돌려주며, main.py의 확인 낱말 시작 API가 부른다.
    """
    state.update(probeStarted=True, probeComplete=not selected, probeItems=selected,
                 probeIndex=1 if selected else 0, probeAttemptsUsed=0, modelCue=False, roundAttempt=1)
    drafts = [{"type": "PROBE_START", "payload": {"wordN": len(selected)}}]
    if selected:
        state["currentItem"] = selected[0]
        update_cue(state, model=False)
    else:
        drafts.append({"type": "PROBE_COMPLETE", "payload": {}})
    return drafts


def advance_probe(state, result):
    """확인 낱말 발화 한 번을 반영한다. 정오는 저장에만 쓰고 공개 이벤트는 중립이며, 판단 보류일 때만 한 번 더 듣는다.
    아이에게 맞고 틀림을 알려 주지 않아야 일반화를 정확히 볼 수 있어서 이렇게 한다.
    """
    state["probeAttemptsUsed"] += 1
    drafts = [{"type": "PROBE_RECORDED", "payload": {}}]
    if result in {"uncertain", "no_speech"} and state["probeAttemptsUsed"] < MAX_PROBE_TRIES:
        state["roundAttempt"] += 1
        return drafts
    if state["probeIndex"] == len(state["probeItems"]):
        state["probeComplete"] = True
        drafts.append({"type": "PROBE_COMPLETE", "payload": {}})
    else:
        state["probeIndex"] += 1
        state.update(currentItem=state["probeItems"][state["probeIndex"] - 1],
                     probeAttemptsUsed=0, roundAttempt=1)
        update_cue(state, model=False)
    return drafts


def advance(state, result, stages, at):
    """연습 발화 한 번을 반영해 시도 수·성공 수를 올리고, 필요하면 다음 줄·다음 라운드·판 완료로 넘긴다.
    무발화·불확실은 평가 시도나 보상을 만들지 않는다. 기록할 사건 초안 목록을 돌려주며 main.py의 발화 제출 처리가 부른다.
    """
    drafts = []
    neutral = result in {"uncertain", "no_speech"}
    if neutral:
        state["listenAgainCount"] += 1
        drafts.append({"type": "NO_SPEECH" if result == "no_speech" else "LISTEN_AGAIN", "payload": {}})
    else:
        state["listenAgainCount"] = 0
        state["itemAttemptsUsed"] += 1
        state["roundAttemptsUsed"] += 1
        state["roundEvaluated"] += 1
        state["totalAttempts"] += 1
        state["roundSuccesses"] += int(result == "success")
        drafts.append({"type": "TARGET_SUCCESS" if result == "success" else "TARGET_RETRY", "payload": {}})
    move = result == "success" or state["itemAttemptsUsed"] >= MAX_TRIES or state["listenAgainCount"] >= MAX_NEUTRAL_STREAK
    if not move:
        # 전송 순번은 평가 시도와 다르다. 중립 발화 재전송도 같은 번호로 두 번 저장하지 않는다.
        state["roundAttempt"] += 1
        update_cue(state, model=state["roundIndex"] == 1 or neutral)
        return drafts
    drafts.append({"type": "ITEM_ADVANCE", "payload": {}})
    if state["itemIndexInRound"] == ITEMS_PER_ROUND:
        drafts.append({"type": "ROUND_CLEAR", "payload": {"index": state["roundIndex"], "doneAttempts": state["roundAttemptsUsed"]}})
        if state["roundIndex"] == 5:
            state["roundsComplete"] = True
            state["modelCue"] = False
            drafts.append({"type": "SESSION_COMPLETE", "payload": {"totalAttempts": state["totalAttempts"], "lap": state.get("lap", 1)}})
            return drafts
        state.update(roundIndex=state["roundIndex"] + 1, stageIndex=state["stageIndex"] + 1,
                     itemIndexInRound=1, roundAttemptsUsed=0, roundSuccesses=0, roundEvaluated=0,
                     roundStartedAt=at.isoformat())
        drafts.append({"type": "ROUND_START", "payload": child_round(GAME_ROUNDS[GAME][state["roundIndex"] - 1])})
    else:
        state["itemIndexInRound"] += 1
    state["stripeIndex"] = (state["roundIndex"] - 1) * ITEMS_PER_ROUND + state["itemIndexInRound"]
    state.update(currentItem=stages[state["stageIndex"]]["items"][state["itemIndexInRound"] - 1],
                 itemAttemptsUsed=0, roundAttempt=1, listenAgainCount=0)
    update_cue(state, model=state["roundIndex"] == 1)
    drafts.append({"type": "TARGET_PRESENTED", "payload": {"item": state["currentItem"]}})
    return drafts


def next_lap(state, stages, at):
    """한 판을 마친 회기에서 첫 줄부터 다시 건너도록 상태를 되돌린다. 시도 수(totalAttempts)와 관찰은 같은 회기에 이어서 쌓인다.
    아이가 "한 판 더"를 고르면 main.py의 판 추가 API가 부른다(최대 MAX_LAPS판).
    """
    lap = state.get("lap", 1) + 1
    state.update(lap=lap, roundsComplete=False, roundIndex=1, stageIndex=0, itemIndexInRound=1, stripeIndex=1,
                 roundAttempt=1, roundAttemptsUsed=0, roundSuccesses=0, roundEvaluated=0, itemAttemptsUsed=0,
                 listenAgainCount=0, roundStartedAt=at.isoformat(), currentItem=stages[0]["items"][0])
    update_cue(state, model=True)
    return [{"type": "LAP_START", "payload": {"lap": lap}},
            {"type": "ROUND_START", "payload": child_round(GAME_ROUNDS[GAME][0])},
            {"type": "TARGET_PRESENTED", "payload": {"item": state["currentItem"]}}]
