# TOTP 출석 프로그램

학생별 개인 TOTP(폰 브라우저가 인증기, 앱 설치 불필요)로 출석 체크. 대리출석 차단 + 현장확인(QR) + DB 암호화.

## 구성
- `app.py` — Flask 웹 서버 (앱 팩토리 + 첫 관리자 부트스트랩 + 블루프린트 등록)
- `db.py` — 저장소 (이중 백엔드: `DATABASE_URL` 있으면 Postgres/psycopg, 없으면 SQLCipher)
- `api/index.py` · `vercel.json` — Vercel 서버리스 배포 진입점/설정
- `config.py` — 환경변수 설정 + 보안 헬퍼 (비번해시/레이트리밋/IP제한/QR 챌린지)
- `views/admin.py` — 교사 계정 관리 (관리자 전용)
- `serve.py` — 운영용 WSGI 실행 (waitress)
- `attendance.db` — 실행 시 자동 생성 (암호화됨)

## 배포 (Vercel + Neon Postgres)
클라우드 배포는 **[DEPLOY.md](DEPLOY.md)** 참고. `DATABASE_URL` 이 있으면 자동으로
Postgres 백엔드(서버리스), 없으면 로컬 SQLCipher 파일 DB로 동작 (코드 동일).

## 실행 (로컬)
```powershell
pip install -r requirements-local.txt   # 로컬: SQLCipher 포함 (배포는 requirements.txt)
$env:ATTENDANCE_DB_KEY        = "강한-DB키"     # 운영 필수
$env:ATTENDANCE_SECRET_KEY    = "세션서명키"     # 운영 필수
$env:ATTENDANCE_ADMIN_USER    = "admin"        # 첫 관리자 아이디 (기본 admin)
$env:ATTENDANCE_ADMIN_PASSWORD= "관리자비번"    # 첫 관리자 비번 (운영 필수)
python app.py            # 개발 서버
# 또는 운영: python serve.py   (waitress)
```
서버: http://localhost:5000 → `/login` 아이디·비밀번호 입력

## 교사 계정 (다중 교사)
- **첫 실행 시** `ATTENDANCE_ADMIN_USER`/`ATTENDANCE_ADMIN_PASSWORD` 로 관리자 1명 자동 생성
  (없으면 하위호환으로 `ATTENDANCE_TEACHER_PASSWORD` 사용, 그것도 없으면 `admin`/`admin`).
- 관리자는 `/admin`(계정 관리)에서 **교사 계정 생성·삭제·비밀번호 초기화**. 관리자 권한 부여 가능.
- **세션 격리**: 일반 교사는 본인 세션·출석부만 보고 관리. 관리자는 전체 조회·접근.
  교사 삭제 시 그 세션은 작업 관리자에게 인계(출석 데이터 보존).
- 학생 등록(`/students`)은 **학교 공용** — 모든 교사가 공유(한 학생이 여러 수업 수강).
- 비밀번호는 `werkzeug`(pbkdf2)로 **해시 저장** (평문 아님).

## 인증 방식: 학생별 개인 TOTP (브라우저 인증기)
학생 폰 브라우저 자체가 인증기. 본인 secret 으로 코드 생성. **대리출석 차단** —
본인 secret 없으면 코드 생성 불가. **별도 앱(Google Authenticator 등) 설치 불필요.**

### 사용 순서
1. 교사: `/students` → 학번·이름 등록 → 등록 QR 표시
2. 학생: 등록 QR을 폰 카메라로 스캔 → 브라우저(`/setup`)가 secret을 **이 기기에 저장**
   (앱 설치 불필요. 브라우저가 인증기 역할)
3. 교사: 세션 생성 (이름만 — 현장확인 QR 자동 적용)
4. 학생: 수업 QR 스캔 → **학번·코드 자동 입력·자동 제출** (브라우저가 TOTP 계산)
   미등록 기기는 학번 + 코드 수동 입력 폴백

### 기기 등록 = 브라우저가 인증기
- 학생 폰 브라우저가 secret을 `localStorage`에 보관 → 페이지에서 TOTP 자동 계산
  (`static/attendance.js`: base32 + HMAC-SHA1, pyotp 호환).
- **평문 HTTP LAN 지원**: WebCrypto(`crypto.subtle`)는 HTTPS/localhost 에서만 동작.
  학생폰이 `http://<교사PC-IP>:5000` 평문으로 접속하면 WebCrypto 가 없으므로
  순수 JS HMAC-SHA1 폴백으로 코드 계산 → 인증서 없이도 자동출석 동작.
- 출석은 `fetch`로 제출 → 페이지 이동 없이 결과 표시.
- 보안 메모: secret이 기기 브라우저에 저장됨(인증앱과 동급 신뢰). 공용 PC 사용 금지,
  기기 분실 시 교사가 해당 학생 재등록(새 secret 발급).

