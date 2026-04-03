# lead_engine/config.py

import os
from dotenv import load_dotenv

load_dotenv()

# -----------------------------
# API RATE LIMITS (Daily)
# -----------------------------
API_LIMITS = {
    "apollo": int(os.getenv("APOLLO_LIMIT", 1000)),
    "builtwith": int(os.getenv("BUILTWITH_LIMIT", 800)),
    "explorium": int(os.getenv("EXPLORIUM_LIMIT", 500)),
    "rocketreach": int(os.getenv("ROCKETREACH_LIMIT", 500)),
    "serpapi": int(os.getenv("SERPAPI_LIMIT", 1000)),
    "thecompanies": int(os.getenv("THECOMPANIES_LIMIT", 1000)),
    "zoho": int(os.getenv("ZOHO_LIMIT", 500)),
    "leadgibbon": int(os.getenv("LEADGIBBON_LIMIT", 500)),
    "leadiq": int(os.getenv("LEADIQ_LIMIT", 500)),
    "voilanorbert": int(os.getenv("VOILANORBERT_LIMIT", 500)),
    "getprospect": int(os.getenv("GETPROSPECT_LIMIT", 500)),
    "prospero": int(os.getenv("PROSPERO_LIMIT", 500)),
}

# -----------------------------
# PROXY CONFIG
# -----------------------------
PROXIES = [
    p.strip()
    for p in os.getenv("PROXIES", "").split(",")
    if p.strip()
]

# -----------------------------
# RETRY CONFIG
# -----------------------------
RETRY_LIMIT = int(os.getenv("RETRY_LIMIT", 3))
RETRY_BACKOFF_BASE = int(os.getenv("RETRY_BACKOFF_BASE", 2))

# -----------------------------
# CACHE CONFIG
# -----------------------------
CACHE_TTL = int(os.getenv("CACHE_TTL", 3600))  # seconds

# -----------------------------
# CONCURRENCY
# -----------------------------
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", 10))

# -----------------------------
# REQUEST TIMEOUTS
# -----------------------------
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", 30))

# -----------------------------
# LOGGING
# -----------------------------
DEBUG = os.getenv("DEBUG", "True").strip().lower() == "true"

# -----------------------------
# DAY 0 / SENDING CONTROLS
# -----------------------------
TEST_MODE = os.getenv("TEST_MODE", "False").strip().lower() == "true"
TEST_EMAIL = os.getenv("TEST_EMAIL", "").strip().lower()

# Anti-spam delay between emails
MIN_SEND_DELAY_SECONDS = int(os.getenv("MIN_SEND_DELAY_SECONDS", 120))
MAX_SEND_DELAY_SECONDS = int(os.getenv("MAX_SEND_DELAY_SECONDS", 300))

# Quality rules
REQUIRE_FIRST_NAME = os.getenv("REQUIRE_FIRST_NAME", "True").strip().lower() == "true"
REQUIRE_EMAIL = os.getenv("REQUIRE_EMAIL", "True").strip().lower() == "true"
REQUIRE_COMPANY = os.getenv("REQUIRE_COMPANY", "True").strip().lower() == "true"