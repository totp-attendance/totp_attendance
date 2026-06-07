"""학생 출석 체크 — 개인 TOTP + QR 챌린지(현장) + 레이트리밋."""
import pyotp
from flask import Blueprint, request, render_template, jsonify, abort

import db
import config
from helpers import client_ip, PERSONAL_INTERVAL

bp = Blueprint("checkin", __name__)


def _validate(session_id, s, sid_val, code, ip, challenge):
    """출석 검증. 성공 시 (None, None, final_name), 실패 시 (msg, cls, None)."""
    # 레이트리밋은 학번까지 묶음 — NAT/프록시 뒤 공용 IP 라도 한 명 실패가
    # 반 전체를 잠그지 않게. (학번별 무차별 시도만 제한)
    rl_key = (session_id, ip, sid_val)
    student = db.get_student(sid_val) if sid_val else None

    if not config.ip_allowed(ip):
        return "허용되지 않은 네트워크에서는 출석할 수 없습니다.", "err", None
    if config.is_rate_limited(rl_key):
        return "시도가 너무 많습니다. 잠시 후 다시 시도하세요.", "err", None
    if not s["open"]:
        return "세션이 닫혔습니다.", "err", None
    # 현장 확인: 교실 화면의 QR 을 스캔해 얻은 유효 챌린지 필요 (원격 차단)
    if s["require_qr"] and not config.verify_challenge(session_id, challenge):
        return "교실 화면의 QR 을 스캔해 출석하세요. (QR 만료 시 다시 스캔)", "err", None
    if not sid_val or not code:
        return "모든 항목 입력 필요.", "err", None
    if not student:
        return ("등록되지 않은 학생입니다. 교사에게 개인 TOTP 등록을 요청하세요.",
                "err", None)
    # 개인 secret 으로 코드 검증 (valid_window=1: 시계 오차 대비)
    verifier = pyotp.TOTP(student["secret"], interval=PERSONAL_INTERVAL)
    if not verifier.verify(code, valid_window=1):
        config.record_fail(rl_key)
        return "코드가 틀렸거나 만료됨. 다시 확인.", "err", None

    # 코드 통과 후에만 중복여부 노출 — 코드 모르면 특정 학생 출석여부를
    # 캐낼 수 없음 (출석여부 오라클 차단)
    if db.already_checked(session_id, sid_val):
        return "이미 출석 처리됨.", "ok", None
    return None, None, student["name"]


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
        ip = client_ip()

        msg, cls, final_name = _validate(
            session_id, s, sid_val, code, ip, challenge
        )
        if final_name is not None:  # 검증 통과 → 기록
            ok = db.mark_attendance(session_id, sid_val, final_name, ip=ip)
            config.reset_fails((session_id, ip, sid_val))
            if ok:
                msg, cls = f"출석 완료! ({final_name})", "ok"
                sid_val = ""
            else:
                msg, cls = "이미 출석 처리됨.", "ok"

        # 자동 출석(fetch)은 JSON 응답 → 페이지 이동 없음(루프 방지)
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(msg=msg, cls=cls, ok=(cls == "ok"))

    return render_template("check.html", s=s, msg=msg, cls=cls,
                           sid_val=sid_val, challenge=challenge)
