"""환경변수 기반 설정 + 보안 헬퍼.

운영 시 환경변수로 주입 (코드/git 에 비밀 넣지 말 것):
  ATTENDANCE_DB_KEY            SQLCipher DB 암호화 키 (db.py에서 사용)
  ATTENDANCE_SECRET_KEY        Flask 세션 쿠키 서명 키
  ATTENDANCE_TEACHER_PASSWORD  교사 로그인 비밀번호
  ATTENDANCE_ALLOWED_SUBNETS   학생 출석 허용 IP 대역 (쉼표구분, 예: "192.168.0.,10.0.")
                               비우면 제한 없음 (모든 IP 허용)
  ATTENDANCE_DEBUG             "1" 이면 Flask 디버그 모드 (운영 금지)
  ATTENDANCE_PORT              포트 (기본 5000)
"""
import os
import sys
import time
import hmac
import secrets
from collections import defaultdict


# --- Flask 세션 서명 키 -----------------------------------------------------
SECRET_KEY = os.environ.get("ATTENDANCE_SECRET_KEY")
if not SECRET_KEY:
    # 미설정 시 프로세스마다 랜덤 — 재시작하면 기존 로그인 세션 무효화됨.
    SECRET_KEY = secrets.token_hex(32)
    print(
        "[WARN] ATTENDANCE_SECRET_KEY 미설정 — 임시 랜덤키 사용 "
        "(재시작 시 로그인 풀림). 운영 시 고정 키 지정.",
        file=sys.stderr,
    )


# --- 교사 계정 / 첫 관리자 시드 ---------------------------------------------
# 다중 교사 계정(teachers 테이블)으로 인증. 첫 실행 시 관리자 1명을 시드.
#   ATTENDANCE_ADMIN_USER / ATTENDANCE_ADMIN_PASSWORD 우선.
#   없으면 하위호환으로 기존 ATTENDANCE_TEACHER_PASSWORD 를 admin 비번으로 사용.
from werkzeug.security import generate_password_hash, check_password_hash

ADMIN_USER = os.environ.get("ATTENDANCE_ADMIN_USER", "admin")
ADMIN_PASSWORD = (os.environ.get("ATTENDANCE_ADMIN_PASSWORD")
                  or os.environ.get("ATTENDANCE_TEACHER_PASSWORD"))
if not ADMIN_PASSWORD:
    ADMIN_PASSWORD = "admin"
    print(
        "[WARN] ATTENDANCE_ADMIN_PASSWORD 미설정 — 첫 관리자 비번 기본값 'admin'. "
        "운영 시 반드시 변경.",
        file=sys.stderr,
    )


def hash_pw(password):
    return generate_password_hash(password)


def verify_pw(pw_hash, candidate):
    if not pw_hash or candidate is None:
        return False
    return check_password_hash(pw_hash, candidate)


# --- 프록시 신뢰 (X-Forwarded-For) ------------------------------------------
# 기본 OFF: X-Forwarded-For 는 클라가 위조 가능 → 그대로 믿으면 ALLOWED_SUBNETS
# 우회·레이트리밋 회피·감사 IP 위조 가능. 신뢰 리버스프록시(nginx 등) 뒤에서만 1.
TRUST_PROXY = os.environ.get("ATTENDANCE_TRUST_PROXY") == "1"


# --- 학생 출석 허용 IP 대역 --------------------------------------------------
_subnets_raw = os.environ.get("ATTENDANCE_ALLOWED_SUBNETS", "").strip()
ALLOWED_SUBNETS = [p.strip() for p in _subnets_raw.split(",") if p.strip()]


def ip_allowed(remote_ip):
    """허용목록 비었으면 전부 허용. 아니면 접두사 매칭."""
    if not ALLOWED_SUBNETS:
        return True
    if not remote_ip:
        return False
    return any(remote_ip.startswith(prefix) for prefix in ALLOWED_SUBNETS)


# --- 무차별 대입 방지: 슬라이딩 윈도우 레이트리밋 (인메모리) ----------------
# 6자리 TOTP = 1e6 경우의 수, valid_window=1 이면 어느 순간 유효코드 ~3개.
# 레이트리밋 없으면 자동 시도로 뚫릴 수 있음 → 실패 시도 제한.
RATE_MAX_FAILS = int(os.environ.get("ATTENDANCE_RATE_MAX_FAILS", "5"))
RATE_WINDOW_SEC = int(os.environ.get("ATTENDANCE_RATE_WINDOW_SEC", "60"))

_fail_log = defaultdict(list)  # key -> [timestamp, ...]


def _now():
    return time.time()


def record_fail(key):
    _fail_log[key].append(_now())


def is_rate_limited(key):
    """윈도우 내 실패 횟수가 한도 이상이면 True."""
    cutoff = _now() - RATE_WINDOW_SEC
    fails = [t for t in _fail_log[key] if t >= cutoff]
    _fail_log[key] = fails  # 만료분 정리
    return len(fails) >= RATE_MAX_FAILS


def reset_fails(key):
    _fail_log.pop(key, None)


# --- 회전 QR 챌린지 (현장 증명) ---------------------------------------------
# 교실 화면에만 뜨는, 짧게 만료되는 챌린지. HMAC 으로 위조 불가 + 시간 바인딩.
# 원격 학생은 현재 화면의 QR 을 못 봐서 유효 챌린지를 얻을 수 없음.
import hashlib  # hmac 은 상단에서 이미 import

QR_ROTATE_SEC = int(os.environ.get("ATTENDANCE_QR_ROTATE_SEC", "10"))


def _qr_epoch(now=None):
    return int((now if now is not None else time.time()) // QR_ROTATE_SEC)


def _qr_nonce(session_id, epoch):
    msg = f"{session_id}:{epoch}".encode()
    return hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()[:16]


def challenge_token(session_id, epoch=None):
    """현재(또는 지정) epoch 의 챌린지 토큰: '<sid>.<epoch>.<nonce>'."""
    e = _qr_epoch() if epoch is None else epoch
    return f"{session_id}.{e}.{_qr_nonce(session_id, e)}"


def verify_challenge(session_id, token):
    """토큰 유효성: 형식·세션·nonce 일치 + 현재/직전 epoch 만 허용(스캔 지연 대비)."""
    if not token:
        return False
    try:
        sid_s, e_s, nonce = token.split(".")
        if int(sid_s) != session_id:
            return False
        epoch = int(e_s)
    except (ValueError, AttributeError):
        return False
    cur = _qr_epoch()
    if epoch not in (cur, cur - 1):
        return False
    return hmac.compare_digest(nonce, _qr_nonce(session_id, epoch))


# --- 실행 설정 --------------------------------------------------------------
DEBUG = os.environ.get("ATTENDANCE_DEBUG") == "1"
PORT = int(os.environ.get("ATTENDANCE_PORT", "5000"))
# ATTENDANCE_SSL=1 -> adhoc 자체서명 인증서로 HTTPS (개발용; cryptography 필요)
USE_SSL = os.environ.get("ATTENDANCE_SSL") == "1"
# HTTPS 종단(Vercel/리버스프록시) 뒤 → Secure 쿠키 강제. (USE_SSL 이면 자동 포함)
HTTPS = USE_SSL or os.environ.get("ATTENDANCE_HTTPS") == "1"
