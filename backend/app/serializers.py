"""아동 놀이 라우트(main.py)와 치료사 라우트(therapist_api.py)가 함께 쓰는 응답 변환·목표 도우미.
두 파일이 서로를 import하지 않도록(순환 import 방지) 공용 함수만 여기에 둔다."""
from fastapi import HTTPException
from sqlalchemy import select

from .enums import LEVEL_ORDER
from .models import TherapistRule, TrainingGoal, TrainingSession
from .schemas import GoalInput


def public(model, fields):
    """DB 행에서 지정한 필드만 골라 dict로 만든다.
    응답에 내보낼 열을 명시적으로 고르려고(민감 열이 새지 않게) 쓴다."""
    return {field: getattr(model, field) for field in fields}


def goal_data(goal):
    """훈련 목표 행을 응답용 dict로 바꾼다.
    목표 목록·대시보드·추천 결정 응답에서 같은 모양을 쓰려고 둔다."""
    return public(goal, ["id", "child_id", "version", "parent_goal_id", "status", "target_phoneme", "target_sound", "word_position", "level", "min_level", "session_duration_min", "repetition_target", "priority", "preferred_cue", "excluded_words", "excluded_games", "priority_targets", "note", "source", "source_recommendation_id"])


def child_data(child):
    """아동 행을 응답용 dict로 바꾼다.
    아동 목록·상세·대시보드 응답에서 같은 모양을 쓰려고 둔다."""
    return public(child, ["id", "child_code", "hero_name", "age_band", "play_code", "xp", "collection_json", "is_seed"])


def rec_data(rec):
    """legacy 목표 추천(AIRecommendation) 행을 응답용 dict로 바꾼다.
    추천 목록과 결정 응답에서 쓴다(출처 정보가 필요하면 rec_payload)."""
    return public(rec, ["id", "child_id", "session_id", "rule_id", "observation", "evidence", "suggestion_text", "suggested_goal", "rationale", "confidence", "status"])


def activity_rec_data(rec):
    """활동 제안(ActivityRecommendation) 행을 응답용 dict로 바꾼다.
    활동 제안 목록·생성·결정 응답에서 같은 모양을 쓰려고 둔다."""
    return public(rec, ["id", "child_id", "activity", "clinical_purpose", "reason", "evidence", "confidence", "status", "selected_activity", "decision_note", "created_at", "decided_at"])


def clinical_eligible(session) -> bool:
    """실제 음성 모드의 비샘플 세션만 임상 검증 통계에 들어간다.
    DEMO·seed 자료가 임상 수치에 섞이지 않게 하는 단일 기준이다."""
    return session is not None and session.mode == "real" and not session.is_seed


def legacy_recommendation_allowed(session) -> bool:
    """legacy 목표 추천(R1–R5)은 임상 근거 회기나 seed 시연 회기에서만 만든다.
    실제 아동의 DEMO 연습은 수락 시 목표를 바꾸는 추천을 만들지 않는다. seed 시연 자료는 샘플로 표시된다."""
    return session is not None and (clinical_eligible(session) or session.is_seed)


def rec_payload(db, rec):
    """legacy 추천 dict에 원본 회기의 모드·샘플 여부·임상 적격성(provenance)을 붙인다.
    치료사가 추천이 DEMO 연습에서 나왔는지 구분할 수 있게 한다."""
    session = db.get(TrainingSession, rec.session_id)
    return {**rec_data(rec), "provenance": {"sessionMode": session.mode if session else None,
                                            "sessionIsSeed": bool(session and session.is_seed),
                                            "clinicalEligible": clinical_eligible(session),
                                            "demoPractice": not legacy_recommendation_allowed(session)}}


def observation_data(observation, session=None):
    """임상 관찰 dict에 DEMO 여부와 임상 적격성 표시를 붙인다.
    타임라인·관찰 상세·결정 응답에서 DEMO 관찰을 구분하려고 쓴다."""
    eligible = clinical_eligible(session)
    return {**observation_fields(observation), "is_demo": not eligible, "clinical_eligible": eligible}


def observation_fields(observation):
    """임상 관찰 행에서 응답에 내보낼 필드만 고른다.
    observation_data가 표시 값을 붙이기 전 기본 모양이다."""
    return public(observation, ["id", "session_id", "utterance_id", "round_index", "round_id", "difficulty", "activity",
                                "target_phoneme", "word_position", "generalization_level", "attempt_number",
                                "cue_type", "independence", "duration_ms", "audio_quality", "ai_result",
                                "ai_confidence", "possible_error_pattern", "evidence", "provenance",
                                "verification_state", "created_at"])


def current_goal(db, child_id):
    """아동의 현재 활성 목표를 찾는다(없으면 None).
    활동 시작·대시보드·새 목표 버전 만들기에서 쓴다."""
    return db.scalar(select(TrainingGoal).where(TrainingGoal.child_id == child_id, TrainingGoal.status == "active"))


def create_goal(db, child, values, source="manual", recommendation_id=None):
    """이전 목표를 복사해 values를 덮어쓴 새 목표 버전을 만들고 이전 목표는 superseded로 바꾼다.
    아동 등록·목표 수정·추천 수락·seed에서 쓰며, 단계가 바뀌면 그 단계 한정 규칙을 끈다."""
    previous = current_goal(db, child.id)
    data = {key: getattr(previous, key) for key in GoalInput.model_fields} if previous else GoalInput().model_dump()
    data.update(values)
    if data["level"] not in [v.value for v in LEVEL_ORDER] or data["min_level"] not in [v.value for v in LEVEL_ORDER] or LEVEL_ORDER.index(data["min_level"]) > LEVEL_ORDER.index(data["level"]):
        raise HTTPException(422, "최소 단계가 목표 단계보다 높습니다")
    if previous:
        previous.status = "superseded"
    goal = TrainingGoal(child_id=child.id, version=previous.version + 1 if previous else 1, parent_goal_id=previous.id if previous else None, source=source, source_recommendation_id=recommendation_id, **data)
    db.add(goal)
    for rule in db.scalars(select(TherapistRule).where(TherapistRule.child_id == child.id, TherapistRule.active == True)).all():
        if rule.params.get("scope") == "until_level_change" and rule.level_at_creation != goal.level:
            rule.active, rule.deactivated_reason = False, "goal level changed"
    db.flush()
    return goal
