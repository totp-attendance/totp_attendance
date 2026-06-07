"""저장소 — 이중 백엔드.

DATABASE_URL 있으면 PostgreSQL(psycopg, 예: Neon/Vercel), 없으면 로컬
SQLCipher(파일 전체 암호화). 함수 API 는 동일 → 상위(views) 무수정.

민감 컬럼(students.secret = 개인 TOTP 시드)은 ATTENDANCE_FIELD_KEY 가 있으면
앱단에서 Fernet 암호화 저장(Postgres 는 SQLCipher 같은 전체파일 암호화가 없으므로).
키 없으면 평문(로컬/테스트 — SQLCipher 가 파일째 암호화).
"""
import os
import sys
import base64
import hashlib
from contextlib import contextmanager

DATABASE_URL = os.environ.get("DATABASE_URL")
PG = bool(DATABASE_URL)

if PG:
    import psycopg
    from psycopg.rows import dict_row
    _INTEGRITY = (psycopg.errors.IntegrityError,)
else:
    from sqlcipher3 import dbapi2 as sqlcipher
    _INTEGRITY = (sqlcipher.IntegrityError,)

DB_PATH = os.path.join(os.path.dirname(__file__), "attendance.db")

# SQLCipher 파일 암호화 키 (로컬 백엔드 전용)
_DEV_KEY = "dev-insecure-change-me"
DB_KEY = os.environ.get("ATTENDANCE_DB_KEY")
if not PG and not DB_KEY:
    DB_KEY = _DEV_KEY
    print("[WARN] ATTENDANCE_DB_KEY 미설정 — 개발용 기본키 사용. "
          "운영 시 환경변수로 강한 키 지정하세요.", file=sys.stderr)


# --- 민감 컬럼 암호화 (Fernet, 키 있을 때만) --------------------------------
_FIELD_KEY = os.environ.get("ATTENDANCE_FIELD_KEY")
if _FIELD_KEY:
    from cryptography.fernet import Fernet
    # 임의 문자열 → 안정적인 32B Fernet 키 (urlsafe base64)
    _fernet = Fernet(base64.urlsafe_b64encode(
        hashlib.sha256(_FIELD_KEY.encode()).digest()))
else:
    _fernet = None
if PG and not _FIELD_KEY:
    print("[WARN] ATTENDANCE_FIELD_KEY 미설정 — Postgres 에서 개인 TOTP 시드가 "
          "평문 저장됩니다. 운영 시 반드시 설정.", file=sys.stderr)


def _enc(plain):
    if _fernet is None or plain is None:
        return plain
    return _fernet.encrypt(plain.encode()).decode()


def _dec(stored):
    if _fernet is None or stored is None:
        return stored
    try:
        return _fernet.decrypt(stored.encode()).decode()
    except Exception:
        return stored  # 평문(미암호화 기존값) 호환


# --- 연결 -------------------------------------------------------------------
def _key_pragma(value):
    return "PRAGMA key = '{}'".format(value.replace("'", "''"))


@contextmanager
def get_conn():
    if PG:
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    else:
        conn = sqlcipher.connect(DB_PATH)
        conn.row_factory = sqlcipher.Row
        conn.execute(_key_pragma(DB_KEY))      # 키는 모든 쿼리보다 먼저
        conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ph(sql):
    """플레이스홀더: 코드엔 ? 로 쓰고 Postgres 면 %s 로 변환."""
    return sql.replace("?", "%s") if PG else sql


def _q1(conn, sql, params=()):
    row = conn.execute(_ph(sql), params).fetchone()
    return dict(row) if row else None


def _qa(conn, sql, params=()):
    return [dict(r) for r in conn.execute(_ph(sql), params).fetchall()]


def _ex(conn, sql, params=()):
    conn.execute(_ph(sql), params)


