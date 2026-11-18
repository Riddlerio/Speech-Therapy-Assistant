"""DEMO 샘플 계정(샘플 치료사·샘플 아동 학생)을 알아보고 찾아 준다.
DEMO 모드(SEED_DEMO_DATA)가 꺼져 있으면 auth.py·main.py가 이 계정들의 인증을 막는 데 쓴다.
"""
from sqlalchemy import select

from .models import Account, Child, Therapist


# seed.py가 만드는 샘플 치료사 아이디. 기존 DB에도 같은 이름으로 남아 있다.
DEMO_THERAPIST_USERNAME = "demo"


def is_demo_account(db, account: Account) -> bool:
    """샘플 아동(is_seed)의 학생 계정, 샘플 치료사 계정이면 DEMO 계정으로 본다. 관리자는 해당하지 않는다.
    로그인과 매 요청 인증 때 DEMO 모드가 꺼져 있으면 이 계정을 거절하려고 부른다.
    """
    if account.role == "STUDENT" and account.child_id:
        child = db.get(Child, account.child_id)
        return bool(child and child.is_seed)
    if account.role == "THERAPIST" and account.therapist_id:
        therapist = db.get(Therapist, account.therapist_id)
        if therapist is None:
            return False
        if therapist.username == DEMO_THERAPIST_USERNAME or account.username == DEMO_THERAPIST_USERNAME:
            return True
        return db.scalar(select(Child.id).where(Child.therapist_id == therapist.id, Child.is_seed == True).limit(1)) is not None
    return False


def demo_account(db, role: str) -> Account | None:
    """DEMO 로그인 버튼에 쓸 계정을 찾는다. 치료사는 샘플 치료사, 학생은 첫 샘플 아동이다.
    main.py의 DEMO 로그인 API가 비밀번호 없이 체험 계정으로 들어갈 때 부른다.
    """
    if role == "THERAPIST":
        row = db.scalar(select(Account).where(Account.role == "THERAPIST", Account.username == DEMO_THERAPIST_USERNAME))
    else:
        row = db.scalar(select(Account).join(Child, Child.id == Account.child_id)
                        .where(Account.role == "STUDENT", Child.is_seed == True).order_by(Child.child_code))
    return row if row is not None and is_demo_account(db, row) else None
