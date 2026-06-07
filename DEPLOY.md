# Vercel + Neon 배포 가이드

이 앱은 Vercel(서버리스) + Neon(PostgreSQL)로 배포한다. `DATABASE_URL` 이 있으면
자동으로 Postgres 백엔드를 쓴다(없으면 로컬 SQLCipher). 코드 변경 없이 환경변수만으로 전환.

## 0. 준비물
- GitHub 레포 (이미 푸시됨)
- Neon 계정 + 프로젝트 (무료)
- Vercel 계정 (GitHub 로그인)

## 1. Neon — DATABASE_URL 확보
1. https://neon.tech 가입 → **Create project** (Region: Seoul/Tokyo/Singapore).
2. 대시보드 **Connection string** → **Pooled connection** 토글 ON.
3. `postgresql://...-pooler...neon.tech/neondb?sslmode=require` 형태 문자열 복사.
   - ⚠️ 비밀번호 포함 → 외부 노출 금지. 노출됐으면 **Reset password** 로 교체.
4. 테이블은 만들 필요 없음 — 앱 첫 실행 시 자동 생성.

## 2. 비밀값 생성 (로컬에서)
```powershell
# 세션 서명 키 / 필드 암호화 키 (각각 다르게)
python -c "import secrets; print(secrets.token_hex(32))"   # ATTENDANCE_SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"   # ATTENDANCE_FIELD_KEY
# 관리자 비번은 강한 값으로 직접 정함
```

## 3. Vercel 프로젝트 생성
1. https://vercel.com → **Add New → Project** → GitHub 레포 import.
2. Framework Preset: **Other** (vercel.json 이 빌드 처리).
3. Build/Output 설정 건드릴 것 없음 (`vercel.json` + `api/index.py` 가 처리).

## 4. 환경변수 (Vercel → Settings → Environment Variables)
| 변수 | 값 | 필수 |
|---|---|---|
| `DATABASE_URL` | Neon **pooled** 연결 문자열 | ✅ |
| `ATTENDANCE_SECRET_KEY` | 위에서 생성한 hex | ✅ (없으면 인스턴스마다 로그인 풀림) |
| `ATTENDANCE_FIELD_KEY` | 위에서 생성한 hex (개인 TOTP 시드 암호화) | ✅ (없으면 시드 평문 저장) |
| `ATTENDANCE_ADMIN_USER` | 첫 관리자 아이디 (예: admin) | 권장 |
| `ATTENDANCE_ADMIN_PASSWORD` | 첫 관리자 비번 (강하게) | ✅ |
| `ATTENDANCE_HTTPS` | `1` (Secure 쿠키) | 권장 |
| `ATTENDANCE_QR_ROTATE_SEC` | 회전 QR 주기, 기본 10 | 선택 |
| `ATTENDANCE_TRUST_PROXY` | IP 허용목록 쓸 때만 `1` | 선택 |

- ⚠️ `ATTENDANCE_FIELD_KEY` 분실 = 기존 학생 시드 복호 불가 → 재등록 필요. 안전 보관.
- `ATTENDANCE_DB_KEY` 는 **Postgres 에선 미사용** (설정 안 함).

## 5. 배포
- **Deploy** 클릭 → 빌드(@vercel/python, requirements.txt 설치) → 첫 요청 시
  테이블 자동 생성 + 관리자 시드.
- 완료되면 `https://<프로젝트>.vercel.app`.

## 6. 첫 로그인 후
1. `https://<앱>.vercel.app/login` → `ATTENDANCE_ADMIN_USER`/`PASSWORD` 로 로그인.
2. `/admin` 에서 실제 교사 계정 생성, 필요 시 관리자 비번 교체.
3. 세션 생성 시 **현장 확인(회전 QR) 항상 ON** 권장 (공개 인터넷이라 회전 QR 이
   유일한 현장 방어 — 끄면 어디서나 출석 가능).

## 운영 한계 (서버리스 특성)
- **레이트리밋은 인메모리 → 인스턴스별/콜드스타트마다 초기화.** 강한 무차별 방어가
  필요하면 외부 저장소(예: Upstash Redis) 연동 필요.
- 콜드 스타트 시 첫 요청 지연 + 매 요청 DB 연결(Neon pooler 사용으로 완화).
- 정적 파일은 Flask 가 서빙(소규모 OK). 트래픽 크면 CDN/정적 호스팅 분리 고려.
- 회전 QR 실시간 릴레이(교실 공범)는 여전히 잔여 한계 (거리한정 HW 영역).

## 로컬 개발 (변경 없음)
```powershell
pip install -r requirements-local.txt   # SQLCipher 포함
.\run.ps1                                # DATABASE_URL 없으면 로컬 암호화 파일 DB
```
