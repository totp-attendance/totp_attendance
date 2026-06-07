"""운영용 WSGI 실행 (waitress).

개발 서버(app.run) 대신 사용. HTTPS 는 앞단 리버스 프록시(nginx/caddy)
또는 개발 시 `ATTENDANCE_SSL=1 python app.py` (adhoc) 로 처리.

실행:
  $env:ATTENDANCE_DB_KEY="..."; $env:ATTENDANCE_SECRET_KEY="..."
  $env:ATTENDANCE_ADMIN_USER="admin"; $env:ATTENDANCE_ADMIN_PASSWORD="..."
  python serve.py
"""
from waitress import serve

import config
from app import app

if __name__ == "__main__":
    print(f"waitress 시작 : 0.0.0.0:{config.PORT}")
    serve(app, host="0.0.0.0", port=config.PORT)
