"""TOTP 기반 출석 웹 앱 — 앱 팩토리 + 블루프린트 등록.

구조:
  config.py        환경변수 설정 + 보안 헬퍼
  db.py            SQLCipher 암호화 저장소
  helpers.py       뷰 공통 유틸 (인증·요청파싱·TOTP·QR)
  views/auth       로그인/로그아웃
  views/sessions   세션 생성·QR 화면·코드·출석부·삭제·CSV
  views/students   개인 TOTP 등록
  views/checkin    학생 출석
  templates/       Jinja 템플릿
"""
from flask import Flask

import config
import db
from views.auth import bp as auth_bp
from views.sessions import bp as sessions_bp
from views.students import bp as students_bp
from views.checkin import bp as checkin_bp
from views.admin import bp as admin_bp
from views.timetable import bp as timetable_bp


def bootstrap_admin():
    """교사 계정이 하나도 없으면 첫 관리자를 시드하고, 소유자 없는 기존
    세션을 그 관리자 소유로 백필 (구버전 DB 업그레이드 1회)."""
    if db.count_teachers() == 0:
        admin_id = db.create_teacher(
            config.ADMIN_USER, config.hash_pw(config.ADMIN_PASSWORD), is_admin=1
        )
        db.backfill_session_owner(admin_id)


def create_app():
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY
    # CSRF 방어: 세션쿠키를 교차사이트 요청에 안 실어보냄(SameSite=Strict)
    # → 악성사이트가 교사 세션으로 세션생성·학생삭제 강제 불가.
    # HttpOnly: JS 로 쿠키 못 읽음(XSS 쿠키탈취 방지). HTTPS 면 Secure 까지.
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=config.HTTPS,
    )
    db.init_db()
    bootstrap_admin()
    app.register_blueprint(auth_bp)
    app.register_blueprint(sessions_bp)
    app.register_blueprint(students_bp)
    app.register_blueprint(checkin_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(timetable_bp)
    return app


app = create_app()


if __name__ == "__main__":
    # 0.0.0.0: 같은 와이파이의 학생 폰에서 접속 가능
    # debug 는 ATTENDANCE_DEBUG=1 일 때만 (운영 기본 off)
    # ATTENDANCE_SSL=1: adhoc 자체서명 HTTPS (브라우저 위치/카메라는 보안컨텍스트 필요)
    ssl_ctx = "adhoc" if config.USE_SSL else None
    app.run(host="0.0.0.0", port=config.PORT, debug=config.DEBUG,
            ssl_context=ssl_ctx)
