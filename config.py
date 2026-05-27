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
COLLECTION_ROOT = "tickers"

# Rate limiting: delay between individual ticker fetches (seconds)
FETCH_DELAY = 0.5

# yfinance batch size for price downloads
PRICE_BATCH_SIZE = 50
