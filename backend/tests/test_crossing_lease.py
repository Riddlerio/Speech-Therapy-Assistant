"""대구대 건너기 회기의 진행 기기(lease)·동시 제출·참여 시간 경계를 실제 API로 검증한다.

이전 두두 모험 테스트(test_dudu_adventure.py)의 lease·동시 완료 검증을 건너기 회기로 옮겼다.
임시 DB와 가상 음향 입력을 사용한다. 실제 마이크나 치료 효과 검증은 아니다.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import sessionmaker

from app.db import Base, make_engine
from app.models import (ActivityLease, Child, ProgressMetric, Therapist, TrainingGoal, TrainingPlan,
                        TrainingSession, Utterance, now)
from test_api_flow import api, student_auth
from test_daegu_crossing import request


def _actor(client, login, *args):
    """한 client에서도 두 기기/역할의 쿠키와 CSRF를 섞지 않는다."""
    headers = login(client, *args)
    return {**headers, "Cookie": f"speech_hero_session={client.cookies.get('speech_hero_session')}"}


def _start(client, headers):
    """건너기 회기를 시작하고 이 기기로 claim해 lease 토큰이 붙은 상태를 돌려준다."""
    response = client.post("/api/activities", headers=headers, json={"game": "daegu_crossing", "mode": "demo"})
    assert response.status_code == 200, response.text
    claimed = client.post(f"/api/activities/{response.json()['sessionId']}/claim", headers=headers,
                          json={"takeover": False})
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["leaseToken"]
    return claimed.json()


def _lease(headers, started):
    return {**headers, "X-Activity-Lease": started["leaseToken"]}


def _say(client, headers, started, current):
    response = client.post(f"/api/activities/{started['sessionId']}/utterances",
                           headers=_lease(headers, started), json=request(current))
    assert response.status_code == 200, response.text
    return response.json()


def _cross(client, headers, started, lines=10):
    """첫 시도 성공으로 lines줄을 건너고 마지막 응답을 돌려준다."""
    current = started
    for _ in range(lines):
        current = _say(client, headers, started, current)
    return current


def _two_requests(client, url, headers, bodies):
    """같은 DB 상태를 경쟁하는 요청을 실제 API에 함께 보낸다. sleep을 쓰지 않는다."""
    gate = Barrier(2)

    def send(body):
        gate.wait(timeout=10)
        return client.post(url, headers=headers, json=body)

    with ThreadPoolExecutor(max_workers=2) as workers:
        return list(workers.map(send, bodies))


def test_claim_pause_heartbeat_cannot_cross_child_ownership_or_skip_csrf(api):
    client, _ = api
    first = _actor(client, student_auth)
    started = _start(client, first)
    other = _actor(client, student_auth, "HERO02")
    url = f"/api/activities/{started['sessionId']}"
    assert client.post(f"{url}/claim", headers=other, json={"takeover": True}).status_code == 404
    assert client.post(f"{url}/pause", headers=_lease(other, started)).status_code == 404
    assert client.post(f"{url}/heartbeat", headers=_lease(other, started)).status_code == 404
    assert client.post(f"{url}/claim", headers={"Cookie": first["Cookie"]}, json={"takeover": True}).status_code == 403


def test_paused_duration_is_excluded_from_the_round_clock(api):
    client, sessions = api
    student = _actor(client, student_auth)
    started = _start(client, student)
    url = f"/api/activities/{started['sessionId']}"
    assert client.post(f"{url}/pause", headers=_lease(student, started)).status_code == 200
    # 10초 연습 뒤 한 시간 쉬었다는 저장 스냅숏. 실제 sleep 없이 재개 시계를 검증한다.
    pause_time = now() - timedelta(hours=1)
    with sessions() as db:
        db.get(ActivityLease, started["sessionId"]).paused_at = pause_time
        session = db.get(TrainingSession, started["sessionId"])
        session.runtime_state = {**session.runtime_state, "roundStartedAt": (pause_time - timedelta(seconds=10)).isoformat()}
        db.commit()
    claimed = client.post(f"{url}/claim", headers=_lease(student, started), json={"takeover": False})
    assert claimed.status_code == 200, claimed.text
    with sessions() as db:
        state = db.get(TrainingSession, started["sessionId"]).runtime_state
        assert (now() - datetime.fromisoformat(state["roundStartedAt"])).total_seconds() < 20


def test_concurrent_final_submissions_write_once_and_can_recover_completion(api):
    client, sessions = api
    student = _actor(client, student_auth)
    started = _start(client, student)
    current = _cross(client, student, started, lines=9)
    url = f"/api/activities/{started['sessionId']}"
    final = request(current)
    responses = _two_requests(client, f"{url}/utterances", _lease(student, started), [final, final])
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert next(response for response in responses if response.status_code == 200).json()["sessionComplete"] is True
    with sessions() as db:
        assert len(db.scalars(select(Utterance).where(Utterance.session_id == started["sessionId"])).all()) == 10
    # 완료 API 응답을 잃었을 때는 claim으로 완료 상태를 회수할 수 있다.
    claimed = client.post(f"{url}/claim", headers=_lease(student, started), json={"takeover": False})
    assert claimed.status_code == 200 and claimed.json()["sessionComplete"] is True
    assert client.post(f"{url}/utterances", headers=_lease(student, claimed.json()), json=final).status_code == 409
    completed = client.post(f"/api/play/sessions/{started['sessionId']}/complete",
                            headers=_lease(student, claimed.json()), json={"elapsedSec": 60})
    assert completed.status_code == 200, completed.text


def test_concurrent_leased_completion_writes_summary_and_metric_once(api):
    client, sessions = api
    student = _actor(client, student_auth)
    started = _start(client, student)
    assert _cross(client, student, started)["sessionComplete"] is True
    with sessions() as db:
        session = db.get(TrainingSession, started["sessionId"])
        child_id = session.child_id
        before_xp = db.get(Child, child_id).xp
        assert not db.scalars(select(ProgressMetric).where(ProgressMetric.session_id == session.id)).all()
    responses = _two_requests(client, f"/api/play/sessions/{started['sessionId']}/complete",
                              _lease(student, started), [{"elapsedSec": 600}, {"elapsedSec": 600}])
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert responses[0].json()["totalAttempts"] == 10
    with sessions() as db:
        # 건너기 완료는 XP를 주지 않는다.
        assert db.get(Child, child_id).xp == before_xp
        assert db.get(TrainingSession, started["sessionId"]).status == "completed"
        metrics = db.scalars(select(ProgressMetric).where(ProgressMetric.session_id == started["sessionId"])).all()
        assert len(metrics) == 1 and metrics[0].level == "all"
        assert metrics[0].attempts == 10


@pytest.mark.parametrize("gap_seconds", [3600, 10])
def test_reclaim_excludes_unconfirmed_heartbeat_gap_from_round_and_session_time(api, gap_seconds):
    client, sessions = api
    student = _actor(client, student_auth)
    started = _start(client, student)
    # 마지막으로 확인한 활동은 10초. 이후 연결이 끊긴 시간은 길이에 관계없이 활동 증거가 아니다.
    last_heartbeat = now() - timedelta(seconds=gap_seconds)
    with sessions() as db:
        lease = db.get(ActivityLease, started["sessionId"])
        lease.heartbeat_at = last_heartbeat
        lease.paused_at = None
        lease.active_elapsed_sec = 10.0
        session = db.get(TrainingSession, started["sessionId"])
        session.runtime_state = {**session.runtime_state,
                                 "roundStartedAt": (last_heartbeat - timedelta(seconds=10)).isoformat()}
        db.commit()
    claimed = client.post(f"/api/activities/{started['sessionId']}/claim",
                          headers=_lease(student, started), json={"takeover": False})
    assert claimed.status_code == 200, claimed.text
    resumed = claimed.json()
    assert resumed["currentRound"]["index"] == 1
    assert resumed["firstItem"] == started["firstItem"]
    with sessions() as db:
        session = db.get(TrainingSession, started["sessionId"])
        round_elapsed = (now() - datetime.fromisoformat(session.runtime_state["roundStartedAt"])).total_seconds()
        assert 8 <= round_elapsed < 15
        assert 10 <= db.get(ActivityLease, session.id).active_elapsed_sec < 15
    _cross(client, student, resumed)
    completed = client.post(f"/api/play/sessions/{started['sessionId']}/complete",
                            headers=_lease(student, resumed), json={"elapsedSec": 600})
    assert completed.status_code == 200, completed.text
    assert 10 <= completed.json()["durationSec"] < 20
    with sessions() as db:
        metric = db.scalar(select(ProgressMetric).where(ProgressMetric.session_id == started["sessionId"]))
        assert metric.duration_sec == completed.json()["durationSec"]


def test_create_all_adds_new_tables_without_replacing_existing_rows(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'existing.db').as_posix()}")
    existing = [Therapist.__table__, Child.__table__, TrainingGoal.__table__, TrainingPlan.__table__, TrainingSession.__table__]
    Base.metadata.create_all(engine, tables=existing)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with sessions() as db:
            therapist = Therapist(id="existing-therapist", username="existing", display_name="기존 치료사",
                                  password_salt="11" * 16, password_hash="existing-hash")
            child = Child(id="existing-child", therapist_id=therapist.id, child_code="KEEP01", play_code="KEEP01",
                          hero_name="기존 이름", collection_json={"badges": ["기존 배지"], "monsterCards": ["기존 카드"]})
            goal = TrainingGoal(id="existing-goal", child_id=child.id, version=7, target_phoneme="ㄹ", level="word")
            plan = TrainingPlan(id="existing-plan", child_id=child.id, goal_id=goal.id, plan_json={"oldPlan": True})
            session = TrainingSession(id="existing-session", child_id=child.id, goal_id=goal.id, plan_id=plan.id,
                                      mode="demo", play_token_hash="existing-token-hash", runtime_state={"oldState": True})
            db.add_all([therapist, child, goal, plan, session]); db.commit()
        Base.metadata.create_all(engine)
        Base.metadata.create_all(engine)
        assert set(Base.metadata.tables).issubset(inspect(engine).get_table_names())
        with sessions() as db:
            assert db.get(Child, "existing-child").collection_json == {"badges": ["기존 배지"], "monsterCards": ["기존 카드"]}
            assert db.get(TrainingPlan, "existing-plan").plan_json == {"oldPlan": True}
            assert db.get(TrainingSession, "existing-session").runtime_state == {"oldState": True}
    finally:
        engine.dispose()
