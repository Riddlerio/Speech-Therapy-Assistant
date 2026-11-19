import pytest
from types import SimpleNamespace

from app.games.rounds import GAME_ROUNDS
from app.games.evaluation import evaluate_round
from test_api_flow import SHORT_ONSET, api, legacy_session, real_child_auth, student_auth

GOOD_REAL = {"durationMs": 700, "activeMs": 500, "onsetFricationMs": 90, "voicedAfterFricationMs": 300,
             "noiseFloorDb": -60, "meanRmsDb": -35}


def test_crossing_has_five_distinct_rounds():
    assert list(GAME_ROUNDS) == ["daegu_crossing"]
    rounds = GAME_ROUNDS["daegu_crossing"]
    assert [r.index for r in rounds] == [1, 2, 3, 4, 5]
    assert len({r.id for r in rounds}) == 5
    assert all(r.child_title and r.clinical_focus and r.generalization_level and r.rule for r in rounds)


@pytest.mark.parametrize("round_index", range(1, 6))
def test_each_round_separates_no_speech_and_poor_audio(round_index):
    definition = GAME_ROUNDS["daegu_crossing"][round_index - 1]
    goal = SimpleNamespace(target_phoneme="ㅅ")
    item = {"game": "daegu_crossing", "displayText": "사과"}
    silent = evaluate_round(definition, item, None, {"source": "microphone", "activeMs": 0}, goal)
    assert silent.result == "no_speech"
    noisy = evaluate_round(definition, item, "사과", {"source": "microphone", "activeMs": 1000,
                           "durationMs": 1200, "noiseFloorDb": -60, "meanRmsDb": -55}, goal)
    assert noisy.result == "uncertain"
    assert "POOR_AUDIO" in noisy.pattern_tags


def _start(client, headers, mode="demo"):
    response = client.post("/api/activities", headers=headers, json={"game": "daegu_crossing", "mode": mode})
    assert response.status_code == 200, response.text
    return response.json()


def test_round_index_is_server_authoritative(api):
    client, _ = api
    headers = student_auth(client)
    result = _start(client, headers)
    response = client.post(f"/api/activities/{result['sessionId']}/utterances", headers=headers,
                           json={"roundIndex": 5, "itemId": result["firstItem"]["itemId"]})
    assert response.status_code == 409


def test_activity_resume_uses_server_round_and_attempt(api):
    client, _ = api
    headers = student_auth(client)
    start = _start(client, headers)
    session_id = start["sessionId"]
    first = client.get(f"/api/activities/{session_id}", headers=headers).json()
    assert first["currentRound"]["index"] == 1
    assert first["firstItem"]["itemId"] == start["firstItem"]["itemId"]
    assert first["nextAttemptIndex"] == 1
    assert first["completedRounds"] == []
    current = first
    for _ in range(2):  # 1라운드의 두 줄을 건넌다.
        item = current.get("nextItem") or current["firstItem"]
        response = client.post(f"/api/activities/{session_id}/utterances", headers=headers,
                               json={"roundIndex": current["roundIndex"], "itemId": item["itemId"],
                                     "attemptIndex": current["nextAttemptIndex"], "acoustic": GOOD_REAL})
        assert response.status_code == 200
        current = response.json()
    resumed = client.get(f"/api/activities/{session_id}", headers=headers).json()
    assert resumed["currentRound"]["index"] == 2
    assert resumed["firstItem"]["itemId"] == current["nextItem"]["itemId"]
    assert resumed["completedRounds"] == [1]


def test_activity_resume_cannot_access_another_child_session(api):
    client, sessions = api
    first = student_auth(client, "HERO01")
    session_id = _start(client, first)["sessionId"]
    second = student_auth(client, "HERO02")
    assert client.get(f"/api/activities/{session_id}", headers=second).status_code == 404
    # 다른 아동의 옛 게임 회기도 409가 아니라 404로 존재를 숨긴다.
    other = legacy_session(sessions, "HERO01", game="magic_beam")
    assert client.get(f"/api/activities/{other}", headers=second).status_code == 404


def test_rejected_observation_is_excluded_from_verified_metric(api):
    client, _ = api
    student, _child = real_child_auth(client)
    started = _start(client, student, mode="real")
    client.post(f"/api/activities/{started['sessionId']}/utterances", headers=student,
                json={"roundIndex": 1, "itemId": started["firstItem"]["itemId"],
                      "acoustic": GOOD_REAL}).raise_for_status()
    from test_api_flow import auth
    therapist = auth(client)
    timeline = client.get(f"/api/sessions/{started['sessionId']}/timeline").json()
    observation = timeline["observations"][0]
    # 같은 관찰에 확인 뒤 거절을 내리면 마지막 결정(거절)만 지표에 반영되어야 한다.
    assert client.post(f"/api/observations/{observation['id']}/decision", headers=therapist,
                       json={"action": "confirm"}).status_code == 200
    summary = client.get(f"/api/sessions/{started['sessionId']}/clinical-summary").json()
    assert summary["rounds"][0]["verifiedN"] == 1
    assert client.post(f"/api/observations/{observation['id']}/decision", headers=therapist,
                       json={"action": "reject", "note": "잡음"}).status_code == 200
    summary = client.get(f"/api/sessions/{started['sessionId']}/clinical-summary").json()
    assert summary["rounds"][0]["totalObservedN"] == 1
    assert summary["rounds"][0]["evaluableN"] == 1
    assert summary["rounds"][0]["verifiedN"] == 0
    assert summary["rounds"][0]["verifiedRate"] is None


def test_demo_observation_is_visible_but_excluded_from_clinical_rate(api):
    client, _ = api
    student = student_auth(client)
    started = _start(client, student)
    client.post(f"/api/activities/{started['sessionId']}/utterances", headers=student,
                json={"roundIndex": 1, "itemId": started["firstItem"]["itemId"],
                      "acoustic": SHORT_ONSET}).raise_for_status()
    from test_api_flow import auth
    therapist = auth(client)
    timeline = client.get(f"/api/sessions/{started['sessionId']}/timeline", headers=therapist).json()
    observation_id = timeline["observations"][0]["id"]
    client.post(f"/api/observations/{observation_id}/decision", headers=therapist,
                json={"action": "confirm", "note": "데모 확인"}).raise_for_status()
    summary = client.get(f"/api/sessions/{started['sessionId']}/clinical-summary", headers=therapist).json()
    assert summary["rounds"][0]["totalObservedN"] == 0
    assert summary["rounds"][0]["evaluableN"] == 0
    assert summary["rounds"][0]["demoN"] == 1
    assert summary["rounds"][0]["verifiedN"] == 0
    assert summary["rounds"][0]["verifiedRate"] is None
