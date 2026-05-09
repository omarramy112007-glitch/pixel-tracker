# outreach_engine/core/reply_monitor.py

from __future__ import annotations

import asyncio
from fastapi import FastAPI, Request

from outreach_engine.tracking.gmail_watcher import check_for_replies, start_reply_polling

app = FastAPI(title="Outreach Engine Reply Monitor")


@app.get("/health")
async def health():
    return {"status": "ok"}


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


if __name__ == "__main__":
    asyncio.run(start_reply_polling())