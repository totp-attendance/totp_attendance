# TOTP 출석 프로그램

학생별 개인 TOTP(Google Authenticator 등)로 출석 체크. 대리출석 차단 + 위치 제한(선택) + DB 암호화.

## 구성
- `app.py` — Flask 웹 서버 (교사화면 + 학생출석 + 학생관리 + 출석부/CSV)
- `db.py` — SQLCipher 저장소 (DB 파일 전체 암호화 + 스키마 마이그레이션)
- `config.py` — 환경변수 설정 + 보안 헬퍼 (인증/레이트리밋/IP제한/지오펜스)
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
2. 학생: QR을 Google Authenticator / Authy 로 스캔 (1회, 최초만)
3. 교사: 세션 생성 (이름만 입력, 필요 시 위치제한 체크)
4. 학생: 출석 페이지에서 학번 + 본인 앱 코드 입력 (이름은 등록정보서 자동)

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

## 위치 제한 (지오펜스, 선택)
- 세션 생성 시 위도·경도·반경(m) 지정 (또는 "현재 위치 사용" 버튼).
- 학생 출석 시 브라우저 위치 권한 요청 → 반경 내(haversine 거리)만 허용.
- **브라우저 위치는 보안 컨텍스트 필요** → `localhost` 외에는 HTTPS 필수.
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
- **무차별 대입 방지**: `(session_id, IP)`당 슬라이딩 윈도우 레이트리밋.
- **네트워크 제한**: `ATTENDANCE_ALLOWED_SUBNETS` IP 대역 (프록시 뒤면 `X-Forwarded-For` 참조).
- **상수시간 비번 비교**: `hmac.compare_digest`.
- **감사 로그**: 출석마다 IP·위치(있으면) 기록 → 출석부/CSV에 표시.

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
- 지오펜스는 위치 스푸핑(개발자도구/모의위치 앱)에 취약 — 보조 수단으로 사용.
- 레이트리밋은 인메모리 → 다중워커/재시작 시 초기화. 다중워커 운영이면 Redis 등 공유저장소 필요.
- 운영은 HTTPS 필수 (없으면 코드·비번 평문 전송).
