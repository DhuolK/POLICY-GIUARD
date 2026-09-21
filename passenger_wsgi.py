"""Passenger WSGI entry point for cPanel "Setup Python App" (Truehost).

cPanel's Phusion Passenger imports this file and serves ``application``.
Do NOT use run.py in production — that is the Flask dev server only.

Setup (cPanel → Setup Python App):
  - Python version: 3.10+ (whatever Truehost offers, >= 3.10)
  - Application root: the folder containing this file (repo root)
  - Application startup file: passenger_wsgi.py
  - Application Entry point: application
  - Environment variables: set in the cPanel UI OR in a .env file here
    (load_dotenv below picks it up — .env must NOT be web-accessible;
    keep it in the app root, outside public_html).

Required env vars (see .env.example):
  FLASK_ENV=production
  SECRET_KEY=<>=32 random chars — generate with:
      python -c "import secrets; print(secrets.token_urlsafe(48))"
  MONGO_URI=mongodb+srv://...   (MongoDB Atlas — shared hosts have no MongoDB)
  MONGO_DB_NAME=policy_guard
  APP_BASE_URL=https://<your-domain>
  AT_USERNAME / AT_API_KEY / AT_SENDER_ID / SMS_SIMULATE=0
  MPESA_ENV=production + Daraja credentials + C2B URLs
"""
import os
import sys

# Ensure the app root (this file's directory) is importable regardless of
# the working directory Passenger launches from.
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(APP_ROOT, '.env'))

# Hard-default to production so a missing FLASK_ENV can never boot the
# development config (debug mode, insecure cookies) on the live server.
os.environ.setdefault('FLASK_ENV', 'production')

from app import create_app  # noqa: E402

application = create_app()
