"""치료사 읽기 화면용 분석 API(횡단보도 게임 분석, 목표별 경과 추세, 회기 인사이트, 호야 대화 기록).
모든 요청은 치료사 로그인과 담당 아동 소유권을 확인하며, 데이터를 읽기만 하고 바꾸지 않는다.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import owned_child, require_therapist
from ..db import get_db
from ..models import Therapist, TrainingSession
from .conversation import conversation_insights
from .crossing_analytics import crossing_analytics
from .service import goal_trends, session_insights

router = APIRouter(prefix="/api", tags=["therapist-insights"])


@router.get("/therapist/children/{child_id}/crossing-analytics")
def read_crossing_analytics(child_id: str, db: Session = Depends(get_db),
                            therapist: Therapist = Depends(require_therapist)):
    """담당 아동의 대구대 건너기 게임 분석 결과를 돌려준다.
    치료사 화면의 횡단보도 분석 패널이 호출한다.
    """
    child = owned_child(db, child_id, therapist)
    return crossing_analytics(db, child)


@router.get("/children/{child_id}/goal-trends")
def read_goal_trends(child_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """담당 아동의 목표·활동별 기준선 대비 최근 성공률 추세를 돌려준다.
    치료사 화면의 경과(추세) 탭이 호출한다.
    """
    child = owned_child(db, child_id, therapist)
    return goal_trends(db, child)


@router.get("/sessions/{session_id}/insights")
def read_session_insights(session_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """회기 하나의 관찰 목록, 라운드별·전체 통계, 요약 문장을 돌려준다. 없는 회기면 404다.
    치료사 화면에서 특정 회기 상세를 열 때 호출한다.
    """
    session = db.get(TrainingSession, session_id)
    if session is None:
        raise HTTPException(404, "회기를 찾을 수 없습니다")
    child = owned_child(db, session.child_id, therapist)
    return session_insights(db, child, session)


@router.get("/children/{child_id}/conversation-insights")
def read_child_conversation_insights(child_id: str, db: Session = Depends(get_db),
                                     therapist: Therapist = Depends(require_therapist)):
    """담당 아동의 최근 호야 대화 기록 요약(턴 수, 목표 관찰·불확실·무발화 수)을 돌려준다.
    치료사 화면의 대화 기록 패널이 아동 기준으로 호출한다.
    """
    child = owned_child(db, child_id, therapist)
    return conversation_insights(db, child)


@router.get("/sessions/{session_id}/conversation-insights")
def read_game_child_conversation_insights(session_id: str, db: Session = Depends(get_db),
                                          therapist: Therapist = Depends(require_therapist)):
    """게임 회기 id로 아동을 찾아 그 아동의 호야 대화 기록 요약을 돌려준다. 없는 회기면 404다.
    치료사 화면이 회기 상세에서 대화 기록을 함께 보여 줄 때 호출한다.
    """
    session = db.get(TrainingSession, session_id)
    if session is None:
        raise HTTPException(404, "회기를 찾을 수 없습니다")
    child = owned_child(db, session.child_id, therapist)
    return conversation_insights(db, child)
