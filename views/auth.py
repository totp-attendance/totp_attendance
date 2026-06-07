"""교사 인증 — 로그인 / 로그아웃."""
from flask import (
    Blueprint, request, redirect, url_for, render_template, session,
)

import config
from helpers import client_ip

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    err = ""
    next_url = request.args.get("next") or request.form.get("next") or "/"
    # 오픈 리다이렉트 방지: 내부 경로만 허용
    if not next_url.startswith("/"):
        next_url = "/"
    if request.method == "POST":
        # 비밀번호 무차별 대입 방지: IP 당 슬라이딩 윈도우 레이트리밋
        rl_key = ("login", client_ip())
        if config.is_rate_limited(rl_key):
            err = "로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요."
            return render_template("login.html", err=err, next_url=next_url)
        if config.check_password(request.form.get("password")):
            config.reset_fails(rl_key)
            session["teacher"] = True
            return redirect(next_url)
        config.record_fail(rl_key)
        err = "비밀번호가 틀렸습니다."
    return render_template("login.html", err=err, next_url=next_url)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
