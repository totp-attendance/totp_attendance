"""학생 개인 TOTP 등록 — 목록·등록·등록QR(기기저장)·삭제."""
from urllib.parse import urlencode

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
    """기기 등록 QR — 학생 폰 카메라로 스캔 시 /setup 열려 브라우저에 secret 저장.
    (Google Authenticator 쓰려면 enroll 페이지의 수동키 입력)"""
    st = db.get_student(student_id)
    if not st:
        abort(404)
    setup_url = url_for("students.setup", _external=True)
    # secret 은 URL fragment(#)로 — 서버/프록시 액세스 로그·브라우저 히스토리에
    # 안 남음 (fragment 는 서버로 전송 안 됨, JS 만 읽음).
    frag = urlencode({"sid": st["student_id"], "name": st["name"],
                      "s": st["secret"]})
    return png_response(f"{setup_url}#{frag}")


@bp.route("/student/<student_id>/otpauth.png")
@require_teacher
def otpauth_png(student_id):
    """Google Authenticator 용 otpauth QR (앱 사용자 폴백)."""
    st = db.get_student(student_id)
    if not st:
        abort(404)
    uri = pyotp.TOTP(st["secret"], interval=PERSONAL_INTERVAL).provisioning_uri(
        name=st["student_id"], issuer_name=config.TOTP_ISSUER
    )
    return png_response(uri)


@bp.route("/setup")
def setup():
    """학생 기기 등록 페이지 (공개). 쿼리의 sid/name/secret 을 JS 가 localStorage 에 저장."""
    return render_template("setup.html")


@bp.route("/student/<student_id>/delete", methods=["POST"])
@require_teacher
def delete(student_id):
    db.delete_student(student_id)
    return redirect(url_for("students.index"))
