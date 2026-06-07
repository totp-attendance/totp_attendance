"""SQLCipher 저장소. 세션·학생·출석 — DB 파일 전체 암호화."""
import os
import sys
from contextlib import contextmanager

from sqlcipher3 import dbapi2 as sqlcipher

DB_PATH = os.path.join(os.path.dirname(__file__), "attendance.db")

# 암호화 키: 환경변수 우선. 없으면 개발용 기본키 + 경고.
# 운영 시 반드시 ATTENDANCE_DB_KEY 설정 (키 분실 = DB 복구 불가).
_DEV_KEY = "dev-insecure-change-me"
DB_KEY = os.environ.get("ATTENDANCE_DB_KEY")
if not DB_KEY:
    DB_KEY = _DEV_KEY
    print(
        "[WARN] ATTENDANCE_DB_KEY 미설정 — 개발용 기본키 사용. "
        "운영 시 환경변수로 강한 키 지정하세요.",
        file=sys.stderr,
    )


def _key_pragma(value):
    # PRAGMA key 값은 작은따옴표 escape (SQL 인젝션 방지)
    return "PRAGMA key = '{}'".format(value.replace("'", "''"))


@contextmanager
def get_conn():
    conn = sqlcipher.connect(DB_PATH)
    conn.row_factory = sqlcipher.Row
    # 키는 모든 쿼리보다 먼저 적용해야 함
    conn.execute(_key_pragma(DB_KEY))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            -- 교사 계정 (관리자 / 일반)
            CREATE TABLE IF NOT EXISTS teachers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT    NOT NULL UNIQUE,
                pw_hash     TEXT    NOT NULL,
                is_admin    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                secret      TEXT    NOT NULL,
                interval    INTEGER NOT NULL DEFAULT 30,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                open        INTEGER NOT NULL DEFAULT 1,
                mode        TEXT    NOT NULL DEFAULT 'session',
                geo_lat     REAL,
                geo_lon     REAL,
                geo_radius  INTEGER,
                require_qr  INTEGER NOT NULL DEFAULT 0,
                owner_id    INTEGER
            );

            -- 학생별 개인 TOTP 등록 (전역, 세션과 무관)
            CREATE TABLE IF NOT EXISTS students (
                student_id  TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                secret      TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS attendance (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER NOT NULL REFERENCES sessions(id),
                student_id  TEXT    NOT NULL,
                student_name TEXT,
                checked_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                ip          TEXT,
                lat         REAL,
                lon         REAL,
                UNIQUE(session_id, student_id)
            );
            """
        )
        _migrate(conn)


def _migrate(conn):
    """기존 DB에 누락 컬럼 추가 (SQLite는 컬럼 IF NOT EXISTS 없음 → pragma 확인)."""
    def cols(table):
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    add = {
        "sessions": {
            "mode": "ALTER TABLE sessions ADD COLUMN mode TEXT NOT NULL DEFAULT 'session'",
            "geo_lat": "ALTER TABLE sessions ADD COLUMN geo_lat REAL",
            "geo_lon": "ALTER TABLE sessions ADD COLUMN geo_lon REAL",
            "geo_radius": "ALTER TABLE sessions ADD COLUMN geo_radius INTEGER",
            "require_qr": "ALTER TABLE sessions ADD COLUMN require_qr INTEGER NOT NULL DEFAULT 0",
            "owner_id": "ALTER TABLE sessions ADD COLUMN owner_id INTEGER",
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
        return conn.execute("SELECT count(*) FROM teachers").fetchone()[0]


def create_teacher(username, pw_hash, is_admin=0):
    """교사 계정 생성. username 중복이면 IntegrityError → None 반환."""
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO teachers (username, pw_hash, is_admin) VALUES (?, ?, ?)",
                (username.strip(), pw_hash, 1 if is_admin else 0),
            )
            return cur.lastrowid
        except sqlcipher.IntegrityError:
            return None


def get_teacher_by_username(username):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM teachers WHERE username = ?", (username.strip(),)
        ).fetchone()
        return dict(row) if row else None


def get_teacher(teacher_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM teachers WHERE id = ?", (teacher_id,)
        ).fetchone()
        return dict(row) if row else None


def list_teachers():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM teachers ORDER BY is_admin DESC, username"
        ).fetchall()
        return [dict(r) for r in rows]


def set_teacher_password(teacher_id, pw_hash):
    with get_conn() as conn:
        conn.execute("UPDATE teachers SET pw_hash = ? WHERE id = ?",
                     (pw_hash, teacher_id))


def delete_teacher(teacher_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM teachers WHERE id = ?", (teacher_id,))


def count_admins():
    with get_conn() as conn:
        return conn.execute(
            "SELECT count(*) FROM teachers WHERE is_admin = 1"
        ).fetchone()[0]


# --- 세션 ------------------------------------------------------------------
def create_session(name, secret, interval=30, mode="session",
                   geo_lat=None, geo_lon=None, geo_radius=None, require_qr=0,
                   owner_id=None):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO sessions "
            "(name, secret, interval, mode, geo_lat, geo_lon, geo_radius, "
            "require_qr, owner_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, secret, interval, mode, geo_lat, geo_lon, geo_radius,
             1 if require_qr else 0, owner_id),
        )
        return cur.lastrowid


def backfill_session_owner(owner_id):
    """소유자 없는(구버전) 세션을 지정 교사 소유로. 업그레이드 1회용."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET owner_id = ? WHERE owner_id IS NULL", (owner_id,)
        )


