"""자동 테스트. 실행: pytest -v

격리: 테스트마다 임시 암호화 DB 사용 (실제 attendance.db 안 건드림).
방식: 개인 TOTP 단일 — 학생 등록 후 본인 코드로 출석.
"""
import pytest
import pyotp

import db
import config
import app as appmod

PW = "test-pw"


@pytest.fixture(autouse=True)
def temp_db(tmp_path):
    """각 테스트마다 새 임시 DB + 고정 교사 비번 + 상태 초기화."""
    db.DB_PATH = str(tmp_path / "test.db")
    db.init_db()
    config.TEACHER_PASSWORD = PW
    config._fail_log.clear()
    config.ALLOWED_SUBNETS = []
    yield


@pytest.fixture
def client():
    return appmod.app.test_client()


@pytest.fixture
def teacher(client):
    """로그인된 교사 클라이언트."""
    client.post("/login", data={"password": PW})
    return client


def make_session(teacher, **form):
    form.setdefault("name", "수업")
    r = teacher.post("/create", data=form)
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


def enroll(teacher, student_id, name):
    """학생 등록 후 개인 secret 반환."""
    teacher.post("/enroll", data={"student_id": student_id, "name": name})
    return db.get_student(student_id)["secret"]


def code_of(secret):
    """개인 secret 의 현재 TOTP 코드 (표준 30초)."""
    return pyotp.TOTP(secret, interval=30).now()


# --- 인증 -------------------------------------------------------------------
def test_teacher_routes_require_login(client):
    for path in ["/", "/teacher/1", "/api/code/1", "/roster/1",
                 "/export/1.csv", "/students"]:
        r = client.get(path)
        assert r.status_code in (302, 308)
        assert "login" in r.headers.get("Location", "")


def test_wrong_password_rejected(client):
    r = client.post("/login", data={"password": "nope"})
    assert "틀렸" in r.data.decode()


def test_login_success(client):
    r = client.post("/login", data={"password": PW})
    assert r.status_code == 302


# --- 개인 TOTP 출석 ---------------------------------------------------------
def test_checkin_success(teacher):
    secret = enroll(teacher, "s1", "학생1")
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    r = pub.post(f"/check/{sid}", data={"student_id": "s1", "code": code_of(secret)})
    body = r.data.decode()
    assert "출석 완료" in body and "학생1" in body  # 이름 자동
    rows = db.list_attendance(sid)
    assert len(rows) == 1 and rows[0]["ip"]  # 감사 IP 기록


def test_duplicate_blocked(teacher):
    secret = enroll(teacher, "s1", "학생1")
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    d = {"student_id": "s1", "code": code_of(secret)}
    pub.post(f"/check/{sid}", data=d)
    r = pub.post(f"/check/{sid}", data=d)
    assert "이미 출석" in r.data.decode()


def test_wrong_code_rejected(teacher):
    enroll(teacher, "s1", "학생1")
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    r = pub.post(f"/check/{sid}", data={"student_id": "s1", "code": "000000"})
    assert "틀렸거나" in r.data.decode()


def test_attendance_oracle_blocked(teacher):
    """코드 모르면 학생 출석여부 못 캐냄 — 중복검사가 코드검증보다 뒤."""
    secret = enroll(teacher, "s1", "학생1")
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    pub.post(f"/check/{sid}", data={"student_id": "s1", "code": code_of(secret)})
    # 이미 출석한 학생을 '틀린 코드'로 찔러봄 → '이미 출석' 노출 금지, 코드오류 반환
    r = pub.post(f"/check/{sid}", data={"student_id": "s1", "code": "000000"})
    body = r.data.decode()
    assert "이미 출석" not in body and "틀렸거나" in body


def test_unenrolled_rejected(teacher):
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    r = pub.post(f"/check/{sid}", data={"student_id": "ghost", "code": "123456"})
    assert "등록되지 않은" in r.data.decode()


def test_closed_session_rejected(teacher):
    secret = enroll(teacher, "s1", "학생1")
    sid = make_session(teacher)
    teacher.post(f"/toggle/{sid}")
    pub = appmod.app.test_client()
    r = pub.post(f"/check/{sid}", data={"student_id": "s1", "code": code_of(secret)})
    assert "닫혔" in r.data.decode()


# --- 학생 등록 --------------------------------------------------------------
def test_enroll_idempotent_secret(teacher):
    teacher.post("/enroll", data={"student_id": "s2", "name": "이름1"})
    secret1 = db.get_student("s2")["secret"]
    teacher.post("/enroll", data={"student_id": "s2", "name": "이름2"})
    s = db.get_student("s2")
    assert s["secret"] == secret1 and s["name"] == "이름2"  # 키 유지, 이름 갱신


def test_enroll_qr_png(teacher):
    enroll(teacher, "s1", "학생1")
    r = teacher.get("/student/s1/qr.png")
    assert r.status_code == 200 and r.mimetype == "image/png"


# --- 기기 등록(setup) + 자동출석(AJAX) --------------------------------------
def test_setup_page_public(client):
    # 학생 기기 등록 페이지는 로그인 없이 열림 (JS 가 secret 저장)
    r = client.get("/setup?sid=1&name=A&s=ABCD")
    assert r.status_code == 200


def test_ajax_checkin_returns_json(teacher):
    secret = enroll(teacher, "s1", "학생1")
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    r = pub.post(f"/check/{sid}",
                 data={"student_id": "s1", "code": code_of(secret)},
                 headers={"X-Requested-With": "fetch"})
    assert r.is_json
    body = r.get_json()
    assert body["ok"] is True and "출석 완료" in body["msg"]