## 교수별 시간표 (과목)
교수마다 본인 정규 수업을 등록하고 거기서 출석을 바로 시작.
- `/timetable` — 본인 **주간 시간표 그리드**(월~금) + 수업 추가/수정/삭제 (과목명·요일·시작·종료·강의실).
- **오늘 수업** 박스 → **[출석 시작]** 1번 → 세션 자동 생성(이름=과목+날짜, 현장확인 QR 자동) → 교사 화면.
- **중복 방지**: 같은 과목·같은 날 이미 시작했으면 그 세션으로 이동.
- 세션은 `course_id` 로 과목에 연결(과목별 이력 집계용). 소유 격리(본인 시간표만).

## 학생 자가등록 (선택)
교사가 일일이 등록하는 대신 학생이 직접 등록하게 할 수 있음.
- 관리자가 `/admin`에서 **등록키** 설정 → 자가등록 활성화 (빈 값 = 비활성).
- 학생은 `/register`(공개)서 **학번·이름·등록키** 입력 → 그 기기 브라우저에 secret 저장.
- **선점잠금**: 이미 등록된 학번은 거부(첫 등록자만) → 남이 내 secret 못 빼감. 재등록은 교사가 삭제 후.
- 등록키 무차별 대입 방지: `(register, IP)` 레이트리밋. 등록키 비교는 `hmac.compare_digest`.
- 교사 화면(`/students`)에 자가등록 링크/QR 표시 → 학생에게 공유.
- 한계: 외부인은 등록키로 차단되나, **같은 반 내 학번 선점 도용**은 막지 못함(완전 차단은 OAuth/이메일 인증 필요).

## 현장 확인 (QR, 항상 적용) — 원격 출석 차단
모든 세션에 자동 적용 (옵션 아님):
- 교사 화면에 **QR** 표시 (기본 10초마다 갱신, HMAC 챌린지 내장 → 위조 불가).
- 학생은 **지금 화면의 QR을 카메라로 스캔**해야 출석 페이지 진입 (챌린지가 URL `?c=`로 전달).
- 만료/위조 챌린지는 거부 → 원격 학생은 화면을 못 봐 유효 챌린지를 얻지 못함.

이중 방어:
- **현장**: QR 챌린지 (교실 화면에만, 금방 만료) → 원격 차단
- **본인**: 개인 TOTP → 대리 차단

암호 설계: 챌린지 = `세션ID.epoch.HMAC(SECRET_KEY, "세션ID:epoch")`. 서버가 재계산해 현재/직전
epoch만 허용. QR 이미지(`/qrc/<id>`)는 교사 로그인 필요 → 외부에서 챌린지 못 빼감.

**잔여 한계**: 교실 안 공범이 QR을 실시간 중계하면 원격 가능(릴레이). 완전 차단은
거리한정(distance bounding) 전용 하드웨어 영역. 갱신 주기를 짧게 해 릴레이 난이도를 높임.
조정: `ATTENDANCE_QR_ROTATE_SEC` (기본 10).

## HTTPS (운영)
- 운영은 HTTPS 필수 (없으면 코드·비번 평문 전송).
  개발: `$env:ATTENDANCE_SSL="1"; python app.py` (adhoc 자체서명).
  운영: 리버스 프록시(nginx/caddy)로 HTTPS 종단.

## 환경변수
| 변수 | 용도 | 미설정 시 |
|---|---|---|
| `DATABASE_URL` | 있으면 Postgres 백엔드(Neon/Vercel), 없으면 로컬 SQLCipher | 로컬 SQLCipher |
| `ATTENDANCE_DB_KEY` | SQLCipher DB 암호화 키 (로컬 백엔드 전용) | 개발용 기본키 + 경고 |
| `ATTENDANCE_FIELD_KEY` | 개인 TOTP 시드(students.secret) 앱단 Fernet 암호화 키 | 없으면 평문(로컬은 파일 암호화로 보호) |
| `ATTENDANCE_HTTPS` | `1` 이면 Secure 쿠키 강제 (HTTPS 종단/Vercel 뒤) | off |
| `ATTENDANCE_SECRET_KEY` | Flask 세션 쿠키 서명 | 임시 랜덤(재시작 시 로그인 풀림) |
| `ATTENDANCE_ADMIN_USER` | 첫 관리자 아이디 (첫 실행 시드) | `admin` |
| `ATTENDANCE_ADMIN_PASSWORD` | 첫 관리자 비번 (첫 실행 시드) | `ATTENDANCE_TEACHER_PASSWORD` → 없으면 `admin` + 경고 |
| `ATTENDANCE_TEACHER_PASSWORD` | (하위호환) 관리자 비번 미설정 시 대체 | — |
| `ATTENDANCE_ALLOWED_SUBNETS` | 출석 허용 IP 대역(쉼표, 예: `192.168.0.,10.0.`) | 제한 없음 |
| `ATTENDANCE_TRUST_PROXY` | `1` 이면 `X-Forwarded-For` 신뢰(신뢰 리버스프록시 뒤에서만) | off (위조 방지, remote_addr 만) |
| `ATTENDANCE_QR_ROTATE_SEC` | QR 챌린지 갱신 주기(초) | `10` |
| `ATTENDANCE_RATE_MAX_FAILS` | 레이트리밋 실패 한도 | `5` |
| `ATTENDANCE_RATE_WINDOW_SEC` | 레이트리밋 윈도우(초) | `60` |
| `ATTENDANCE_SSL` | `1` 이면 adhoc HTTPS | off |
| `ATTENDANCE_DEBUG` | `1` 이면 디버그 모드 | off |
| `ATTENDANCE_PORT` | 포트 | `5000` |

