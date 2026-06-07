"""학생 개인 TOTP 등록 — 목록·등록·등록QR(기기저장)·자가등록·삭제."""
import hmac
from urllib.parse import urlencode

import pyotp
from flask import (
    Blueprint, request, redirect, url_for, render_template, abort,
)

import db
import config
from helpers import require_teacher, png_response, client_ip

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
    (브라우저 자체가 인증기. 앱 설치 불필요)"""
    st = db.get_student(student_id)
    if not st:
        abort(404)
    setup_url = url_for("students.setup", _external=True)
    # secret 은 URL fragment(#)로 — 서버/프록시 액세스 로그·브라우저 히스토리에
    # 안 남음 (fragment 는 서버로 전송 안 됨, JS 만 읽음).
    frag = urlencode({"sid": st["student_id"], "name": st["name"],
                      "s": st["secret"]})
    return png_response(f"{setup_url}#{frag}")


@bp.route("/setup")
def setup():
    """학생 기기 등록 페이지 (공개). 쿼리의 sid/name/secret 을 JS 가 localStorage 에 저장."""
    return render_template("setup.html")


@bp.route("/register", methods=["GET", "POST"])
def register():
    """학생 자가등록 (공개). 등록키 필요 + 학번 선점잠금.
    성공 시 그 기기 브라우저에 secret 저장(인증기 됨)."""
    code = db.get_setting("enroll_code") or ""
    enabled = bool(code)
    if not enabled:
        return render_template("register.html", enabled=False)
    if request.method == "POST":
        ip = client_ip()
        rl_key = ("register", ip)
        if config.is_rate_limited(rl_key):
            return render_template("register.html", enabled=True,
                                   err="시도가 너무 많습니다. 잠시 후 다시.")
        sid = request.form.get("student_id", "").strip()
        name = request.form.get("name", "").strip()
        given = request.form.get("code", "")
        if not (sid and name and given):
            return render_template("register.html", enabled=True,
                                   err="모든 항목을 입력하세요.")
        if not hmac.compare_digest(given, code):
            config.record_fail(rl_key)
            return render_template("register.html", enabled=True,
                                   err="등록키가 올바르지 않습니다.")
        if db.get_student(sid):
            return render_template("register.html", enabled=True,
                                   err="이미 등록된 학번입니다. 교사에게 문의하세요.")
        config.reset_fails(rl_key)
        secret, _ = db.upsert_student(sid, name, pyotp.random_base32())
        # 이 기기(요청 보낸 학생 폰)에 바로 저장
        return render_template("register.html", enabled=True, done=True,
                               sid=sid, name=name, secret=secret)
    return render_template("register.html", enabled=True)


@bp.route("/register/qr.png")
def register_qr():
    """자가등록 페이지 QR (공개). 교사가 화면/유인물로 공유."""
    url = url_for("students.register", _external=True)
    return png_response(url)


@bp.route("/student/<student_id>/delete", methods=["POST"])
@require_teacher
def delete(student_id):
    db.delete_student(student_id)
    return redirect(url_for("students.index"))
