"""보존 기간이 지난 아동 발화 문장·호야 대화 문장을 주기적으로 지우는 개인정보 보존 작업.
main.py가 서버 시작 때 한 번 실행하고, 이후 retention_loop로 백그라운드에서 계속 돌린다.
"""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select

from .models import HoyaChatTurn, Utterance, now


def purge_expired_transcripts(db, retention_days: int, current=None) -> int:
    """보존 기간이 지난 발화의 인식 문장과 대체 인식 결과를 비운다. 비운 발화 수를 돌려준다.
    점수 등 수치 기록은 남기고 아동이 말한 문장(개인정보)만 지우기 위해 있다.
    """
    cutoff = (current or now()) - timedelta(days=retention_days)
    rows = [row for row in db.scalars(select(Utterance).where(Utterance.created_at < cutoff)).all()
            if row.transcript is not None or row.alternatives]
    for utterance in rows:
        utterance.transcript = None
        utterance.alternatives = []
    db.commit()
    return len(rows)


def purge_expired_chat_text(db, retention_days: int, current=None) -> int:
    """보존 기간이 지난 호야 대화의 아동 발화·호야 응답 문장·요청 지문을 비운다. 비운 턴 수를 돌려준다.
    호야 응답도 아동 말을 되풀이할 수 있어서 함께 지운다.
    """
    cutoff = (current or now()) - timedelta(days=retention_days)
    rows = [row for row in db.scalars(select(HoyaChatTurn).where(HoyaChatTurn.created_at < cutoff)).all()
            if row.child_transcript is not None or row.hoya_text is not None or row.request_fingerprint is not None]
    for turn in rows:
        turn.child_transcript = None
        turn.hoya_text = None
        turn.request_fingerprint = None
    db.commit()
    return len(rows)


def run_retention_once(session_factory, retention_days: int) -> int:
    """새 DB 세션을 열어 발화 문장과 대화 문장 보존 삭제를 한 번 실행하고, 지운 총개수를 돌려준다.
    retention_loop가 주기마다 별도 스레드에서 부른다.
    """
    with session_factory() as db:
        return purge_expired_transcripts(db, retention_days) + purge_expired_chat_text(db, retention_days)


async def retention_loop(session_factory, retention_days: int, interval_sec: float) -> None:
    """서버가 켜져 있는 동안 interval_sec마다 보존 기간 삭제를 실행한다.
    한 번 실패해도 로그만 남기고 다음 주기에 다시 시도한다. main.py의 서버 시작 처리(lifespan)가 띄운다.
    """
    while True:
        await asyncio.sleep(interval_sec)
        try:
            await asyncio.to_thread(run_retention_once, session_factory, retention_days)
        except Exception:  # 한 번 실패해도 다음 주기에 다시 시도한다.
            logging.exception("전사문 보존 기간 삭제에 실패했습니다")
