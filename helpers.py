"""뷰 공통 유틸 — 인증 데코레이터, 요청 파싱, TOTP, QR 응답."""
import io
from functools import wraps

import qrcode
from flask import request, redirect, url_for, session, Response

# 개인 TOTP 표준 주기 (브라우저 인증기 = attendance.js 와 동일, 30초)
PERSONAL_INTERVAL = 30


def require_teacher(view):
    """교사 로그인 안 됐으면 로그인 페이지로 리다이렉트."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("teacher"):
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def client_ip():
    """프록시 뒤면 X-Forwarded-For 첫 IP, 아니면 remote_addr."""
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
