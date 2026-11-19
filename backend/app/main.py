"""Speech Hero FastAPI 앱의 시작점: 앱 생성·보안 미들웨어·로그인/로그아웃·아동 놀이(대구대 건너기) 라우트.
치료사 화면 라우트는 therapist_api.py, 기능별 라우트는 각 패키지 api.py에 두고 여기서 순서대로 등록한다."""
import asyncio
import secrets
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import Depends, FastAPI, Header, HTTPException, Response, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import Base, engine, get_db, SessionLocal
from .models import (Account, AuditEvent, CookieSession, LoginFailure, Child, GameEvent, SpeechAnalysis,
                     TrainingGoal, TrainingPlan, TrainingSession, Utterance, now)
from .schemas import CompleteInput, DemoLoginInput, LoginInput, StartActivityInput, UtteranceInput
from .security import hash_token, verify_dummy_password, verify_password
from .maintenance import purge_expired_chat_text, purge_expired_transcripts, retention_loop
from .auth import COOKIE_NAME, create_session, current_account, require_student, require_admin
from .seed import seed
from .demo import demo_account, is_demo_account
from . import static_site
from .session_state import activity_state, completion_state, state_guard
from .analysis.progress import recompute
from .analysis.translation import therapist_text
from .clinical.observation_builder import build_observation
from .games.rounds import GAME_ROUNDS
from .games.evaluation import evaluate_round
from .therapist_api import router as therapist_router
from .hoya.api import router as hoya_chat_router
from .therapist_planning.api import router as session_planning_router
from .therapist_insights.api import router as therapist_insights_router
from .hoya.schema_compat import upgrade_hoya_chat_schema
from .adventure.api import router as adventure_router
from .adventure import service as adventure_service
from .games import crossing
from .game_settings.api import router as game_settings_router
from .game_settings.service import rhythm_snapshot
from .serializers import current_goal


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 때 표 생성·호야 표 구조 갱신·만료 전사 삭제·DEMO seed를 하고 주기 삭제 작업을 띄운다.
    앱이 뜰 때 한 번 실행되며 종료 때 주기 작업을 취소한다."""
    Base.metadata.create_all(engine)
    # PR #4 초기 개발 DB의 호야 대화 표를 현재 구조로 옮긴다(자료 보존). 이미 최신이면 아무것도 하지 않는다.
    upgrade_hoya_chat_schema(engine)
    if settings.secret_key == "dev-only-change-me":
        logging.warning("개발용 SECRET_KEY가 사용 중입니다. 운영 환경에서는 변경하세요.")
    with SessionLocal() as db:
        purge_expired_transcripts(db, settings.transcript_retention_days)
        purge_expired_chat_text(db, settings.transcript_retention_days)
    if settings.seed_demo_data:
        with SessionLocal() as db:
            seed(db)
    task = None
    if settings.transcript_purge_interval_hours > 0:
        task = asyncio.create_task(retention_loop(SessionLocal, settings.transcript_retention_days,
                                                  settings.transcript_purge_interval_hours * 3600))
    yield
    if task:
        task.cancel()


app = FastAPI(title="Speech Hero API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","), allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def limit_body(request: Request, call_next):
    """허용되지 않은 출처를 403으로, 잘못되거나 64KB를 넘는 본문을 400·413으로 막는다.
    라우트가 본문을 읽기 전에 크기·출처를 먼저 걸러 과도한 요청을 차단한다."""
    origin = request.headers.get("origin")
    allowed = {value.strip() for value in settings.cors_origins.split(",")}
    if origin and origin not in allowed:
        return JSONResponse(status_code=403, content={"detail": "허용되지 않은 출처"})
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isdecimal():
            return JSONResponse(status_code=400, content={"detail": "잘못된 Content-Length"})
        if int(length) > 65536:
            return JSONResponse(status_code=413, content={"detail": "요청 본문이 너무 큽니다"})
    size = 0
    chunks = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > 65536:
            return JSONResponse(status_code=413, content={"detail": "요청 본문이 너무 큽니다"})
        chunks.append(chunk)
    request._body = b"".join(chunks)
    return await call_next(request)


# 마지막에 등록한 미들웨어가 가장 바깥에서 실행된다. 403·413 같은 조기 응답에도 보안 헤더가 붙는다.
@app.middleware("http")
async def security_headers(request: Request, call_next):
    """모든 응답에 캐시·nosniff·referrer·framing·CSP 보안 헤더를 붙인다.
    화면(정적 파일)과 API에 서로 다른 CSP를 적용하려고 경로를 구분한다."""
    response = await call_next(request)
    path = request.url.path
    frontend = static_site.is_frontend_path(path)
    # 해시가 붙은 빌드 파일만 오래 캐시한다. HTML과 API 응답은 저장하지 않는다.
    response.headers["Cache-Control"] = ("public, max-age=31536000, immutable"
                                         if frontend and path.startswith("/assets/") and response.status_code == 200 else "no-store")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    # API는 아무것도 불러오지 않는 정책, 화면(HTML·정적 파일)은 React/Vite/three.js가 동작하는 정책이다. 둘 다 framing 금지.
    response.headers["Content-Security-Policy"] = static_site.FRONTEND_CSP if frontend else settings.content_security_policy
    return response


@app.exception_handler(RequestValidationError)
async def safe_validation_error(_request: Request, exc: RequestValidationError):
    """422 검증 오류 목록에서 입력값(input)·컨텍스트(ctx) 항목을 빼고 돌려준다.
    오류 응답에 아동 발화 내용이 다시 실려 나가지 않게 한다."""
    # 입력값과 예외 컨텍스트에는 아동의 발화 내용이 포함될 수 있다.
    errors = [{key: value for key, value in error.items() if key not in {"input", "ctx"}} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


def play_session(db, session_id, account):
    """아동 계정이 소유한 회기를 찾고, 없거나 남의 회기면 404를 낸다.
    놀이 라우트가 회기를 읽기 전 소유권 확인에 쓴다(account가 없으면 서버 내부 seed 호출)."""
    session = db.get(TrainingSession, session_id)
    # account가 없는 경우는 서버 내부 DEMO seed 호출뿐이다.
    if not session or (account is not None and account.child_id != session.child_id):
        raise HTTPException(404, "세션을 찾을 수 없습니다")
    return session


def save_events(db, session, drafts, utterance_id=None, item=None, goal=None, analysis=None):
    """게임 이벤트 초안들을 치료사용 설명과 함께 저장하고 응답용 목록으로 돌려준다.
    활동 시작·발화·판 넘김 때 화면 이벤트와 치료사 타임라인을 같은 기록으로 남긴다."""
    result = []
    for draft in drafts:
        event = GameEvent(session_id=session.id, utterance_id=utterance_id, type=draft["type"], payload=draft.get("payload", {}), therapist_text=therapist_text(draft["type"], draft.get("payload", {}), item, goal, analysis))
        db.add(event)
        db.flush()
        result.append({"id": event.id, "type": event.type, "at": event.created_at.isoformat() if event.created_at else now().isoformat(), "payload": event.payload})
    return result


@app.get("/api/system/info")
def system_info():
    """앱 버전·모드·분석 방식과 음성 전송 안내 문구를 돌려준다.
    화면이 로그인 전에도 서비스 정보와 개인정보 안내를 보여 줄 때 쓴다."""
    return {"version": "0.2.0", "modes": ["real", "demo"], "analysisMethod": "baseline_acoustic_and_asr_v2",
            "pronunciationProvider": "BASELINE", "clinicalValidation": False,
            "notice": "Web Speech API 사용 시 브라우저 제공업체 서버로 음성이 전송될 수 있습니다. 원본 음성은 이 서버에 저장하지 않습니다."}


@app.post("/api/auth/login")
def login(body: LoginInput, request: Request, response: Response, db: Session = Depends(get_db)):
    """아이디·비밀번호를 확인해 쿠키 세션을 열고, 실패는 IP·아이디·쌍 기준으로 세어 429로 제한한다.
    모든 사용자의 로그인 입구이며 없는 아이디도 같은 시간이 걸리게 dummy 검증을 한다."""
    ip = request.client.host if request.client else "unknown"
    username = body.username.lower()
    keys = {"ip": hash_token(f"ip:{ip}"), "user": hash_token(f"user:{username}"), "pair": hash_token(f"pair:{ip}:{username}")}
    limits = {"ip": settings.login_max_failures_ip, "user": settings.login_max_failures_username,
              "pair": settings.login_max_failures_pair}
    since = now() - timedelta(minutes=settings.login_window_minutes)
    db.execute(delete(LoginFailure).where(LoginFailure.created_at < since))
    for kind, key in keys.items():
        recent = db.scalar(select(func.count()).select_from(LoginFailure)
                           .where(LoginFailure.key_hash == key, LoginFailure.created_at >= since))
        if recent >= limits[kind]:
            db.commit()
            raise HTTPException(429, "잠시 후 다시 시도해 주세요")
    user = db.scalar(select(Account).where(Account.username == body.username))
    valid = (verify_password(body.password, user.password_salt, user.password_hash) if user
             else verify_dummy_password(body.password))
    # DEMO 모드가 꺼져 있으면 기존 DB에 남은 샘플 계정도 비밀번호와 관계없이 로그인할 수 없다.
    if valid and not settings.seed_demo_data and is_demo_account(db, user):
        valid = False
    if not valid:
        for key in keys.values():
            db.add(LoginFailure(key_hash=key))
        db.add(AuditEvent(actor_id=user.id if user else keys["pair"][:36], action="LOGIN_FAILURE",
                          resource_id=user.id if user else keys["pair"][:36], result="DENIED"))
        db.commit()
        raise HTTPException(401, "로그인 정보가 올바르지 않습니다")
    # 성공하면 해당 아이디 기준 실패만 지운다. IP 기준 실패는 창이 지날 때까지 남긴다.
    db.execute(delete(LoginFailure).where(LoginFailure.key_hash.in_([keys["user"], keys["pair"]])))
    return start_cookie_session(db, response, user, "LOGIN_SUCCESS")


def start_cookie_session(db, response: Response, user: Account, action: str) -> dict:
    """세션·CSRF 토큰을 만들고 감사 기록을 남긴 뒤 httponly 쿠키를 설정한다.
    일반 로그인과 DEMO 로그인이 같은 쿠키 규칙을 쓰도록 공유한다."""
    token, csrf = create_session(db, user)
    db.add(AuditEvent(actor_id=user.id, action=action, resource_id=user.id, result="SUCCESS"))
    db.commit()
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=settings.cookie_secure,
                        samesite="strict", max_age=12 * 3600, path="/api")
    return {"role": user.role, "csrfToken": csrf}


@app.get("/api/config/public")
def public_config():
    """로그인 전 화면에 필요한 공개 설정(DEMO 모드 여부)을 돌려준다.
    로그인 화면이 DEMO 버튼을 보일지 정할 때 쓴다."""
    return {"demoModeEnabled": settings.seed_demo_data}


@app.post("/api/auth/demo-login")
def demo_login(body: DemoLoginInput, response: Response, db: Session = Depends(get_db)):
    """DEMO 모드에서만 샘플 계정 세션을 만든다. 비밀번호는 브라우저로 전달하지 않는다.
    시연용 빠른 로그인이며 DEMO 모드가 꺼져 있거나 계정이 없으면 404다."""
    if not settings.seed_demo_data:
        raise HTTPException(404)
    user = demo_account(db, body.role)
    if user is None:
        raise HTTPException(404)
    result = start_cookie_session(db, response, user, "DEMO_LOGIN")
    return {**result, "username": user.username}


@app.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response, account: Account = Depends(current_account), db: Session = Depends(get_db)):
    """현재 쿠키 세션을 지우고 감사 기록을 남긴 뒤 쿠키를 삭제한다(204).
    로그아웃 버튼에서 쓰며 서버 쪽 세션도 함께 무효화한다."""
    db.execute(delete(CookieSession).where(CookieSession.token_hash == hash_token(request.cookies[COOKIE_NAME])))
    db.add(AuditEvent(actor_id=account.id, action="LOGOUT", resource_id=account.id, result="SUCCESS"))
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/api")
    response.status_code = 204
    return response


@app.get("/api/auth/me")
def auth_me(account: Account = Depends(current_account)):
    """로그인한 계정의 id와 역할을 돌려준다.
    화면이 새로고침 뒤 로그인 상태와 역할별 화면을 정할 때 쓴다."""
    return {"id": account.id, "role": account.role}


@app.get("/api/admin/health")
def admin_health(_account: Account = Depends(require_admin)):
    """관리자 전용 상태 확인으로 {"status": "ok"}를 돌려준다.
    운영 점검과 관리자 권한 검사가 동작하는지 확인할 때 쓴다."""
    return {"status": "ok"}




@app.post("/api/play/sessions/{session_id}/complete")
@state_guard
def complete(session_id: str, body: CompleteInput, db: Session = Depends(get_db), account: Account | None = Depends(require_student),
             x_activity_lease: str | None = Header(default=None)):
    """대구대 건너기 회기를 끝내고 지표를 재계산해 요약을 저장하고 돌려준다.
    다섯 라운드를 마친 뒤 부르며, 이미 완료된 회기는 저장된 요약을 그대로 돌려준다. 이전 게임 회기는 409다."""
    child_id = account.child_id if account is not None else play_session(db, session_id, account).child_id
    adventure_service.serialize_child(db, child_id)
    session = play_session(db, session_id, account)
    lease = adventure_service.check_lease(db, session, x_activity_lease, allow_paused=True)
    state = completion_state(session)
    if session.status == "completed":
        return session.summary_json
    if not state.get("roundsComplete"):
        raise HTTPException(409, "다섯 라운드를 마친 뒤 모험을 완료할 수 있습니다")
    # 보호된 V2 회기는 숨김·중단 시간을 포함하는 브라우저 벽시계 대신 서버의 활동 시간을 쓴다.
    elapsed_sec = body.elapsed_sec
    if lease is not None:
        adventure_service.accrue_active_time(lease, session, now())
        elapsed_sec = max(0, int(lease.active_elapsed_sec or 0))
    recompute(db, session, db.get(TrainingGoal, session.goal_id), elapsed_sec)
    # 재시도 뒤 성공해도 XP·배지·카드·재료를 발급하지 않는 완료 경계다.
    summary = {"totalAttempts": state.get("totalAttempts", 0),
               "durationSec": max(0, elapsed_sec), "sessionComplete": True}
    session.summary_json = summary
    session.insight_json = ["음향 근사 관찰입니다. 치료사가 확인해 주세요."]
    session.status, session.ended_at = "completed", now()
    db.commit()
    return summary


@app.get("/api/play/children/{play_code}/profile")
def play_profile(play_code: str, db: Session = Depends(get_db), account: Account = Depends(require_student)):
    """놀이 코드로 아동의 영웅 이름(heroName)만 돌려준다.
    아동 화면이 로그인 직후 본인 놀이 코드인지 확인할 때 쓰며, 본인 놀이 코드가 아니면 404다."""
    child = db.scalar(select(Child).where(Child.play_code == play_code.upper()))
    if not child:
        raise HTTPException(404)
    if account.child_id != child.id:
        raise HTTPException(404)
    return {"heroName": child.hero_name}


@app.post("/api/activities")
def start_activity(body: StartActivityInput, db: Session = Depends(get_db), account: Account = Depends(require_student)):
    """현재 목표로 대구대 건너기 5라운드 계획·회기·런타임 상태를 만들고 첫 항목과 시작 이벤트를 돌려준다.
    아동이 게임을 시작할 때 쓰며 보호자 동의(403)와 활성 목표(409)가 있어야 한다."""
    child = db.get(Child, account.child_id)
    if not child.guardian_consent_at:
        raise HTTPException(403, "보호자 동의가 필요합니다")
    goal = current_goal(db, child.id)
    if not goal:
        raise HTTPException(409, "활성 목표가 없습니다")
    definitions = GAME_ROUNDS[body.game]
    plan_stages = crossing.build_stages(goal)
    plan = TrainingPlan(goal_id=goal.id, child_id=child.id,
                        plan_json={"stages": plan_stages, "nextSlot": 5, "rationale": ["V2_ROUND_ACTIVITY"]},
                        rationale_json=["V2_ROUND_ACTIVITY"])
    db.add(plan)
    db.flush()
    first = plan_stages[0]["items"][0]
    # currentCue·modelCue·roundDefinition은 아래 update_cue가 채운다.
    state = {"activityGame": body.game, "roundIndex": 1, "roundAttempt": 1, "roundAttemptsUsed": 0,
             "roundSuccesses": 0, "roundEvaluated": 0, "listenAgainCount": 0, "roundStartedAt": now().isoformat(),
             "difficulty": 2, "difficultyIncreases": 0, "lowStreak": 0, "difficultyDecreased": False,
             "currentItem": first, "totalAttempts": 0, "xp": 0, "stageIndex": 0,
             "itemIndexInRound": 1, "stripeIndex": 1, "itemAttemptsUsed": 0,
             "rhythm": rhythm_snapshot(db, child.id)}
    crossing.update_cue(state, model=True)
    session = TrainingSession(child_id=child.id, goal_id=goal.id, plan_id=plan.id, mode=body.mode, is_seed=child.is_seed,
                              play_token_hash=hash_token(secrets.token_urlsafe(32)), runtime_state=state)
    db.add(session)
    db.flush()
    events = save_events(db, session, [{"type": "SESSION_START", "payload": {"activity": body.game}},
                                        {"type": "ROUND_START", "payload": crossing.child_round(definitions[0])},
                                        {"type": "TARGET_PRESENTED", "payload": {"item": first}}], item=first, goal=goal)
    db.commit()
    return {**adventure_service.activity_payload(db, session), "events": events}


@app.get("/api/activities/{session_id}")
@state_guard
def current_activity(session_id: str, db: Session = Depends(get_db), account: Account = Depends(require_student)):
    """진행 중인 활동 회기의 현재 화면 상태를 돌려준다.
    새로고침·재접속 때 이어 하기용이며 활성 회기가 아니면 409다."""
    session = play_session(db, session_id, account)
    activity_state(session)
    if session.status != "active":
        raise HTTPException(409, "활성 게임이 아닙니다")
    return adventure_service.activity_payload(db, session)


@app.post("/api/activities/{session_id}/utterances")
@state_guard
def activity_utterance(session_id: str, body: UtteranceInput, db: Session = Depends(get_db), account: Account = Depends(require_student),
                       x_activity_lease: str | None = Header(default=None)):
    """대구대 건너기 발화 한 번을 평가·저장하고 진행(재시도·다음 줄·다음 라운드·확인 낱말)을 계산해 돌려준다.
    라운드 상태 기계의 핵심이며 현재 라운드·항목·시도 번호가 다르면 409다."""
    adventure_service.serialize_child(db, account.child_id)
    session = play_session(db, session_id, account)
    state = activity_state(session)
    if session.status != "active" or (state.get("roundsComplete") and not crossing.probe_active(state)):
        raise HTTPException(409, "활성 게임이 아닙니다")
    lease = adventure_service.check_lease(db, session, x_activity_lease)
    if lease is not None:
        adventure_service.heartbeat(db, session, x_activity_lease)
    round_index = state["roundIndex"]
    if body.round_index != round_index or body.item_id != state["currentItem"]["itemId"] or body.attempt_index != state["roundAttempt"]:
        raise HTTPException(409, "현재 라운드 또는 항목과 일치하지 않습니다")
    definition = crossing.definition_for_item(state)
    goal = db.get(TrainingGoal, session.goal_id)
    acoustic = body.acoustic.model_dump(by_alias=True, exclude_none=True)
    acoustic["source"] = "keyboard" if session.mode == "demo" else "microphone"
    analysis = evaluate_round(definition, state["currentItem"], body.transcript, acoustic, goal)
    item = state["currentItem"]
    utterance = Utterance(session_id=session.id, item_id=item["itemId"], item_text=item["displayText"],
                          level=item["level"], game=state["activityGame"], stage_index=round_index - 1,
                          attempt_index=state["roundAttempt"], transcript=body.transcript,
                          alternatives=body.alternatives, recognizer=body.recognizer, acoustic=acoustic)
    db.add(utterance)
    db.flush()
    db.add(SpeechAnalysis(utterance_id=utterance.id, target_phones=analysis.target_phones,
                          observed_phones=analysis.observed_phones, alignment=analysis.alignment,
                          ai_score=analysis.score, ai_result=analysis.result, target_status=analysis.target_status,
                          substitute_symbol=analysis.substitute_symbol, pattern_tags=analysis.pattern_tags,
                          final_score=analysis.score, final_result=analysis.result))
    if analysis.result != "not_target_attempt":
        db.add(build_observation(session, utterance, goal, analysis, acoustic, state))
    stages = db.get(TrainingPlan, session.plan_id).plan_json["stages"]
    is_probe = crossing.probe_active(state)
    drafts = crossing.advance_probe(state, analysis.result) if is_probe else crossing.advance(state, analysis.result, stages, now())
    session.runtime_state = state
    events = save_events(db, session, drafts, utterance.id, item, goal, analysis)
    db.commit()
    finished = bool(state.get("roundsComplete")) and not crossing.probe_active(state)
    return {"events": events, **({} if is_probe else {"result": analysis.result}),
            "nextItem": None if finished else state["currentItem"],
            "nextAttemptIndex": state["roundAttempt"],
            "currentRound": None if finished else crossing.child_round(crossing.definition_for_item(state)),
            **crossing.cursor(state)}


@app.post("/api/activities/{session_id}/laps")
@state_guard
def next_crossing_lap(session_id: str, db: Session = Depends(get_db), account: Account = Depends(require_student),
                      x_activity_lease: str | None = Header(default=None)):
    """대구대 건너기: 한 판을 마친 같은 회기에서 첫 줄부터 한 판 더 건넌다(최대 crossing.MAX_LAPS판).
    새 판은 항목 id를 새로 만들며 새 낱말 확인을 시작한 뒤에는 쓸 수 없다."""
    adventure_service.serialize_child(db, account.child_id)
    session = play_session(db, session_id, account)
    state = activity_state(session)
    if session.status != "active" or state["activityGame"] != crossing.GAME:
        raise HTTPException(409, "활성 건너기 게임이 아닙니다")
    if state.get("probeStarted"):
        raise HTTPException(409, "새 낱말 확인을 시작한 뒤에는 다음 판을 시작할 수 없습니다")
    if not state.get("roundsComplete"):
        raise HTTPException(409, "이번 판을 아직 다 건너지 않았습니다")
    if state.get("lap", 1) >= crossing.MAX_LAPS:
        raise HTTPException(409, "오늘은 여기까지 건넜어요")
    adventure_service.check_lease(db, session, x_activity_lease)
    goal = db.get(TrainingGoal, session.goal_id)
    plan = db.get(TrainingPlan, session.plan_id)
    # 새 판은 항목 id를 새로 만든다. 지난 판의 관찰·발화는 이미 저장되어 있어 계획을 바꿔도 남는다.
    stages = crossing.build_stages(goal)
    plan.plan_json = {**plan.plan_json, "stages": stages}
    drafts = crossing.next_lap(state, stages, now())
    session.runtime_state = state
    events = save_events(db, session, drafts, item=state["currentItem"], goal=goal)
    db.commit()
    return {**adventure_service.activity_payload(db, session), "events": events}


@app.post("/api/activities/{session_id}/probes")
@state_guard
def start_crossing_probes(session_id: str, db: Session = Depends(get_db), account: Account = Depends(require_student),
                          x_activity_lease: str | None = Header(default=None)):
    """끝까지 건넌 판 뒤 연습하지 않은 낱말을 피드백 없이 확인한다.
    일반화 확인용이며 한 회기에 한 번만 시작할 수 있다."""
    adventure_service.serialize_child(db, account.child_id)
    session = play_session(db, session_id, account)
    state = activity_state(session)
    if session.status != "active" or state["activityGame"] != crossing.GAME:
        raise HTTPException(409, "활성 건너기 게임이 아닙니다")
    if not state.get("roundsComplete") or state.get("probeStarted"):
        raise HTTPException(409, "판을 끝까지 건넌 뒤 새 낱말을 한 번만 확인할 수 있습니다")
    adventure_service.check_lease(db, session, x_activity_lease)
    goal = db.get(TrainingGoal, session.goal_id)
    stages = db.get(TrainingPlan, session.plan_id).plan_json["stages"]
    selected = crossing.build_probes(goal, stages)
    drafts = crossing.start_probes(state, selected)
    session.runtime_state = state
    events = save_events(db, session, drafts, goal=goal)
    db.commit()
    return {**adventure_service.activity_payload(db, session), "events": events}


app.include_router(therapist_router)
app.include_router(hoya_chat_router)
app.include_router(session_planning_router)
app.include_router(therapist_insights_router)
app.include_router(adventure_router)
app.include_router(game_settings_router)


# 반드시 마지막에 등록한다. 위의 /api 경로가 먼저 일치하고, 나머지 GET만 프로덕션 SPA로 간다.
@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def frontend(path: str):
    """API가 아닌 GET·HEAD 경로에 빌드된 프론트엔드(SPA) 파일을 돌려준다.
    반드시 마지막에 등록해 위의 /api 경로가 먼저 일치하게 한다."""
    return static_site.serve(settings.frontend_dist, path)