def _ins(conn, sql, params=()):
    """INSERT 후 새 id 반환 (PG: RETURNING id / SQLite: lastrowid)."""
    if PG:
        return conn.execute(_ph(sql) + " RETURNING id", params).fetchone()["id"]
    return conn.execute(sql, params).lastrowid


# --- 스키마 -----------------------------------------------------------------
_DDL_SQLITE = [
    """CREATE TABLE IF NOT EXISTS teachers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        pw_hash TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))""",
    """CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        secret TEXT NOT NULL,
        "interval" INTEGER NOT NULL DEFAULT 30,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        open INTEGER NOT NULL DEFAULT 1,
        mode TEXT NOT NULL DEFAULT 'session',
        geo_lat REAL, geo_lon REAL, geo_radius INTEGER,
        require_qr INTEGER NOT NULL DEFAULT 0,
        owner_id INTEGER,
        course_id INTEGER)""",
    """CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        day INTEGER NOT NULL,
        start_t TEXT, end_t TEXT, room TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))""",
    """CREATE TABLE IF NOT EXISTS students (
        student_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        secret TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))""",
    """CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL REFERENCES sessions(id),
        student_id TEXT NOT NULL,
        student_name TEXT,
        checked_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        ip TEXT, lat REAL, lon REAL,
        UNIQUE(session_id, student_id))""",
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT)""",
]

_DDL_PG = [
    """CREATE TABLE IF NOT EXISTS teachers (
        id SERIAL PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        pw_hash TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS sessions (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        secret TEXT NOT NULL,
        "interval" INTEGER NOT NULL DEFAULT 30,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        open INTEGER NOT NULL DEFAULT 1,
        mode TEXT NOT NULL DEFAULT 'session',
        geo_lat DOUBLE PRECISION, geo_lon DOUBLE PRECISION, geo_radius INTEGER,
        require_qr INTEGER NOT NULL DEFAULT 0,
        owner_id INTEGER,
        course_id INTEGER)""",
    """CREATE TABLE IF NOT EXISTS courses (
        id SERIAL PRIMARY KEY,
        owner_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        day INTEGER NOT NULL,
        start_t TEXT, end_t TEXT, room TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS students (
        student_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        secret TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS attendance (
        id SERIAL PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES sessions(id),
        student_id TEXT NOT NULL,
        student_name TEXT,
        checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        ip TEXT, lat DOUBLE PRECISION, lon DOUBLE PRECISION,
        UNIQUE(session_id, student_id))""",
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT)""",
]


def init_db():
    with get_conn() as conn:
        for ddl in (_DDL_PG if PG else _DDL_SQLITE):
            conn.execute(ddl)
        _migrate(conn)


def _migrate(conn):
    """구버전 DB 누락 컬럼 보강."""
    if PG:
        for stmt in [
            "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS require_qr INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS owner_id INTEGER",
            "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS course_id INTEGER",
            "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS ip TEXT",
            "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS lat DOUBLE PRECISION",
            "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS lon DOUBLE PRECISION",
        ]:
            conn.execute(stmt)
        return
    # SQLite: 컬럼 IF NOT EXISTS 없음 → pragma 확인 후 ALTER
    def cols(table):
        return {r[1] for r in conn.execute(
            f"PRAGMA table_info({table})").fetchall()}
    add = {
        "sessions": {
            "require_qr": "ALTER TABLE sessions ADD COLUMN require_qr INTEGER NOT NULL DEFAULT 0",
            "owner_id": "ALTER TABLE sessions ADD COLUMN owner_id INTEGER",
            "course_id": "ALTER TABLE sessions ADD COLUMN course_id INTEGER",
        },
        "attendance": {
            "ip": "ALTER TABLE attendance ADD COLUMN ip TEXT",
            "lat": "ALTER TABLE attendance ADD COLUMN lat REAL",
            "lon": "ALTER TABLE attendance ADD COLUMN lon REAL",
        },
    }
    for table, specs in add.items():
        existing = cols(table)
        for col, ddl in specs.items():
            if col not in existing:
                conn.execute(ddl)


# --- 교사 계정 --------------------------------------------------------------
def count_teachers():
    with get_conn() as conn:
        return _q1(conn, "SELECT count(*) AS n FROM teachers")["n"]


def create_teacher(username, pw_hash, is_admin=0):
    """교사 계정 생성. username 중복이면 None."""
    with get_conn() as conn:
        try:
            return _ins(conn,
                        "INSERT INTO teachers (username, pw_hash, is_admin) "
                        "VALUES (?, ?, ?)",
                        (username.strip(), pw_hash, 1 if is_admin else 0))
        except _INTEGRITY:
            conn.rollback()
            return None


def get_teacher_by_username(username):
    with get_conn() as conn:
        return _q1(conn, "SELECT * FROM teachers WHERE username = ?",
                   (username.strip(),))


def get_teacher(teacher_id):
    with get_conn() as conn:
        return _q1(conn, "SELECT * FROM teachers WHERE id = ?", (teacher_id,))


def list_teachers():
    with get_conn() as conn:
        return _qa(conn,
                   "SELECT * FROM teachers ORDER BY is_admin DESC, username")


def set_teacher_password(teacher_id, pw_hash):
    with get_conn() as conn:
        _ex(conn, "UPDATE teachers SET pw_hash = ? WHERE id = ?",
            (pw_hash, teacher_id))


def delete_teacher(teacher_id):
    with get_conn() as conn:
        _ex(conn, "DELETE FROM teachers WHERE id = ?", (teacher_id,))


def count_admins():
    with get_conn() as conn:
        return _q1(conn,
                   "SELECT count(*) AS n FROM teachers WHERE is_admin = 1")["n"]


# --- 세션 ------------------------------------------------------------------
def create_session(name, secret, require_qr=0, owner_id=None, course_id=None):
    # interval/mode/geo_* 는 dead 컬럼 → DB 기본값/NULL (INSERT 에서 제외)
    with get_conn() as conn:
        return _ins(conn,
                    "INSERT INTO sessions "
                    "(name, secret, require_qr, owner_id, course_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (name, secret, 1 if require_qr else 0, owner_id, course_id))


def get_course_session(course_id, name):
    """같은 과목·같은 이름(=과목+날짜) 세션 찾기 (출석 시작 중복 방지)."""
    with get_conn() as conn:
        return _q1(conn,
                   "SELECT * FROM sessions WHERE course_id = ? AND name = ?",
                   (course_id, name))


# --- 시간표(과목) ----------------------------------------------------------
def create_course(owner_id, name, day, start_t, end_t, room):
    with get_conn() as conn:
        return _ins(conn,
                    "INSERT INTO courses (owner_id, name, day, start_t, end_t, room) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (owner_id, name, day, start_t, end_t, room))


def get_course(course_id):
    with get_conn() as conn:
        return _q1(conn, "SELECT * FROM courses WHERE id = ?", (course_id,))


def list_courses(owner_id):
    with get_conn() as conn:
        return _qa(conn,
                   "SELECT * FROM courses WHERE owner_id = ? "
                   "ORDER BY day, start_t", (owner_id,))


def update_course(course_id, name, day, start_t, end_t, room):
    with get_conn() as conn:
        _ex(conn,
            "UPDATE courses SET name = ?, day = ?, start_t = ?, end_t = ?, "
            "room = ? WHERE id = ?",
            (name, day, start_t, end_t, room, course_id))


def delete_course(course_id):
    with get_conn() as conn:
        _ex(conn, "DELETE FROM courses WHERE id = ?", (course_id,))


def backfill_session_owner(owner_id):
    """소유자 없는(구버전) 세션을 지정 교사 소유로. 업그레이드 1회용."""
    with get_conn() as conn:
        _ex(conn, "UPDATE sessions SET owner_id = ? WHERE owner_id IS NULL",
            (owner_id,))


def reassign_sessions(from_owner, to_owner):
    """교사 삭제 시 세션을 다른 교사에게 인계 (출석 데이터 보존)."""
    with get_conn() as conn:
        _ex(conn, "UPDATE sessions SET owner_id = ? WHERE owner_id = ?",
            (to_owner, from_owner))


def get_session(session_id):
    with get_conn() as conn:
        return _q1(conn, "SELECT * FROM sessions WHERE id = ?", (session_id,))


def list_sessions(owner_id=None):
    """owner_id 주면 해당 교사 세션만, 없으면 전체(관리자용)."""
    with get_conn() as conn:
        if owner_id is None:
            return _qa(conn, "SELECT * FROM sessions ORDER BY id DESC")
        return _qa(conn,
                   "SELECT * FROM sessions WHERE owner_id = ? ORDER BY id DESC",
                   (owner_id,))


def set_session_open(session_id, is_open):
    with get_conn() as conn:
        _ex(conn, "UPDATE sessions SET open = ? WHERE id = ?",
            (1 if is_open else 0, session_id))


# --- 학생 (개인 TOTP 등록) --------------------------------------------------
def upsert_student(student_id, name, secret):
    """신규면 등록, 기존이면 이름만 갱신(secret 유지). secret 은 암호화 저장."""
    sid = student_id.strip()
    with get_conn() as conn:
        existing = _q1(conn,
                       "SELECT secret FROM students WHERE student_id = ?", (sid,))
        if existing:
            _ex(conn, "UPDATE students SET name = ? WHERE student_id = ?",
                (name.strip(), sid))
            return _dec(existing["secret"]), False  # 기존 secret(복호), 신규아님
        _ex(conn,
            "INSERT INTO students (student_id, name, secret) VALUES (?, ?, ?)",
            (sid, name.strip(), _enc(secret)))
        return secret, True  # 새 secret(평문), 신규


def get_student(student_id):
    with get_conn() as conn:
        row = _q1(conn, "SELECT * FROM students WHERE student_id = ?",
                  (student_id.strip(),))
    if row:
        row["secret"] = _dec(row["secret"])
    return row


def list_students():
    with get_conn() as conn:
        return _qa(conn, "SELECT * FROM students ORDER BY student_id")


def delete_student(student_id):
    with get_conn() as conn:
        _ex(conn, "DELETE FROM students WHERE student_id = ?",
            (student_id.strip(),))


# --- 출석 ------------------------------------------------------------------
def mark_attendance(session_id, student_id, student_name, ip=None):
    """이미 출석했으면 False, 새로 기록하면 True. (lat/lon 은 dead 컬럼)"""
    with get_conn() as conn:
        try:
            _ex(conn,
                "INSERT INTO attendance "
                "(session_id, student_id, student_name, ip) "
                "VALUES (?, ?, ?, ?)",
                (session_id, student_id.strip(), student_name.strip(), ip))
            return True
        except _INTEGRITY:
            conn.rollback()
            return False


def list_attendance(session_id):
    with get_conn() as conn:
        return _qa(conn,
                   "SELECT * FROM attendance WHERE session_id = ? "
                   "ORDER BY checked_at", (session_id,))


def already_checked(session_id, student_id):
    with get_conn() as conn:
        return _q1(conn,
                   "SELECT 1 AS x FROM attendance "
                   "WHERE session_id = ? AND student_id = ?",
                   (session_id, student_id.strip())) is not None


# --- 전역 설정 (key-value) --------------------------------------------------
def get_setting(key, default=None):
    with get_conn() as conn:
        row = _q1(conn, "SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key, value):
    with get_conn() as conn:
        exists = _q1(conn, "SELECT 1 AS x FROM settings WHERE key = ?", (key,))
        if exists:
            _ex(conn, "UPDATE settings SET value = ? WHERE key = ?", (value, key))
        else:
            _ex(conn, "INSERT INTO settings (key, value) VALUES (?, ?)",
                (key, value))
