"""학생 개인 TOTP 등록 — 목록·등록·등록QR·삭제."""
import pyotp
from flask import (
    Blueprint, request, redirect, url_for, render_template, abort,
)

import db
import config
from helpers import require_teacher, png_response, PERSONAL_INTERVAL

bp = Blueprint("students", __name__)


@bp.route("/students")
@require_teacher
def index():
    return render_template("students.html", students=db.list_students())


@bp.route("/enroll", methods=["POST"])
@require_teacher
def enroll():
    student_id = request.form.get("student_id", "").strip()
    name = request.form.get("name", "").strip()
    if not (student_id and name):
        abort(400, "학번·이름 필요")
    _, is_new = db.upsert_student(student_id, name, pyotp.random_base32())
    st = db.get_student(student_id)
    return render_template("enroll_qr.html", st=st, is_new=is_new)


@bp.route("/student/<student_id>/qr")
@require_teacher
def qr(student_id):
    st = db.get_student(student_id)
    if not st:
        abort(404)
    return render_template("enroll_qr.html", st=st, is_new=False)


@bp.route("/student/<student_id>/qr.png")
@require_teacher
def qr_png(student_id):
    st = db.get_student(student_id)
    if not st:
        abort(404)
    uri = pyotp.TOTP(st["secret"], interval=PERSONAL_INTERVAL).provisioning_uri(
        name=st["student_id"], issuer_name=config.TOTP_ISSUER
    )
    return png_response(uri)


@bp.route("/student/<student_id>/delete", methods=["POST"])
@require_teacher
def delete(student_id):
    db.delete_student(student_id)
    return redirect(url_for("students.index"))
