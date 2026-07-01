#!/usr/bin/env bash
set -e

# Install dependencies explicitly. Nixpacks should do this from
# requirements.txt, but if it didn't, the deploy crashes with
# "No module named uvicorn" — this script makes the install
# unconditional and visible in the deploy log.
echo "=== Installing Python dependencies ==="
pip install --no-cache-dir -r requirements.txt

echo "=== Starting pixel tracker ==="
exec python -m uvicorn outreach_engine.tracking.pixel_server:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}"
