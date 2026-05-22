# outreach_engine/core/reply_monitor.py

from __future__ import annotations

import asyncio
import os
from typing import Optional

from fastapi import FastAPI, Request

from outreach_engine.tracking.gmail_watcher import (
    POLL_INTERVAL_SECONDS,
    WATCH_MODE,
    check_for_replies,
    start_reply_polling,
    start_watch,
)

app = FastAPI(title="Outreach Engine Reply Monitor")

GMAIL_WATCH_MODE = os.getenv("GMAIL_WATCH_MODE", WATCH_MODE).strip().lower()
POLL_TASK: Optional[asyncio.Task] = None


@app.on_event("startup")
async def on_startup():
    """
    On startup:
    - watch mode -> renew Gmail watch
    - poll mode  -> start background polling
    """
    global POLL_TASK

    if GMAIL_WATCH_MODE == "watch":
        try:
            result = start_watch()
            print(f"✅ Gmail watch renewed on startup: {result}")
        except Exception as e:
            print(f"⚠️ Gmail watch renewal failed on startup: {e}")
            print("⚠️ Falling back to poll mode for this session")
            if POLL_TASK is None:
                POLL_TASK = asyncio.create_task(start_reply_polling(POLL_INTERVAL_SECONDS))
    else:
        print(f"👂 Starting reply polling every {POLL_INTERVAL_SECONDS}s")
        if POLL_TASK is None:
            POLL_TASK = asyncio.create_task(start_reply_polling(POLL_INTERVAL_SECONDS))


@app.on_event("shutdown")
async def on_shutdown():
    global POLL_TASK
    if POLL_TASK:
        POLL_TASK.cancel()
        POLL_TASK = None


@app.get("/health")
async def health():
    return {"status": "ok", "mode": GMAIL_WATCH_MODE}


@app.get("/check")
async def check_now():
    processed = check_for_replies()
    return {
        "status": "ok",
        "processed": len(processed),
        "replies": processed,
    }


@app.post("/check")
async def check_now_post(request: Request):
    processed = check_for_replies()
    return {
        "status": "ok",
        "processed": len(processed),
        "replies": processed,
    }


@app.post("/renew-watch")
async def renew_watch():
    """
    Call this endpoint from a cron job every 6 days to keep watch alive.
    """
    try:
        result = start_watch()
        return {"status": "ok", "watch": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    asyncio.run(start_reply_polling(POLL_INTERVAL_SECONDS))