def test_ajax_wrong_code_json_not_ok(teacher):
    enroll(teacher, "s1", "학생1")
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    r = pub.post(f"/check/{sid}",
                 data={"student_id": "s1", "code": "000000"},
                 headers={"X-Requested-With": "fetch"})
    assert r.is_json and r.get_json()["ok"] is False


# --- 지오펜스 ---------------------------------------------------------------
def test_geofence_within_and_outside(teacher):
    secret = enroll(teacher, "g1", "X")
    secret2 = enroll(teacher, "g2", "Y")
    sid = make_session(teacher, geo_lat="37.5665", geo_lon="126.9780",
                       geo_radius="100")
    pub = appmod.app.test_client()
    # 반경 내
    r = pub.post(f"/check/{sid}", data={"student_id": "g1", "code": code_of(secret),
                 "lat": "37.5665", "lon": "126.9780"})
    assert "출석 완료" in r.data.decode()
    # 반경 밖 (~1.5km)
    r = pub.post(f"/check/{sid}", data={"student_id": "g2", "code": code_of(secret2),
                 "lat": "37.5800", "lon": "126.9780"})
    assert "허용 위치 밖" in r.data.decode()


def test_geofence_missing_location(teacher):
    secret = enroll(teacher, "g3", "Z")
    sid = make_session(teacher, geo_lat="37.5", geo_lon="127.0", geo_radius="50")
    pub = appmod.app.test_client()
    r = pub.post(f"/check/{sid}", data={"student_id": "g3", "code": code_of(secret)})
    assert "위치 정보가 필요" in r.data.decode()


# --- 회전 QR 챌린지 (현장 확인) ---------------------------------------------
def test_qr_required_blocks_without_challenge(teacher):
    secret = enroll(teacher, "s1", "학생1")
    sid = make_session(teacher, require_qr="1")
    pub = appmod.app.test_client()
    # 챌린지(c) 없이 제출 → 차단
    r = pub.post(f"/check/{sid}", data={"student_id": "s1", "code": code_of(secret)})
    assert "QR" in r.data.decode()


def test_qr_valid_challenge_passes(teacher):
    secret = enroll(teacher, "s1", "학생1")
    sid = make_session(teacher, require_qr="1")
    token = config.challenge_token(sid)  # 현재 화면 챌린지 모사
    pub = appmod.app.test_client()
    r = pub.post(f"/check/{sid}",
                 data={"student_id": "s1", "code": code_of(secret), "c": token})
    assert "출석 완료" in r.data.decode()


def test_qr_invalid_challenge_rejected(teacher):
    secret = enroll(teacher, "s1", "학생1")
    sid = make_session(teacher, require_qr="1")
    pub = appmod.app.test_client()
    r = pub.post(f"/check/{sid}",
                 data={"student_id": "s1", "code": code_of(secret),
                       "c": f"{sid}.999.deadbeefdeadbeef"})  # 위조 nonce/만료 epoch
    assert "QR" in r.data.decode()


def test_challenge_verify_unit():
    assert config.verify_challenge(7, config.challenge_token(7)) is True
    assert config.verify_challenge(7, "7.0.0000000000000000") is False  # 옛 epoch
    assert config.verify_challenge(7, config.challenge_token(8)) is False  # 다른 세션
    assert config.verify_challenge(7, "garbage") is False


def test_qr_challenge_png(teacher):
    sid = make_session(teacher, require_qr="1")
    r = teacher.get(f"/qrc/{sid}")
    assert r.status_code == 200 and r.mimetype == "image/png"


def test_qrc_requires_login(client):
    r = client.get("/qrc/1")
    assert r.status_code in (302, 308) and "login" in r.headers.get("Location", "")


# --- 레이트리밋 -------------------------------------------------------------
def test_rate_limit(teacher):
    enroll(teacher, "s1", "학생1")
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    blocked = False
    for i in range(config.RATE_MAX_FAILS + 2):
        r = pub.post(f"/check/{sid}", data={"student_id": "s1", "code": "000000"})
        if "너무 많습니다" in r.data.decode():
            blocked = True
            break
    assert blocked


# --- CSV --------------------------------------------------------------------
def test_csv_export_bom(teacher):
    secret = enroll(teacher, "s1", "홍길동")
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    pub.post(f"/check/{sid}", data={"student_id": "s1", "code": code_of(secret)})
    r = teacher.get(f"/export/{sid}.csv")
    assert r.data.startswith(b"\xef\xbb\xbf")  # 엑셀 BOM
    assert "홍길동" in r.data.decode("utf-8-sig")


def test_csv_formula_injection_neutralized(teacher):
    """이름이 '='로 시작해도 엑셀 수식 실행 안 되게 작은따옴표 prepend."""
    secret = enroll(teacher, "s1", "=1+2")
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    pub.post(f"/check/{sid}", data={"student_id": "s1", "code": code_of(secret)})
    r = teacher.get(f"/export/{sid}.csv")
    text = r.data.decode("utf-8-sig")
    assert "'=1+2" in text  # 무력화됨
    assert ",=1+2" not in text  # 원본 수식 그대로 안 나감


# --- 암호화 -----------------------------------------------------------------
def test_db_is_encrypted():
    import sqlite3
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(db.DB_PATH).execute("SELECT * FROM sessions").fetchone()


# --- haversine --------------------------------------------------------------
def test_haversine():
    assert config.haversine_m(37.5, 127.0, 37.5, 127.0) < 0.01
    d = config.haversine_m(37.5665, 126.9780, 37.5755, 126.9780)  # ~1km
    assert 900 < d < 1100
