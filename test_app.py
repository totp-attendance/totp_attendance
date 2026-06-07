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
    """각 테스트마다 새 임시 DB + admin 교사 시드 + 상태 초기화."""
    db.DB_PATH = str(tmp_path / "test.db")
    db.init_db()
    db.create_teacher("admin", config.hash_pw(PW), is_admin=1)  # 관리자 시드
    config._fail_log.clear()
    config.ALLOWED_SUBNETS = []
    config.TRUST_PROXY = False
    yield


@pytest.fixture
def client():
    return appmod.app.test_client()


def login(client, username="admin", password=PW):
    return client.post("/login", data={"username": username, "password": password})


@pytest.fixture
def teacher(client):
    """로그인된 관리자 교사 클라이언트."""
    login(client)
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
    r = client.post("/login", data={"username": "admin", "password": "nope"})
    assert "틀렸" in r.data.decode()


def test_login_success(client):
    r = login(client)
    assert r.status_code == 302


def test_unknown_user_rejected(client):
    r = client.post("/login", data={"username": "ghost", "password": PW})
    assert "틀렸" in r.data.decode()


def test_login_rate_limit(client):
    """교사 로그인 무차별 대입 → 레이트리밋. 한도 후 올바른 비번도 거부."""
    for _ in range(config.RATE_MAX_FAILS):
        client.post("/login", data={"password": "wrong"})
    r = client.post("/login", data={"password": "wrong"})
    assert "너무 많습니다" in r.data.decode()
    r2 = client.post("/login", data={"password": PW})  # 잠긴 동안엔 정답도 막힘
    assert "너무 많습니다" in r2.data.decode()


# --- X-Forwarded-For 신뢰 게이팅 --------------------------------------------
def test_xff_spoof_ignored_by_default(teacher):
    """TRUST_PROXY off: 위조 XFF 무시 → remote_addr 기준 IP 제한 적용."""
    secret = enroll(teacher, "s1", "학생1")
    sid = make_session(teacher)
    config.ALLOWED_SUBNETS = ["10.0."]
    pub = appmod.app.test_client()
    r = pub.post(f"/check/{sid}",
                 data={"student_id": "s1", "code": code_of(secret),
                       "c": config.challenge_token(sid)},
                 headers={"X-Forwarded-For": "10.0.0.5"})  # 위조 시도
    assert "허용되지 않은 네트워크" in r.data.decode()


def test_xff_trusted_when_proxy_enabled(teacher):
    """TRUST_PROXY on: 신뢰 프록시 뒤 → XFF 첫 hop 사용."""
    secret = enroll(teacher, "s1", "학생1")
    sid = make_session(teacher)
    config.ALLOWED_SUBNETS = ["10.0."]
    config.TRUST_PROXY = True
    pub = appmod.app.test_client()
    r = pub.post(f"/check/{sid}",
                 data={"student_id": "s1", "code": code_of(secret),
                       "c": config.challenge_token(sid)},
                 headers={"X-Forwarded-For": "10.0.0.5"})
    assert "출석 완료" in r.data.decode()


# --- 개인 TOTP 출석 ---------------------------------------------------------
def test_checkin_success(teacher):
    secret = enroll(teacher, "s1", "학생1")
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    r = pub.post(f"/check/{sid}", data={"student_id": "s1", "code": code_of(secret), "c": config.challenge_token(sid)})
    body = r.data.decode()
    assert "출석 완료" in body and "학생1" in body  # 이름 자동
    rows = db.list_attendance(sid)
    assert len(rows) == 1 and rows[0]["ip"]  # 감사 IP 기록


def test_duplicate_blocked(teacher):
    secret = enroll(teacher, "s1", "학생1")
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    d = {"student_id": "s1", "code": code_of(secret), "c": config.challenge_token(sid)}
    pub.post(f"/check/{sid}", data=d)
    r = pub.post(f"/check/{sid}", data=d)
    assert "이미 출석" in r.data.decode()


def test_wrong_code_rejected(teacher):
    enroll(teacher, "s1", "학생1")
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    r = pub.post(f"/check/{sid}", data={"student_id": "s1", "code": "000000", "c": config.challenge_token(sid)})
    assert "틀렸거나" in r.data.decode()


