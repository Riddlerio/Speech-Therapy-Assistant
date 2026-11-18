"""치료사가 아동별 게임 설정(대구 횡단보도 게임의 시작 박자 BPM, 빨라지기 허용)을 읽고 저장하는 API.
치료사 화면의 게임 설정 패널이 호출하며, 실제 저장은 game_settings/service.py가 한다.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..adventure.service import serialize_child
from ..auth import owned_child, require_therapist
from ..db import get_db
from ..models import Therapist
from .schemas import GameSettingsInput
from .service import game_settings, update_game_settings

router = APIRouter(prefix="/api/therapist/children", tags=["game-settings"])


@router.get("/{child_id}/game-settings")
def read_game_settings(child_id: str, db: Session = Depends(get_db), therapist: Therapist = Depends(require_therapist)):
    """담당 아동의 현재 게임 설정과 마지막 수정 시각·수정한 치료사를 돌려준다.
    치료사 화면이 설정 패널을 열 때 호출한다.
    """
    owned_child(db, child_id, therapist)
    return game_settings(db, child_id)


@router.put("/{child_id}/game-settings")
def save_game_settings(child_id: str, body: GameSettingsInput, db: Session = Depends(get_db),
                       therapist: Therapist = Depends(require_therapist)):
    """담당 아동의 게임 설정을 저장하고 저장된 결과를 돌려준다.
    치료사 화면의 저장 버튼이 호출하며, 같은 아동에 대한 동시 저장은 serialize_child로 차례대로 처리한다.
    """
    owned_child(db, child_id, therapist)
    serialize_child(db, child_id)
    result = update_game_settings(db, child_id, therapist, body.daeguCrossing)
    db.commit()
    return result
