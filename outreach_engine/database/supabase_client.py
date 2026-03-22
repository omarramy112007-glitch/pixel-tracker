# outreach_engine/database/supabase_client.py

import os
from pathlib import Path
from supabase import create_client, Client
from dotenv import load_dotenv


# ---------------------------------------------------
# Load .env file explicitly
# ---------------------------------------------------

# Get project root (two levels up)
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env from project root
load_dotenv(BASE_DIR / ".env")


# ---------------------------------------------------
# Supabase Credentials
# ---------------------------------------------------

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")


if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "❌ Supabase credentials missing.\n"
        "Create a .env file in the root outreach_engine folder with:\n"
        "SUPABASE_URL=your_url\n"
        "SUPABASE_KEY=your_key"
    )


# ---------------------------------------------------
# Create Supabase Client
# ---------------------------------------------------

try:

    supabase: Client = create_client(
        SUPABASE_URL,
        SUPABASE_KEY
    )

    print("✅ Supabase client initialized")

except Exception as e:

    raise RuntimeError(
        f"❌ Failed to initialize Supabase client: {e}"
    )


# ---------------------------------------------------
# Helper Function
# ---------------------------------------------------

def get_supabase() -> Client:
    """
    Returns the global Supabase client.
    """
    return supabase