def test_attendance_oracle_blocked(teacher):
    """코드 모르면 학생 출석여부 못 캐냄 — 중복검사가 코드검증보다 뒤."""
    secret = enroll(teacher, "s1", "학생1")
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    pub.post(f"/check/{sid}", data={"student_id": "s1", "code": code_of(secret), "c": config.challenge_token(sid)})
    # 이미 출석한 학생을 '틀린 코드'로 찔러봄 → '이미 출석' 노출 금지, 코드오류 반환
    r = pub.post(f"/check/{sid}", data={"student_id": "s1", "code": "000000", "c": config.challenge_token(sid)})
    body = r.data.decode()
    assert "이미 출석" not in body and "틀렸거나" in body


def test_unenrolled_rejected(teacher):
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    r = pub.post(f"/check/{sid}", data={"student_id": "ghost", "code": "123456", "c": config.challenge_token(sid)})
    assert "등록되지 않은" in r.data.decode()


def test_closed_session_rejected(teacher):
    secret = enroll(teacher, "s1", "학생1")
    sid = make_session(teacher)
    teacher.post(f"/toggle/{sid}")
    pub = appmod.app.test_client()
    r = pub.post(f"/check/{sid}", data={"student_id": "s1", "code": code_of(secret), "c": config.challenge_token(sid)})
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
                 data={"student_id": "s1", "code": code_of(secret),
                       "c": config.challenge_token(sid)},
                 headers={"X-Requested-With": "fetch"})
    assert r.is_json
    body = r.get_json()
    assert body["ok"] is True and "출석 완료" in body["msg"]


def test_ajax_wrong_code_json_not_ok(teacher):
    enroll(teacher, "s1", "학생1")
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    r = pub.post(f"/check/{sid}",
                 data={"student_id": "s1", "code": "000000",
                       "c": config.challenge_token(sid)},
                 headers={"X-Requested-With": "fetch"})
    assert r.is_json and r.get_json()["ok"] is False


# --- QR 챌린지 (현장 확인) --------------------------------------------------
def test_qr_required_blocks_without_challenge(teacher):
    secret = enroll(teacher, "s1", "학생1")
    sid = make_session(teacher, require_qr="1")
    pub = appmod.app.test_client()
    # 챌린지(c) 없이 제출 → 차단
    r = pub.post(f"/check/{sid}", data={"student_id": "s1", "code": code_of(secret), "c": config.challenge_token(sid)})
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
        r = pub.post(f"/check/{sid}", data={"student_id": "s1", "code": "000000", "c": config.challenge_token(sid)})
        if "너무 많습니다" in r.data.decode():
            blocked = True
            break
    assert blocked


# --- CSV --------------------------------------------------------------------
def test_csv_export_bom(teacher):
    secret = enroll(teacher, "s1", "홍길동")
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    pub.post(f"/check/{sid}", data={"student_id": "s1", "code": code_of(secret), "c": config.challenge_token(sid)})
    r = teacher.get(f"/export/{sid}.csv")
    assert r.data.startswith(b"\xef\xbb\xbf")  # 엑셀 BOM
    assert "홍길동" in r.data.decode("utf-8-sig")


def test_csv_formula_injection_neutralized(teacher):
    """이름이 '='로 시작해도 엑셀 수식 실행 안 되게 작은따옴표 prepend."""
    secret = enroll(teacher, "s1", "=1+2")
    sid = make_session(teacher)
    pub = appmod.app.test_client()
    pub.post(f"/check/{sid}", data={"student_id": "s1", "code": code_of(secret), "c": config.challenge_token(sid)})
    r = teacher.get(f"/export/{sid}.csv")
    text = r.data.decode("utf-8-sig")
    assert "'=1+2" in text  # 무력화됨
    assert ",=1+2" not in text  # 원본 수식 그대로 안 나감


# --- 다중 교사 계정 / 권한 / 격리 -------------------------------------------
def _new_teacher(admin, username, password="pw1234", is_admin=False):
    data = {"username": username, "password": password}
    if is_admin:
        data["is_admin"] = "1"
    admin.post("/admin/create", data=data)
    c = appmod.app.test_client()
    login(c, username, password)
    return c


def test_admin_create_teacher_and_login(teacher):
    c = _new_teacher(teacher, "prof1")
    assert c.get("/").status_code == 200  # 로그인 성공


def test_duplicate_username_rejected(teacher):
    teacher.post("/admin/create", data={"username": "prof1", "password": "pw1234"})
    r = teacher.post("/admin/create", data={"username": "prof1", "password": "xxxx"})
    assert "이미 존재" in r.data.decode()


def test_non_admin_blocked_from_admin(teacher):
    c = _new_teacher(teacher, "prof1")  # 일반 교사
    assert c.get("/admin").status_code == 403
    assert c.post("/admin/create",
                  data={"username": "x", "password": "pw1234"}).status_code == 403


