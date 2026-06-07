"""Vercel 서버리스 진입점. 루트의 Flask app(WSGI)을 노출."""
import os
import sys

# 레포 루트를 import 경로에 추가 (app.py / db.py / views/ 접근)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (Vercel @vercel/python 이 'app' WSGI 감지)

__all__ = ["app"]
