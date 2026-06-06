"""교사 인증 — 로그인 / 로그아웃."""
from flask import (
    Blueprint, request, redirect, url_for, render_template, session,
)

import config

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    err = ""
    next_url = request.args.get("next") or request.form.get("next") or "/"
    # 오픈 리다이렉트 방지: 내부 경로만 허용
    if not next_url.startswith("/"):
        next_url = "/"
    if request.method == "POST":
        if config.check_password(request.form.get("password")):
            session["teacher"] = True
            return redirect(next_url)
        err = "비밀번호가 틀렸습니다."
    return render_template("login.html", err=err, next_url=next_url)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
