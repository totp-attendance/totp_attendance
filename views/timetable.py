"""교수별 시간표(과목) 관리 + 시간표에서 출석 세션 바로 시작."""
from datetime import datetime

import pyotp
from flask import (
    Blueprint, request, redirect, url_for, render_template, abort,
)

import db
from helpers import require_teacher, current_teacher_id

bp = Blueprint("timetable", __name__)

DAYS = ["월", "화", "수", "목", "금", "토", "일"]


def _own_course(course_id):
    c = db.get_course(course_id)
    if not c:
        abort(404)
    if c.get("owner_id") != current_teacher_id():
        abort(404)
    return c


def _parse_day(v):
    try:
        d = int(v)
        return d if 0 <= d <= 6 else 0
    except (TypeError, ValueError):
        return 0


def _hm(h, m):
    """시·분 드롭다운 값 → 'HH:MM' (둘 다 있어야, 없으면 '')."""
    h = (h or "").strip()
    m = (m or "").strip()
    if h.isdigit() and m.isdigit():
        return "%02d:%02d" % (int(h), int(m))
    return ""


@bp.route("/timetable")
@require_teacher
def index():
    me = current_teacher_id()
    courses = db.list_courses(me)
    today = datetime.now().weekday()           # 월=0 .. 일=6
    today_courses = [c for c in courses if c["day"] == today]
    return render_template("timetable.html", courses=courses, days=DAYS,
                           today=today, today_courses=today_courses)


@bp.route("/timetable/add", methods=["POST"])
@require_teacher
def add():
    name = request.form.get("name", "").strip()
    if not name:
        abort(400, "과목명 필요")
    db.create_course(current_teacher_id(), name,
                     _parse_day(request.form.get("day")),
                     _hm(request.form.get("start_h"), request.form.get("start_m")),
                     _hm(request.form.get("end_h"), request.form.get("end_m")),
                     request.form.get("room", "").strip())
    return redirect(url_for("timetable.index"))


@bp.route("/timetable/<int:course_id>/edit", methods=["POST"])
@require_teacher
def edit(course_id):
    _own_course(course_id)
    name = request.form.get("name", "").strip()
    if not name:
        abort(400, "과목명 필요")
    db.update_course(course_id, name,
                     _parse_day(request.form.get("day")),
                     _hm(request.form.get("start_h"), request.form.get("start_m")),
                     _hm(request.form.get("end_h"), request.form.get("end_m")),
                     request.form.get("room", "").strip())
    return redirect(url_for("timetable.index"))


@bp.route("/timetable/<int:course_id>/delete", methods=["POST"])
@require_teacher
def delete(course_id):
    _own_course(course_id)
    db.delete_course(course_id)
    return redirect(url_for("timetable.index"))


@bp.route("/timetable/<int:course_id>/start", methods=["POST"])
@require_teacher
def start(course_id):
    """과목에서 오늘자 출석 세션 시작 (이미 있으면 그 세션으로 이동)."""
    c = _own_course(course_id)
    today = datetime.now().strftime("%Y-%m-%d")
    name = f"{c['name']} {today}"
    existing = db.get_course_session(course_id, name)
    if existing:
        sid = existing["id"]
    else:
        sid = db.create_session(name, pyotp.random_base32(), require_qr=1,
                                owner_id=current_teacher_id(), course_id=course_id)
    return redirect(url_for("sessions.teacher", session_id=sid))
