"""뷰 공통 유틸 — 인증 데코레이터, 요청 파싱, TOTP, QR 응답."""
import io
from functools import wraps

import qrcode
from flask import request, redirect, url_for, session, Response, abort

import config

# 개인 TOTP 표준 주기 (브라우저 인증기 = attendance.js 와 동일, 30초)
PERSONAL_INTERVAL = 30


def current_teacher_id():
    return session.get("teacher_id")


def is_admin():
    return bool(session.get("is_admin"))


def require_teacher(view):
    """교사 로그인 안 됐으면 로그인 페이지로 리다이렉트."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("teacher_id"):
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def require_admin(view):
    """관리자만 허용. 비로그인 → 로그인, 로그인했지만 비관리자 → 403."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("teacher_id"):
            return redirect(url_for("auth.login", next=request.path))
        if not session.get("is_admin"):
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def client_ip():
    """클라 IP. 기본은 remote_addr 만 신뢰.
    X-Forwarded-For 는 클라가 위조 가능하므로 TRUST_PROXY=1 (신뢰 리버스프록시
    뒤) 일 때만 첫 hop 을 사용. 아니면 무시 → IP 제한·레이트리밋·감사 위조 방지."""
    if config.TRUST_PROXY:
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.remote_addr or ""


def parse_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def png_response(data):
    """QR 등 PNG 바이트를 image/png 응답으로."""
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="image/png")
