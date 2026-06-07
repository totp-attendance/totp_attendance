# TOTP 출석 프로그램

학생별 개인 TOTP(Google Authenticator 등)로 출석 체크. 대리출석 차단 + 현장확인(회전 QR) + DB 암호화.

## 구성
- `app.py` — Flask 웹 서버 (교사화면 + 학생출석 + 학생관리 + 출석부/CSV)
- `db.py` — SQLCipher 저장소 (DB 파일 전체 암호화 + 스키마 마이그레이션)
- `config.py` — 환경변수 설정 + 보안 헬퍼 (인증/레이트리밋/IP제한/회전QR 챌린지)
- `serve.py` — 운영용 WSGI 실행 (waitress)
- `attendance.db` — 실행 시 자동 생성 (암호화됨)

## 실행
```powershell
pip install -r requirements.txt
$env:ATTENDANCE_DB_KEY           = "강한-DB키"     # 운영 필수
$env:ATTENDANCE_SECRET_KEY       = "세션서명키"     # 운영 필수
$env:ATTENDANCE_TEACHER_PASSWORD = "교사비번"       # 운영 필수
python app.py            # 개발 서버
# 또는 운영: python serve.py   (waitress)
```
서버: http://localhost:5000 → `/login` 교사 비밀번호 입력

## 인증 방식: 학생별 개인 TOTP
학생이 본인 인증앱(Google Authenticator 등) 코드로 출석. **대리출석 차단** —
본인 secret 없으면 코드 생성 불가.

### 사용 순서
1. 교사: `/students` → 학번·이름 등록 → 등록 QR 표시
2. 학생: 등록 QR을 폰 카메라로 스캔 → 브라우저(`/setup`)가 secret을 **이 기기에 저장**
   (앱 설치 불필요. Google Authenticator 쓰려면 enroll 페이지의 otpauth QR/수동키 사용)
3. 교사: 세션 생성 (이름만, 필요 시 현장확인 체크)
4. 학생: 수업 QR 스캔 → **학번·코드 자동 입력·자동 제출** (브라우저가 TOTP 계산)
   미등록 기기는 학번 + 코드 수동 입력 폴백

### 기기 등록 = 브라우저가 인증기
- 학생 폰 브라우저가 secret을 `localStorage`에 보관 → 페이지에서 TOTP 자동 계산
  (`static/attendance.js`: base32 + WebCrypto HMAC-SHA1, pyotp 호환).
- 출석은 `fetch`로 제출 → 페이지 이동 없이 결과 표시.
- 보안 메모: secret이 기기 브라우저에 저장됨(인증앱과 동급 신뢰). 공용 PC 사용 금지,
  기기 분실 시 교사가 해당 학생 재등록(새 secret 발급).

## 현장 확인 (회전 QR, 선택) — 원격 출석 차단
세션 생성 시 "현장 확인" 체크 시:
- 교사 화면에 **회전 QR** 표시 (기본 10초마다 갱신, HMAC 챌린지 내장 → 위조 불가).
- 학생은 **지금 화면의 QR을 카메라로 스캔**해야 출석 페이지 진입 (챌린지가 URL `?c=`로 전달).
- 만료/위조 챌린지는 거부 → 원격 학생은 화면을 못 봐 유효 챌린지를 얻지 못함.

이중 방어:
- **현장**: 회전 QR 챌린지 (교실 화면에만, 금방 만료) → 원격 차단
- **본인**: 개인 TOTP → 대리 차단

암호 설계: 챌린지 = `세션ID.epoch.HMAC(SECRET_KEY, "세션ID:epoch")`. 서버가 재계산해 현재/직전
epoch만 허용. 회전 QR 이미지(`/qrc/<id>`)는 교사 로그인 필요 → 외부에서 챌린지 못 빼감.

**잔여 한계**: 교실 안 공범이 QR을 실시간 중계하면 원격 서명 가능(릴레이). 완전 차단은
거리한정(distance bounding) 전용 하드웨어 영역. 회전 주기를 짧게 해 릴레이 난이도를 높임.
조정: `ATTENDANCE_QR_ROTATE_SEC` (기본 10).

## HTTPS (운영)
- 운영은 HTTPS 필수 (없으면 코드·비번 평문 전송).
  개발: `$env:ATTENDANCE_SSL="1"; python app.py` (adhoc 자체서명).
  운영: 리버스 프록시(nginx/caddy)로 HTTPS 종단.

## 환경변수
| 변수 | 용도 | 미설정 시 |
|---|---|---|
| `ATTENDANCE_DB_KEY` | SQLCipher DB 암호화 키 | 개발용 기본키 + 경고 |
| `ATTENDANCE_SECRET_KEY` | Flask 세션 쿠키 서명 | 임시 랜덤(재시작 시 로그인 풀림) |
| `ATTENDANCE_TEACHER_PASSWORD` | 교사 로그인 비번 | `admin` + 경고 |
| `ATTENDANCE_ALLOWED_SUBNETS` | 출석 허용 IP 대역(쉼표, 예: `192.168.0.,10.0.`) | 제한 없음 |
| `ATTENDANCE_ISSUER` | 인증앱 표시 발급자명 | `출석` |
| `ATTENDANCE_QR_ROTATE_SEC` | 회전 QR 챌린지 갱신 주기(초) | `10` |
| `ATTENDANCE_RATE_MAX_FAILS` | 레이트리밋 실패 한도 | `5` |
| `ATTENDANCE_RATE_WINDOW_SEC` | 레이트리밋 윈도우(초) | `60` |
| `ATTENDANCE_SSL` | `1` 이면 adhoc HTTPS | off |
| `ATTENDANCE_DEBUG` | `1` 이면 디버그 모드 | off |
| `ATTENDANCE_PORT` | 포트 | `5000` |

## 보안
- **DB 암호화**: SQLCipher AES-256, DB 파일 전체 암호화 — 일반 `sqlite3`로 못 엶.
  키 분실 = 복구 불가. 키를 코드/git에 넣지 말 것.
- **교사 인증**: 모든 교사 라우트 로그인 필요. `/check`·`/qr`만 공개. `/api/code`도 보호 → 외부서 코드 못 읽음.
- **무차별 대입 방지**: `(session_id, IP, 학번)`당 슬라이딩 윈도우 레이트리밋
  (공용 IP 라도 한 명 실패가 반 전체를 잠그지 않음).
- **네트워크 제한**: `ATTENDANCE_ALLOWED_SUBNETS` IP 대역 (프록시 뒤면 `X-Forwarded-For` 참조).
- **상수시간 비번 비교**: `hmac.compare_digest`.
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
- `sessions(id, name, secret, interval, created_at, open, mode, geo_lat, geo_lon, geo_radius)`
- `students(student_id PK, name, secret, created_at)` — 개인 TOTP 등록
- `attendance(id, session_id, student_id, student_name, checked_at, ip, lat, lon)` + UNIQUE(session_id, student_id)

## 같은 와이파이에서 학생 폰 접속
서버는 `0.0.0.0` 바인드. 학생은 `http://<교사PC IP>:5000/check/<id>` 접속.
QR은 접속 호스트 기준 URL 생성 → PC IP로 서버 열고 그 화면의 QR 사용.

## 남은 한계
- 개인 TOTP secret을 학생이 타인과 공유하면 여전히 대리 가능 (인적 통제 영역).
- 레이트리밋은 인메모리 → 다중워커/재시작 시 초기화. 다중워커 운영이면 Redis 등 공유저장소 필요.
- 운영은 HTTPS 필수 (없으면 코드·비번 평문 전송).
