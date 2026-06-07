"""세션(수업) — 목록·생성·교사화면·코드 API·QR·출석부·열닫기·CSV."""
import io
import csv

import pyotp
from flask import (
    Blueprint, request, redirect, url_for, render_template,
    jsonify, Response, abort,
)

import db
import config
from helpers import (require_teacher, png_response, current_teacher_id, is_admin)

bp = Blueprint("sessions", __name__)


def _csv_safe(v):
    """엑셀 수식 인젝션 방지 — 위험 문자로 시작하면 작은따옴표 prepend."""
    s = "" if v is None else str(v)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def _owned_session(session_id):
    """세션 조회 + 소유권 확인. 본인 것 아니고 관리자도 아니면 404
    (존재 여부 노출 방지)."""
    s = db.get_session(session_id)
    if not s:
        abort(404)
    if not is_admin() and s.get("owner_id") != current_teacher_id():
        abort(404)
    return s


@bp.route("/")
@require_teacher
def index():
    # 관리자는 전체, 일반 교사는 본인 세션만
    owner = None if is_admin() else current_teacher_id()
    return render_template("index.html", sessions=db.list_sessions(owner),
                           is_admin=is_admin())


@bp.route("/create", methods=["POST"])
@require_teacher
def create():
    name = request.form.get("name", "").strip()
    if not name:
        abort(400, "이름 필요")
    # 현장 확인(QR 챌린지)은 항상 적용 — 원격 출석 차단 (옵션 아님).
    sid = db.create_session(name, pyotp.random_base32(), 30, "personal",
                            require_qr=1, owner_id=current_teacher_id())
    return redirect(url_for("sessions.teacher", session_id=sid))


@bp.route("/teacher/<int:session_id>")
@require_teacher
def teacher(session_id):
    s = _owned_session(session_id)
    check_url = url_for("checkin.check", session_id=session_id, _external=True)
    return render_template("teacher.html", s=s, check_url=check_url,
                           qr_rotate=config.QR_ROTATE_SEC)


@bp.route("/api/code/<int:session_id>")
@require_teacher
def api_code(session_id):
    # 개인 TOTP 방식: 코드 없음. 실시간 출석수만 제공.
    _owned_session(session_id)
    return jsonify(count=len(db.list_attendance(session_id)))


@bp.route("/qr/<int:session_id>.png")
@bp.route("/qr/<int:session_id>")
def qr(session_id):
    s = db.get_session(session_id)
    if not s:
        abort(404)
    url = url_for("checkin.check", session_id=session_id, _external=True)
    return png_response(url)


@bp.route("/qrc/<int:session_id>")
@require_teacher
def qr_challenge(session_id):
    """QR — 현재 챌린지를 담은 출석 URL의 QR PNG.
    교사 화면(인증됨)에서만 가져올 수 있어 외부서 챌린지 못 빼감."""
    _owned_session(session_id)
    token = config.challenge_token(session_id)
    url = url_for("checkin.check", session_id=session_id, _external=True)
    return png_response(f"{url}?c={token}")


@bp.route("/roster/<int:session_id>")
@require_teacher
def roster(session_id):
    s = _owned_session(session_id)
    return render_template("roster.html", s=s,
                           rows=db.list_attendance(session_id))


@bp.route("/toggle/<int:session_id>", methods=["POST"])
@require_teacher
def toggle(session_id):
    s = _owned_session(session_id)
    db.set_session_open(session_id, not s["open"])
    return redirect(url_for("sessions.roster", session_id=session_id))


@bp.route("/export/<int:session_id>.csv")
@require_teacher
def export_csv(session_id):
    _owned_session(session_id)
    rows = db.list_attendance(session_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["학번", "이름", "시각", "IP"])
    for r in rows:
        w.writerow([_csv_safe(r["student_id"]), _csv_safe(r["student_name"]),
                    r["checked_at"], r.get("ip") or ""])
    # 엑셀 한글 깨짐 방지 BOM
    data = "﻿" + buf.getvalue()
    return Response(
        data,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=attendance_{session_id}.csv"
        },
    )