## 보안
- **DB 암호화**: SQLCipher AES-256, DB 파일 전체 암호화 — 일반 `sqlite3`로 못 엶.
  키 분실 = 복구 불가. 키를 코드/git에 넣지 말 것.
- **교사 인증**: 아이디/비밀번호 로그인 (비번 `werkzeug` pbkdf2 해시 저장). 모든 교사
  라우트 로그인 필요. `/check`·`/qr`·`/setup`만 공개. `/api/code`·`/qrc`도 보호.
- **계정 권한**: 관리자/일반 교사 2단계. `/admin` 계정관리는 관리자 전용(비관리자 403).
- **세션 소유 격리**: 일반 교사는 본인 세션만 접근(타 세션 404로 존재 은닉). 관리자는 전체.
- **세션 고정 방지**: 로그인 성공 시 세션 초기화 후 재발급.
- **무차별 대입 방지**: 출석은 `(session_id, IP, 학번)`, 교사 로그인은 `("login", IP)`
  당 슬라이딩 윈도우 레이트리밋 (공용 IP 라도 한 명 실패가 반 전체를 잠그지 않음).
- **네트워크 제한**: `ATTENDANCE_ALLOWED_SUBNETS` IP 대역. `X-Forwarded-For` 는
  **기본 무시**(클라 위조 가능) — 신뢰 리버스프록시 뒤에서 `ATTENDANCE_TRUST_PROXY=1`
  일 때만 사용. 안 그러면 `remote_addr` 만 신뢰 → IP제한·레이트리밋·감사 위조 방지.
- **상수시간 비교**: QR 챌린지 nonce 는 `hmac.compare_digest`. 비번은 pbkdf2 해시 검증.
- **CSRF 방어**: 세션쿠키 `SameSite=Strict` + `HttpOnly` (HTTPS 면 `Secure`) →
  악성사이트가 교사 세션으로 세션생성·학생삭제 강제 불가.
- **출석여부 오라클 차단**: 중복출석 검사를 TOTP 검증 *뒤*에 수행 →
  코드 모르면 특정 학생의 출석여부를 캐낼 수 없음.
- **secret 누출 방지**: 기기등록 QR 의 secret 은 URL fragment(`#`)로 전달 →
  서버/프록시 액세스 로그·브라우저 히스토리에 안 남음.
- **CSV 수식 인젝션 방지**: `=`/`+`/`-`/`@` 로 시작하는 셀에 작은따옴표 prepend.
- **감사 로그**: 출석마다 IP 기록 → 출석부/CSV에 표시.

## 동작
- 세션 secret: 생성 시 랜덤 base32. 개인 secret: 학생 등록 시 랜덤 base32 (표준 30초 주기).
- 코드 검증: `valid_window=1` — 직전/현재/직후 윈도우 허용 (시계 오차 대비).
- 중복 출석: `(session_id, student_id)` UNIQUE 제약으로 차단.
- 스키마 마이그레이션: 구버전 DB 열면 누락 컬럼 자동 추가 (`db._migrate`).

## 데이터 모델
- `teachers(id PK, username UNIQUE, pw_hash, is_admin, created_at)` — 교사 계정
- `sessions(..., require_qr, owner_id, course_id)`
  — `owner_id` = 소유 교사(teachers.id), `course_id` = 연결 과목(courses.id, 선택).
  geo_*·secret·interval·mode 는 미사용(dead) 컬럼.
- `courses(id, owner_id, name, day, start_t, end_t, room, created_at)` — 교수별 주간 시간표(과목)
- `students(student_id PK, name, secret, created_at)` — 개인 TOTP 등록 (학교 공용)
- `attendance(id, session_id, student_id, student_name, checked_at, ip, lat, lon)` + UNIQUE(session_id, student_id) — lat/lon 미사용(dead)
- `settings(key PK, value)` — 전역 설정 (예: `enroll_code` 자가등록 등록키)

## 같은 와이파이에서 학생 폰 접속
서버는 `0.0.0.0` 바인드. 학생은 `http://<교사PC IP>:5000/check/<id>` 접속.
QR은 접속 호스트 기준 URL 생성 → PC IP로 서버 열고 그 화면의 QR 사용.

## 남은 한계
- 개인 TOTP secret을 학생이 타인과 공유하면 여전히 대리 가능 (인적 통제 영역).
- 레이트리밋은 인메모리 → 다중워커/재시작 시 초기화. 다중워커 운영이면 Redis 등 공유저장소 필요.
- 운영은 HTTPS 필수 (없으면 코드·비번 평문 전송).
