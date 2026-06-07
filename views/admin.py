"""교사 계정 관리 — 관리자 전용 (목록·생성·삭제·비번 초기화)."""
from flask import Blueprint, request, render_template, abort

import db
import config
from helpers import require_admin, current_teacher_id

bp = Blueprint("admin", __name__)


def _render(err="", msg=""):
    return render_template("admin.html", teachers=db.list_teachers(),
                           err=err, msg=msg, me=current_teacher_id())


@bp.route("/admin")
@require_admin
def index():
    return _render()


@bp.route("/admin/create", methods=["POST"])
@require_admin
def create():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    is_admin = 1 if request.form.get("is_admin") else 0
    if not username or not password:
        return _render(err="아이디·비밀번호를 입력하세요.")
    if len(password) < 4:
        return _render(err="비밀번호는 4자 이상이어야 합니다.")
    tid = db.create_teacher(username, config.hash_pw(password), is_admin)
    if tid is None:
        return _render(err=f"이미 존재하는 아이디입니다: {username}")
    return _render(msg=f"계정 생성됨: {username}")


@bp.route("/admin/<int:teacher_id>/delete", methods=["POST"])
@require_admin
def delete(teacher_id):
    t = db.get_teacher(teacher_id)
    if not t:
        abort(404)
    if teacher_id == current_teacher_id():
        return _render(err="본인 계정은 삭제할 수 없습니다.")
    if t["is_admin"] and db.count_admins() <= 1:
        return _render(err="마지막 관리자는 삭제할 수 없습니다.")
    # 삭제 교사의 세션은 작업 관리자에게 인계 (출석 데이터 보존)
    db.reassign_sessions(teacher_id, current_teacher_id())
    db.delete_teacher(teacher_id)
    return _render(msg=f"계정 삭제됨: {t['username']} (세션은 본인에게 인계)")


@bp.route("/admin/<int:teacher_id>/reset", methods=["POST"])
@require_admin
def reset(teacher_id):
    t = db.get_teacher(teacher_id)
    if not t:
        abort(404)
    password = request.form.get("password", "")
    if len(password) < 4:
        return _render(err="비밀번호는 4자 이상이어야 합니다.")
    db.set_teacher_password(teacher_id, config.hash_pw(password))
    return _render(msg=f"비밀번호 초기화됨: {t['username']}")