def test_session_owner_isolation(teacher):
    owner = _new_teacher(teacher, "prof1")
    other = _new_teacher(teacher, "prof2")
    sid = make_session(owner, name="프로프1수업")
    # 타 교사: 목록에 안 보이고 직접 접근도 404
    assert "프로프1수업" not in other.get("/").data.decode()
    assert other.get(f"/teacher/{sid}").status_code == 404
    assert other.get(f"/roster/{sid}").status_code == 404
    assert other.get(f"/api/code/{sid}").status_code == 404
    # 본인은 접근 가능
    assert owner.get(f"/teacher/{sid}").status_code == 200


def test_admin_sees_all_sessions(teacher):
    owner = _new_teacher(teacher, "prof1")
    sid = make_session(owner, name="프로프1수업")
    assert "프로프1수업" in teacher.get("/").data.decode()   # 관리자 전체 조회
    assert teacher.get(f"/teacher/{sid}").status_code == 200  # 관리자 접근 허용


def test_cannot_delete_self(teacher):
    me = db.get_teacher_by_username("admin")["id"]
    r = teacher.post(f"/admin/{me}/delete")
    assert "본인 계정" in r.data.decode()
    assert db.get_teacher(me) is not None  # 삭제 안 됨


def test_delete_teacher_reassigns_sessions(teacher):
    owner = _new_teacher(teacher, "prof1")
    sid = make_session(owner, name="인계대상")
    tid = db.get_teacher_by_username("prof1")["id"]
    teacher.post(f"/admin/{tid}/delete")
    assert db.get_teacher(tid) is None              # 계정 삭제됨
    assert db.get_session(sid)["owner_id"] == db.get_teacher_by_username("admin")["id"]  # 세션 인계


def test_password_reset(teacher):
    _new_teacher(teacher, "prof1", "oldpw1")
    tid = db.get_teacher_by_username("prof1")["id"]
    teacher.post(f"/admin/{tid}/reset", data={"password": "newpw2"})
    c = appmod.app.test_client()
    assert login(c, "prof1", "oldpw1").status_code == 200  # 옛 비번 실패(로그인폼 재표시)
    assert login(c, "prof1", "newpw2").status_code == 302  # 새 비번 성공


# --- 학생 자가등록 (등록키 + 선점잠금) -------------------------------------
def test_register_disabled_by_default(client):
    # 등록키 미설정 → 자가등록 비활성, 생성 안 됨
    r = client.get("/register")
    assert "비활성" in r.data.decode()
    client.post("/register", data={"student_id": "x1", "name": "x", "code": "any"})
    assert db.get_student("x1") is None


def test_register_success_and_checkin(teacher):
    db.set_setting("enroll_code", "cs2026")
    pub = appmod.app.test_client()
    r = pub.post("/register",
                 data={"student_id": "sr1", "name": "셀프", "code": "cs2026"})
    assert "등록 완료" in r.data.decode()
    st = db.get_student("sr1")
    assert st and st["name"] == "셀프"
    # 자가등록 학생이 실제 출석 가능
    secret = st["secret"]
    sid = make_session(teacher)
    r = pub.post(f"/check/{sid}",
                 data={"student_id": "sr1", "code": code_of(secret),
                       "c": config.challenge_token(sid)})
    assert "출석 완료" in r.data.decode()


def test_register_wrong_code_rejected(client):
    db.set_setting("enroll_code", "cs2026")
    client.post("/register",
                data={"student_id": "sr2", "name": "n", "code": "WRONG"})
    assert db.get_student("sr2") is None


def test_register_duplicate_student_blocked(teacher):
    db.set_setting("enroll_code", "cs2026")
    enroll(teacher, "dup", "기존학생")
    pub = appmod.app.test_client()
    r = pub.post("/register",
                 data={"student_id": "dup", "name": "사칭", "code": "cs2026"})
    assert "이미 등록된" in r.data.decode()
    assert db.get_student("dup")["name"] == "기존학생"  # 덮어쓰기 안 됨


def test_admin_sets_enroll_code(teacher):
    teacher.post("/admin/enroll-code", data={"code": "abc123"})
    assert db.get_setting("enroll_code") == "abc123"


def test_non_admin_cannot_set_enroll_code(teacher):
    c = _new_teacher(teacher, "prof1")
    assert c.post("/admin/enroll-code",
                  data={"code": "x"}).status_code == 403


# --- 암호화 -----------------------------------------------------------------
def test_db_is_encrypted():
    import sqlite3
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(db.DB_PATH).execute("SELECT * FROM sessions").fetchone()