def reassign_sessions(from_owner, to_owner):
    """교사 삭제 시 그 세션을 다른 교사에게 인계 (출석 데이터 보존)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET owner_id = ? WHERE owner_id = ?",
            (to_owner, from_owner),
        )


def get_session(session_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def list_sessions(owner_id=None):
    """owner_id 주면 해당 교사 세션만, 없으면 전체(관리자용)."""
    with get_conn() as conn:
        if owner_id is None:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY id DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE owner_id = ? ORDER BY id DESC",
                (owner_id,)).fetchall()
        return [dict(r) for r in rows]


def set_session_open(session_id, is_open):
    with get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET open = ? WHERE id = ?",
            (1 if is_open else 0, session_id),
        )


# --- 학생 (개인 TOTP 등록) --------------------------------------------------
def upsert_student(student_id, name, secret):
    """신규면 등록, 기존이면 이름만 갱신(secret 유지)."""
    sid = student_id.strip()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT secret FROM students WHERE student_id = ?", (sid,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE students SET name = ? WHERE student_id = ?",
                (name.strip(), sid),
            )
            return existing["secret"], False  # 기존 secret, 신규아님
        conn.execute(
            "INSERT INTO students (student_id, name, secret) VALUES (?, ?, ?)",
            (sid, name.strip(), secret),
        )
        return secret, True  # 새 secret, 신규


def get_student(student_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM students WHERE student_id = ?", (student_id.strip(),)
        ).fetchone()
        return dict(row) if row else None


def list_students():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM students ORDER BY student_id").fetchall()
        return [dict(r) for r in rows]


def delete_student(student_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM students WHERE student_id = ?", (student_id.strip(),))


# --- 출석 ------------------------------------------------------------------
def mark_attendance(session_id, student_id, student_name, ip=None, lat=None, lon=None):
    """이미 출석했으면 False, 새로 기록하면 True."""
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO attendance "
                "(session_id, student_id, student_name, ip, lat, lon) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, student_id.strip(), student_name.strip(), ip, lat, lon),
            )
            return True
        except sqlcipher.IntegrityError:
            return False


def list_attendance(session_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM attendance WHERE session_id = ? ORDER BY checked_at",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def already_checked(session_id, student_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM attendance WHERE session_id = ? AND student_id = ?",
            (session_id, student_id.strip()),
        ).fetchone()
        return row is not None
