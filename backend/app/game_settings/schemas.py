"""게임 설정의 허용 범위(시작 BPM 76~100, 기본 84)와 입력 형식을 정의한다.
설정 저장 API의 입력 검사와 회기 상태(session_state.py)의 박자 기록 검사에 함께 쓴다.
"""

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

MIN_START_BPM = 76
MAX_START_BPM = 100
DEFAULT_START_BPM = 84
DEFAULT_ALLOW_FASTER = True


class DaeguCrossingRhythm(BaseModel):
    """대구 횡단보도 게임의 박자 설정 형식(시작 BPM과 빨라지기 허용 여부). 모르는 키는 거절한다."""
    model_config = ConfigDict(extra="forbid", strict=True)
    startBpm: StrictInt = Field(ge=MIN_START_BPM, le=MAX_START_BPM)
    allowFaster: StrictBool


class GameSettingsInput(BaseModel):
    """게임 설정 저장 요청 본문 형식. 지금은 대구 횡단보도 박자 설정 하나만 담는다."""
    model_config = ConfigDict(extra="forbid", strict=True)
    daeguCrossing: DaeguCrossingRhythm
