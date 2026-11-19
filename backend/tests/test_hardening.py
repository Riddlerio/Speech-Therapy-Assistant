"""V2 hardening 회귀 테스트: 세션 종류, 라운드 의미, 실제 음향 규칙, DEMO 검토, 로그인, 보존 기간, 보안 헤더."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import main as api_module
from app import maintenance
from app.games.rounds import GAME_ROUNDS, public_round
from app.models import ClinicalObservation, TrainingSession, Utterance, now
from test_api_flow import api, auth, real_child_auth, student_auth


GOOD = {"noiseFloorDb": -60, "meanRmsDb": -35}
ONSET = {"durationMs": 700, "activeMs": 500, "onsetFricationMs": 90, "voicedAfterFricationMs": 300}


def _start(client, headers, game="daegu_crossing", mode="real"):
    response = client.post("/api/activities", headers=headers, json={"game": game, "mode": mode})
    assert response.status_code == 200, response.text
    return response.json()


def _say(client, headers, session_id, round_index, item, acoustic, attempt=1, transcript=None):
    response = client.post(f"/api/activities/{session_id}/utterances", headers=headers,
                           json={"roundIndex": round_index, "itemId": item["itemId"], "attemptIndex": attempt,
                                 "transcript": transcript, "acoustic": acoustic})
    assert response.status_code == 200, response.text
    return {event["type"] for event in response.json()["events"]}


# ---------------------------------------------------------------- 1. 세션 종류 교차 호출

def _past_legacy(sessions, session_id):
    """기존 모험(2026-10-04 제거)이 DB에 남긴 세션처럼 activityGame 없는 상태로 바꾼다."""
    with sessions() as db:
        session = db.get(TrainingSession, session_id)
        session.runtime_state = {k: v for k, v in session.runtime_state.items() if k != "activityGame"}
        db.commit()


def test_removed_legacy_adventure_routes_are_gone(api):
    client, _ = api
    headers = student_auth(client)
    started = _start(client, headers, mode="demo")
    assert client.post("/api/play/start", headers=headers, json={"playCode": "HERO01", "mode": "demo"}).status_code in {404, 405}
    assert client.post(f"/api/play/sessions/{started['sessionId']}/utterances", headers=headers,
                       json={"itemId": started["firstItem"]["itemId"], "acoustic": {"durationMs": 900}}).status_code in {404, 405}


def test_activity_endpoint_accepts_activity_session(api):
    client, _ = api
    headers = student_auth(client)
    started = _start(client, headers, mode="demo")
    assert client.get(f"/api/activities/{started['sessionId']}", headers=headers).status_code == 200
    events = _say(client, headers, started["sessionId"], 1, started["firstItem"], ONSET)
    assert "TARGET_SUCCESS" in events


def test_activity_endpoint_rejects_past_legacy_session(api):
    client, sessions = api
    headers = student_auth(client)
    started = _start(client, headers, mode="demo")
    _past_legacy(sessions, started["sessionId"])
    assert client.get(f"/api/activities/{started['sessionId']}", headers=headers).status_code == 409
    response = client.post(f"/api/activities/{started['sessionId']}/utterances", headers=headers,
                           json={"roundIndex": 1, "itemId": started["firstItem"]["itemId"], "acoustic": {"durationMs": 900}})
    assert response.status_code == 409


def test_unknown_session_is_404_on_every_play_route(api):
    client, _ = api
    headers = student_auth(client)
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.post(f"/api/play/sessions/{missing}/complete", headers=headers, json={}).status_code == 404
    assert client.get(f"/api/activities/{missing}", headers=headers).status_code == 404
    assert client.post(f"/api/activities/{missing}/utterances", headers=headers,
                       json={"roundIndex": 1, "itemId": "x", "acoustic": {}}).status_code == 404


@pytest.mark.parametrize("damage", [
    {"roundIndex": 9}, {"roundIndex": "1"}, {"activityGame": "unknown_game"}, {"currentItem": []},
    {"roundStartedAt": "not-a-date"}, {"difficulty": None},
])
def test_malformed_activity_state_is_409_not_500(api, damage):
    client, sessions = api
    headers = student_auth(client)
    started = _start(client, headers, mode="demo")
    with sessions() as db:
        session = db.get(TrainingSession, started["sessionId"])
        session.runtime_state = {**session.runtime_state, **damage}
        db.commit()
    assert client.get(f"/api/activities/{started['sessionId']}", headers=headers).status_code == 409
    response = client.post(f"/api/activities/{started['sessionId']}/utterances", headers=headers,
                           json={"roundIndex": 1, "itemId": started["firstItem"]["itemId"], "acoustic": {"durationMs": 900}})
    assert response.status_code == 409


# ---------------------------------------------------------------- 2. 라운드 의미

def test_round_labels_do_not_claim_untrained_words():
    for rounds in GAME_ROUNDS.values():
        for definition in rounds:
            assert definition.item_source == "TRAINING_BANK"
            text = definition.child_title + definition.child_prompt + definition.clinical_focus
            assert "미훈련" not in text and "처음 보는" not in text and "novel" not in text.lower()
            assert public_round(definition)["endHoldMs"] == definition.end_hold_ms == 700


# ---------------------------------------------------------------- 3. 실제 마이크 음향 규칙

CASES = [
    ({"onsetFricationMs": 90, "voicedAfterFricationMs": 300}, "TARGET_SUCCESS"),
    ({"onsetFricationMs": 30, "voicedAfterFricationMs": 300}, "TARGET_RETRY"),
    ({"onsetFricationMs": 90, "voicedAfterFricationMs": 0}, "TARGET_RETRY"),
]


@pytest.mark.parametrize("acoustic,expected", CASES)
def test_microphone_round_rules_through_api(api, acoustic, expected):
    client, sessions = api
    headers = student_auth(client)
    started = _start(client, headers)
    events = _say(client, headers, started["sessionId"], 1, started["firstItem"],
                  {**GOOD, "durationMs": 700, "activeMs": 500, **acoustic})
    assert expected in events
    with sessions() as db:
        observation = db.scalars(select(ClinicalObservation).where(
            ClinicalObservation.session_id == started["sessionId"])).one()
        assert observation.evidence["acoustic"]["source"] == "microphone"


# ---------------------------------------------------------------- 4. DEMO 관찰 검토 의미

def test_demo_observation_is_marked_and_review_is_not_clinical_verification(api):
    client, _ = api
    student = student_auth(client)
    started = _start(client, student, mode="demo")
    _say(client, student, started["sessionId"], 1, started["firstItem"], ONSET)
    therapist = auth(client)
    observation = client.get(f"/api/sessions/{started['sessionId']}/timeline", headers=therapist).json()["observations"][0]
    assert observation["is_demo"] is True and observation["clinical_eligible"] is False
    decided = client.post(f"/api/observations/{observation['id']}/decision", headers=therapist,
                          json={"action": "confirm"}).json()
    assert decided["verification_state"] == "DEMO_CONFIRMED"


def test_real_observation_is_clinically_eligible(api):
    client, _ = api
    student, _child = real_child_auth(client)
    started = _start(client, student)
    _say(client, student, started["sessionId"], 1, started["firstItem"], {**GOOD, **ONSET})
    therapist = auth(client)
    observation = client.get(f"/api/sessions/{started['sessionId']}/timeline", headers=therapist).json()["observations"][0]
    assert observation["is_demo"] is False and observation["clinical_eligible"] is True
    decided = client.post(f"/api/observations/{observation['id']}/decision", headers=therapist,
                          json={"action": "confirm"}).json()
    assert decided["verification_state"] == "CONFIRMED"


# ---------------------------------------------------------------- 8. 임상 요약 표본 수

def test_summary_separates_evaluable_from_uncertain_and_no_speech(api):
    client, _ = api
    student, _child = real_child_auth(client)
    started = _start(client, student)
    session_id, item = started["sessionId"], started["firstItem"]
    # 건너기는 중립 발화에도 전송 순번(attemptIndex)이 늘어난다.
    _say(client, student, session_id, 1, item, {**GOOD, "durationMs": 900, "activeMs": 0})
    _say(client, student, session_id, 1, item, {"noiseFloorDb": -60, "meanRmsDb": -56, "durationMs": 1200, "activeMs": 1000}, attempt=2)
    _say(client, student, session_id, 1, item, {**GOOD, **ONSET, "onsetFricationMs": 30}, attempt=3)
    therapist = auth(client)
    row = client.get(f"/api/sessions/{session_id}/clinical-summary", headers=therapist).json()["rounds"][0]
    assert row["totalObservedN"] == 3
    assert row["evaluableN"] == 1
    assert row["uncertainN"] == 1
    assert row["noSpeechN"] == 1
    assert row["limitedData"] is True
    assert "n" not in row
    empty = client.get(f"/api/sessions/{session_id}/clinical-summary", headers=therapist).json()["rounds"][1]
    assert empty["totalObservedN"] == 0 and empty["verifiedRate"] is None  # 자료 없음은 0%가 아니다.


# ---------------------------------------------------------------- 5. 로그인

def test_unknown_account_pays_password_hash_cost(api, monkeypatch):
    client, _ = api
    calls = []
    original = api_module.verify_dummy_password
    monkeypatch.setattr(api_module, "verify_dummy_password", lambda password: calls.append(password) or original(password))
    response = client.post("/api/auth/login", json={"username": "nobody", "password": "whatever"})
    assert response.status_code == 401
    assert response.json()["detail"] == client.post("/api/auth/login", json={"username": "demo", "password": "bad"}).json()["detail"]
    assert calls == ["whatever"]


def test_username_limit_blocks_across_passwords(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(api_module.settings, "login_max_failures_pair", 100)
    monkeypatch.setattr(api_module.settings, "login_max_failures_username", 3)
    for index in range(3):
        assert client.post("/api/auth/login", json={"username": "demo", "password": f"bad{index}"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "DEMO", "password": "speechhero"}).status_code == 429
    assert client.post("/api/auth/login", json={"username": "HERO01", "password": "speechhero"}).status_code == 200


def test_ip_limit_blocks_spraying_many_usernames(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(api_module.settings, "login_max_failures_ip", 4)
    for index in range(4):
        assert client.post("/api/auth/login", json={"username": f"user{index}", "password": "x"}).status_code == 401
    blocked = client.post("/api/auth/login", json={"username": "demo", "password": "speechhero"})
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "잠시 후 다시 시도해 주세요"


def test_login_failures_expire_after_window(api, monkeypatch):
    from app.models import LoginFailure
    client, sessions = api
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "demo", "password": "bad"})
    assert client.post("/api/auth/login", json={"username": "demo", "password": "speechhero"}).status_code == 429
    with sessions() as db:
        for row in db.query(LoginFailure).all():
            row.created_at = now() - timedelta(minutes=16)
        db.commit()
    assert client.post("/api/auth/login", json={"username": "demo", "password": "speechhero"}).status_code == 200


def test_demo_accounts_flag_follows_server_setting(api, monkeypatch):
    client, _ = api
    assert client.get("/api/config/public").json()["demoModeEnabled"] is True
    monkeypatch.setattr(api_module.settings, "seed_demo_data", False)
    assert client.get("/api/config/public").json()["demoModeEnabled"] is False


# ---------------------------------------------------------------- 6. 보존 기간

def test_retention_purge_clears_only_expired_transcripts(api):
    _, sessions = api
    with sessions() as db:
        rows = db.scalars(select(Utterance).where(Utterance.transcript.is_not(None))).all()
        assert len(rows) >= 2
        old, fresh = rows[0], rows[1]
        old.created_at = now() - timedelta(days=91)
        old.alternatives = ["사과"]
        fresh.created_at = now() - timedelta(days=1)
        old_id, fresh_id = old.id, fresh.id
        db.commit()
        assert maintenance.purge_expired_transcripts(db, 90) == 1
        assert maintenance.purge_expired_transcripts(db, 90) == 0
    with sessions() as db:
        assert db.get(Utterance, old_id).transcript is None and db.get(Utterance, old_id).alternatives == []
        assert db.get(Utterance, fresh_id).transcript is not None


def test_retention_loop_runs_periodically(monkeypatch):
    calls = []
    monkeypatch.setattr(maintenance, "run_retention_once", lambda factory, days: calls.append(days) or 0)

    async def run():
        task = asyncio.create_task(maintenance.retention_loop(object(), 90, 0.01))
        await asyncio.sleep(0.08)
        task.cancel()
    asyncio.run(run())
    assert len(calls) >= 2 and set(calls) == {90}


# ---------------------------------------------------------------- 7. 보안 헤더

HEADERS = ("Cache-Control", "X-Content-Type-Options", "Referrer-Policy", "X-Frame-Options", "Content-Security-Policy")


def _has_security_headers(response):
    return all(name in response.headers for name in HEADERS)


def test_security_headers_on_normal_and_early_responses(api):
    client, _ = api
    responses = [
        client.get("/api/system/info"),
        client.get("/api/auth/me"),  # 401
        client.post("/api/auth/login", headers={"Origin": "https://evil.example"}, json={}),  # 미들웨어 403
        client.post("/api/auth/login", content=b"{" + b" " * 70000 + b"}",
                    headers={"Content-Type": "application/json"}),  # 미들웨어 413
        client.post("/api/auth/login", content=b"{}", headers={"Content-Type": "application/json",
                                                                "Content-Length": "abc"}),  # 미들웨어 400
        client.post("/api/auth/login", json={"username": ""}),  # 422
    ]
    assert [response.status_code for response in responses] == [200, 401, 403, 413, 400, 422]
    for response in responses:
        assert _has_security_headers(response), response.status_code
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
