# CLAUDE.md

Guidance for Claude Code (and other AI sessions) working in this repo. Read this
first — it captures architecture, commands, and non-obvious constraints so you can
start working immediately without re-deriving context.

## What this is

A TOTP-based class attendance web app (Flask). Each student has a personal TOTP
secret; attendance proves **identity** (personal TOTP) + **physical presence**
(rotating QR challenge). Encrypted-at-rest DB (SQLCipher). Korean UI; code comments
are Korean.

Core security tension solved: TOTP proves *who* you are but not *where* you are.
Dual factor closes the gap:
- **Identity** — personal TOTP (no secret → no valid code → no proxy attendance).
- **Presence** — rotating QR with a short-lived HMAC challenge shown only on the
  classroom screen (remote student can't see it → can't get a valid challenge).

Residual limit (hardware territory, not fixable in code): an in-room accomplice
relaying the QR in real time. Mitigated by short rotation, not eliminated.

## Architecture

App-factory + Blueprints. All DB access is isolated in `db.py` — no other module
runs SQL.

```
app.py            Flask app factory (create_app). Sets secret_key, CSRF cookie
                  flags, calls db.init_db(), registers 4 blueprints. __main__ runs
                  dev server (ssl_context="adhoc" if ATTENDANCE_SSL=1).
config.py         Env-var settings + security helpers: password check, IP allowlist,
                  in-memory rate limiter, rotating-QR challenge
                  (challenge_token / verify_challenge).
db.py             SQLCipher store. ONLY place that touches the DB. get_conn()
                  applies PRAGMA key before any query. Schema + _migrate (adds
                  missing columns via PRAGMA table_info). CRUD helpers.
helpers.py        require_teacher decorator, client_ip, parse_float, png_response,
                  PERSONAL_INTERVAL=30.
views/auth.py     /login (open-redirect-guarded), /logout.
views/sessions.py Session CRUD, /teacher, /api/code (count), /qr (static),
                  /qrc (rotating challenge QR, login-required), /roster, /toggle,
                  /export CSV. _csv_safe() neutralizes formula injection.
views/students.py Student personal-TOTP enrollment, device-registration QR
                  (/student/<id>/qr.png → /setup#... with secret in fragment),
                  public /setup, delete. (No Google Authenticator / otpauth —
                  browser-authenticator only.)
views/checkin.py  /check/<id> — _validate() runs the ordered gate checks, then
                  records attendance. AJAX branch returns JSON.
static/attendance.js  Browser-side TOTP (HMAC-SHA1, pyotp-compatible) +
                  localStorage device identity. Makes the phone browser the
                  authenticator (no app install needed). Uses WebCrypto when
                  available, else a pure-JS HMAC-SHA1 fallback — so it works on
                  plain HTTP LAN (http://<PC-IP>:5000), where crypto.subtle is
                  undefined (secure-context only). Do NOT remove the fallback.
templates/        Jinja, base.html inheritance.
serve.py          Production WSGI (waitress).
run.ps1           Loads .env into env vars, runs app.py (or serve.py with -Serve).
test_app.py       pytest suite (27 tests), temp encrypted DB per test.
```

## Data model

- `sessions(id, name, secret, interval, created_at, open, mode, geo_lat, geo_lon, geo_radius, require_qr)`
  — `secret`/`interval`/`mode` and `geo_lat`/`geo_lon`/`geo_radius` are dead columns
  (personal-TOTP mode; geofence was removed). Kept in schema for NOT NULL / migration
  compatibility; no code reads or writes them.
- `students(student_id PK, name, secret, created_at)` — global, session-independent.
- `attendance(id, session_id, student_id, student_name, checked_at, ip, lat, lon)`
  with `UNIQUE(session_id, student_id)` (duplicate-attendance guard).

## Attendance flow

1. Teacher: `/login` → `/students` enroll (server generates random base32 secret)
   → show registration QR.
2. Student (once per device): scan registration QR → `/setup#sid=&name=&s=secret`
   → `attendance.js` saves identity to `localStorage`. No app install — the browser
   IS the authenticator (no Google Authenticator / otpauth path).
3. Teacher: `/create` (name + optional require_qr) → `/teacher/<id>`
   shows the rotating QR (refreshes every QR_ROTATE_SEC, default 10s) with a
   countdown bar, plus live attendance count.
4. Student (scan once): scan classroom rotating QR → `/check/<id>?c=<challenge>` →
   JS auto-fills student_id + computed TOTP → submits via `fetch` (AJAX, no page
   nav) → inline JSON result. Unregistered devices fall back to manual entry.

`_validate` gate order (in `views/checkin.py`): ip_allowed → rate_limit → open →
require_qr challenge → required fields → student enrolled → **TOTP
verify** → duplicate check. (Duplicate check is intentionally *after* TOTP verify —
see security note below.)

## Commands

