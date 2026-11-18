"""SQLAlchemy DB 엔진과 세션(연결) 공장을 만든다.
모든 API가 get_db로 DB 세션을 받아 테이블을 읽고 쓰는 출발점이다.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import settings


class Base(DeclarativeBase):
    """모든 DB 테이블 모델(models.py)이 상속하는 기본 클래스.
    이 클래스를 상속한 모델들이 모여 실제 테이블 목록이 된다.
    """
    pass


def make_engine(url: str):
    """DB 주소(url)로 SQLAlchemy 엔진을 만든다. SQLite면 여러 스레드에서 쓰도록 옵션을 켠다.
    메모리 DB("sqlite://")는 연결 하나를 공유해야 테이블이 사라지지 않으므로 StaticPool을 쓴다(테스트용).
    """
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    if url == "sqlite://":
        kwargs["poolclass"] = StaticPool
    return create_engine(url, **kwargs)


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    """요청 하나 동안 쓸 DB 세션을 열어 주고, 요청이 끝나면 자동으로 닫는다.
    FastAPI 라우트가 Depends(get_db)로 받아 쓰는 의존성 함수다.
    """
    with SessionLocal() as db:
        yield db
