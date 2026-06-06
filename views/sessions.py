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
from helpers import require_teacher, png_response, parse_float

bp = Blueprint("sessions", __name__)


@bp.route("/")
@require_teacher
def index():
    return render_template("index.html", sessions=db.list_sessions())


@bp.route("/create", methods=["POST"])
@require_teacher
def create():
    name = request.form.get("name", "").strip()
    if not name:
        abort(400, "이름 필요")
    geo_lat = parse_float(request.form.get("geo_lat"))
    geo_lon = parse_float(request.form.get("geo_lon"))
    geo_radius = request.form.get("geo_radius", "").strip()
    geo_radius = int(geo_radius) if geo_radius.isdigit() else None
    # 지오펜스는 위경도+반경 모두 있을 때만 적용
    if geo_lat is None or geo_lon is None or not geo_radius:
        geo_lat = geo_lon = geo_radius = None
    require_qr = 1 if request.form.get("require_qr") else 0
    # 개인 TOTP 방식만 사용. secret 컬럼은 NOT NULL 충족용(미사용).
    sid = db.create_session(name, pyotp.random_base32(), 30, "personal",
                            geo_lat, geo_lon, geo_radius, require_qr)
    return redirect(url_for("sessions.teacher", session_id=sid))


@bp.route("/teacher/<int:session_id>")
@require_teacher
def teacher(session_id):
    s = db.get_session(session_id)
    if not s:
        abort(404)
    check_url = url_for("checkin.check", session_id=session_id, _external=True)
    return render_template("teacher.html", s=s, check_url=check_url,
                           qr_rotate=config.QR_ROTATE_SEC)


@bp.route("/api/code/<int:session_id>")
@require_teacher
def api_code(session_id):
    # 개인 TOTP 방식: 회전 코드 없음. 실시간 출석수만 제공.
    s = db.get_session(session_id)
    if not s:
        abort(404)
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
    """회전 QR — 현재 챌린지를 담은 출석 URL의 QR PNG.
    교사 화면(인증됨)에서만 가져올 수 있어 외부서 챌린지 못 빼감."""
    s = db.get_session(session_id)
    if not s:
        abort(404)
    token = config.challenge_token(session_id)
    url = url_for("checkin.check", session_id=session_id, _external=True)
    return png_response(f"{url}?c={token}")


@bp.route("/roster/<int:session_id>")
@require_teacher
def roster(session_id):
    s = db.get_session(session_id)
    if not s:
        abort(404)
    return render_template("roster.html", s=s,
                           rows=db.list_attendance(session_id))


@bp.route("/toggle/<int:session_id>", methods=["POST"])
@require_teacher
def toggle(session_id):
    s = db.get_session(session_id)
    if not s:
        abort(404)
    db.set_session_open(session_id, not s["open"])
    return redirect(url_for("sessions.roster", session_id=session_id))


@bp.route("/export/<int:session_id>.csv")
@require_teacher
def export_csv(session_id):
    s = db.get_session(session_id)
    if not s:
        abort(404)
    rows = db.list_attendance(session_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["학번", "이름", "시각", "IP", "위도", "경도"])
    for r in rows:
        w.writerow([r["student_id"], r["student_name"], r["checked_at"],
                    r.get("ip") or "", r.get("lat") or "", r.get("lon") or ""])
    # 엑셀 한글 깨짐 방지 BOM
    data = "﻿" + buf.getvalue()
    return Response(
        data,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=attendance_{session_id}.csv"
        },
    )