```powershell
pip install -r requirements.txt

# Run tests (ALWAYS do this first — see constraints below)
python -m pytest -q

# Dev server (http://localhost:5000)
python app.py
# Same-wifi student phones can reach http://<teacher-PC-IP>:5000/check/<id>

# Production
python serve.py            # waitress
# or load .env and run:
.\run.ps1                  # dev
.\run.ps1 -Serve           # waitress
```

### Running tests — IMPORTANT setup

`create_app()` runs `db.init_db()` at import time on the default `attendance.db`.
A stale `attendance.db` created with a different key makes SQLCipher fail at import
(hmac check). Before running pytest:

1. Kill any stray server holding port 5000:
   `Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`
2. Delete `attendance.db` if present.

The test fixture overrides `db.DB_PATH` to a tmp file per test, so tests never touch
the real DB once running — the issue is only the import-time init.

After editing code, restart the background dev server manually (Flask debug is off →
no auto-reload) before any live/E2E check.

## Environment variables

| Var | Purpose | If unset |
|---|---|---|
| `ATTENDANCE_DB_KEY` | SQLCipher encryption key | dev default key + warning |
| `ATTENDANCE_SECRET_KEY` | Flask session signing | random per process (logins drop on restart) |
| `ATTENDANCE_TEACHER_PASSWORD` | teacher login | `admin` + warning |
| `ATTENDANCE_ALLOWED_SUBNETS` | comma IP-prefix allowlist (e.g. `192.168.0.,10.0.`) | no restriction |
| `ATTENDANCE_TRUST_PROXY` | `1` → trust `X-Forwarded-For` (only behind a trusted reverse proxy) | off (use remote_addr only) |
| `ATTENDANCE_QR_ROTATE_SEC` | rotating-QR challenge period | `10` |
| `ATTENDANCE_RATE_MAX_FAILS` / `_WINDOW_SEC` | rate limiter | `5` / `60` |
| `ATTENDANCE_SSL` | `1` → adhoc HTTPS | off |
| `ATTENDANCE_DEBUG` | `1` → Flask debug (NEVER in prod) | off |
| `ATTENDANCE_PORT` | port | `5000` |

## Security model (implemented)

- **DB encryption**: SQLCipher AES-256, whole-file. `ATTENDANCE_DB_KEY` loss =
  unrecoverable DB. Changing the key breaks the existing DB.
- **Teacher auth**: all teacher routes require login. Only `/check`, `/qr`, `/setup`
  are public. `/qrc` (challenge QR) and `/api/code` are login-gated so challenges/
  codes can't be pulled externally.
- **Rate limit**: sliding window — check-in keyed on `(session_id, ip, student_id)`,
  teacher login keyed on `("login", ip)`. A shared public IP won't lock out the class.
- **X-Forwarded-For**: ignored by default (client-spoofable) — `client_ip()` uses
  `remote_addr` unless `ATTENDANCE_TRUST_PROXY=1` (behind a trusted reverse proxy).
  Prevents IP-allowlist bypass, rate-limit evasion, and audit-IP forgery.
- **CSRF**: session cookie `SameSite=Strict` + `HttpOnly` (+ `Secure` when SSL).
- **Attendance-status oracle blocked**: duplicate check runs *after* TOTP verify, so
  you can't probe whether a student is present without their code.
- **Secret leak prevention**: device-registration QR carries the secret in the URL
  fragment (`#`), which is never sent to the server → not in access logs / history.
- **CSV formula injection**: cells starting with `= + - @` get a leading `'`.
- **Constant-time**: password and challenge nonce compared with `hmac.compare_digest`.
- **Audit**: every attendance row logs IP.

## Conventions & hard constraints

- **NEVER commit `.env` or `attendance.db`** (both gitignored). Before any push,
  verify: `git ls-files --cached | Select-String '\.env$|attendance\.db'` must be
  empty. `.env` holds real generated secrets (DB key, session key, teacher password)
  — local only.
- Git commits use trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
  and author `git -c user.name="heahgo" -c user.email="1hc0c07@gmail.com"`.
- Remote `github.com/totp-attendance/totp_attendance` is **public**.
- Code comments and UI strings are Korean; match the surrounding style.
- Windows / PowerShell environment. Use `$null`, `$env:VAR`. For `python -c`,
  prefer a temp `.py` file over PowerShell here-strings (here-strings with `/path`
  and quotes get mis-parsed).
- All new DB access goes through `db.py` — do not open connections elsewhere.
- The student check page submits via `fetch` (AJAX → JSON). Do NOT switch back to
  full-page form POST: it caused an infinite resubmit loop because the result page
  still carried the challenge.

## Remaining limits (documented, not bugs)

- Sharing a personal TOTP secret with another person still enables proxy attendance
  (human/policy control).
- Rate limiter is in-memory → resets on restart / not shared across workers
  (use Redis etc. for multi-worker).
- Production needs HTTPS (else codes/passwords travel in plaintext).
- Rotating-QR real-time relay by an in-room accomplice (distance-bounding hardware
  territory).
