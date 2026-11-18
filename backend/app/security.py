"""비밀번호 해시·검증과 토큰 해시 같은 보안용 작은 함수 모음.
로그인(main.py)과 쿠키 세션·CSRF 확인(auth.py), 모험 활동 임대 토큰(adventure)이 사용한다.
"""

import hashlib
import hmac
import secrets


def hash_password(password: str, salt: str) -> str:
    """비밀번호를 salt와 함께 PBKDF2-SHA256(20만 번 반복)으로 해시한 16진 문자열을 만든다.
    DB에는 비밀번호 원문 대신 이 해시만 저장하기 위해 쓴다.
    """
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def verify_password(password: str, salt: str, expected: str) -> bool:
    """입력한 비밀번호의 해시가 저장된 해시와 같은지 확인한다.
    compare_digest로 비교해 비교 시간으로 정답을 추측하는 공격(타이밍 공격)을 막는다.
    """
    return hmac.compare_digest(hash_password(password, salt), expected)


# 없는 계정에도 같은 PBKDF2 비용을 치르게 해 응답 시간으로 계정 존재를 알 수 없게 한다.
_DUMMY_SALT = secrets.token_hex(16)
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16), _DUMMY_SALT)


def verify_dummy_password(password: str) -> bool:
    """없는 계정으로 로그인할 때도 진짜 검증과 같은 시간을 쓰게 하고 항상 False를 돌려준다.
    로그인 응답 속도 차이로 아이디가 존재하는지 알아내지 못하게 하려고 있다.
    """
    verify_password(password, _DUMMY_SALT, _DUMMY_HASH)
    return False


def hash_token(token: str) -> str:
    """세션·CSRF·임대 토큰을 SHA-256으로 해시한다.
    DB가 유출돼도 원래 토큰을 쓸 수 없도록 토큰 원문 대신 해시만 저장할 때 쓴다.
    """
    return hashlib.sha256(token.encode()).hexdigest()
