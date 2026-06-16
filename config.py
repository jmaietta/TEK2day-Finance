"""
Configuration for the Yahoo Finance data pipeline.

Reads from environment variables or .env file.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

FIRESTORE_PROJECT = os.getenv("FIRESTORE_PROJECT", "").strip()
CEORATER_API_KEY = os.getenv("CEORATER_API_KEY", "").strip()
TEK2DAY_API_URL = os.getenv(
    "TEK2DAY_API_URL",
    "https://tek2day-api-568356743692.us-central1.run.app",
).strip().rstrip("/")
# ── Firebase Auth (yfinance-cli project) — used by auth.py ──────────────────
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "yfinance-cli").strip()
FIREBASE_WEB_API_KEY = os.getenv("FIREBASE_WEB_API_KEY", "").strip()
FIREBASE_AUTH_DOMAIN = os.getenv("FIREBASE_AUTH_DOMAIN", "yfinance-cli.firebaseapp.com").strip()
FIREBASE_APP_ID = os.getenv("FIREBASE_APP_ID", "").strip()
FIREBASE_MESSAGING_SENDER_ID = os.getenv("FIREBASE_MESSAGING_SENDER_ID", "").strip()
FIREBASE_STORAGE_BUCKET = os.getenv("FIREBASE_STORAGE_BUCKET", "").strip()
COOKIE_NAME = os.getenv("COOKIE_NAME", "t2d_sid").strip()
SESSION_TTL_DAYS = int(os.getenv("SESSION_TTL_DAYS", "14"))

COLLECTION_ROOT = "tickers"

# Rate limiting: delay between individual ticker fetches (seconds)
FETCH_DELAY = 0.5

# yfinance batch size for price downloads
PRICE_BATCH_SIZE = 50
