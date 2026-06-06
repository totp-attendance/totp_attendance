"""학생 출석 체크 — 개인 TOTP + 지오펜스 + 레이트리밋."""
import pyotp
from flask import Blueprint, request, render_template, abort

import db
import config
from helpers import client_ip, parse_float, PERSONAL_INTERVAL

bp = Blueprint("checkin", __name__)


def _validate(session_id, s, sid_val, code, lat, lon, ip, challenge):
    """출석 검증. 성공 시 (None, None, final_name), 실패 시 (msg, cls, None)."""
    rl_key = (session_id, ip)
    student = db.get_student(sid_val) if sid_val else None
    geo_ok, dist = config.within_geofence(s, lat, lon)

    if not config.ip_allowed(ip):
        return "허용되지 않은 네트워크에서는 출석할 수 없습니다.", "err", None
    if config.is_rate_limited(rl_key):
        return "시도가 너무 많습니다. 잠시 후 다시 시도하세요.", "err", None
    if not s["open"]:
        return "세션이 닫혔습니다.", "err", None
    # 현장 확인: 교실 화면의 회전 QR 을 스캔해 얻은 유효 챌린지 필요 (원격 차단)
    if s["require_qr"] and not config.verify_challenge(session_id, challenge):
        return "교실 화면의 QR 을 스캔해 출석하세요. (QR 만료 시 다시 스캔)", "err", None
    if not sid_val or not code:
        return "모든 항목 입력 필요.", "err", None
    if not geo_ok:
        if lat is None or lon is None:
            return "위치 정보가 필요합니다. 위치 권한을 허용하세요.", "err", None
        return f"허용 위치 밖입니다 (약 {int(dist)}m 떨어짐).", "err", None
    if not student:
        return ("등록되지 않은 학생입니다. 교사에게 개인 TOTP 등록을 요청하세요.",
                "err", None)
    if db.already_checked(session_id, sid_val):
        return "이미 출석 처리됨.", "ok", None

    # 개인 secret 으로 코드 검증 (valid_window=1: 시계 오차 대비)
    verifier = pyotp.TOTP(student["secret"], interval=PERSONAL_INTERVAL)
    if verifier.verify(code, valid_window=1):
        return None, None, student["name"]
    config.record_fail(rl_key)
    return "코드가 틀렸거나 만료됨. 다시 확인.", "err", None


@bp.route("/check/<int:session_id>", methods=["GET", "POST"])
def check(session_id):
    s = db.get_session(session_id)
    if not s:
        abort(404)

    msg = cls = ""
    sid_val = ""
    # 챌린지: 스캔하면 GET ?c=, 제출 시 hidden 필드로 전달
    challenge = request.values.get("c", "")

    if request.method == "POST":
        sid_val = request.form.get("student_id", "").strip()
        code = request.form.get("code", "").strip()
        lat = parse_float(request.form.get("lat"))
        lon = parse_float(request.form.get("lon"))
        ip = client_ip()

        msg, cls, final_name = _validate(
            session_id, s, sid_val, code, lat, lon, ip, challenge
        )
        if final_name is not None:  # 검증 통과 → 기록
            ok = db.mark_attendance(session_id, sid_val, final_name,
                                    ip=ip, lat=lat, lon=lon)
            config.reset_fails((session_id, ip))
            if ok:
                msg, cls = f"출석 완료! ({final_name})", "ok"
                sid_val = ""
            else:
                msg, cls = "이미 출석 처리됨.", "ok"

    return render_template("check.html", s=s, msg=msg, cls=cls,
                           sid_val=sid_val, challenge=challenge)
