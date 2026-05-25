# outreach_engine/core/reply_classifier.py

from __future__ import annotations

import re
from typing import Any, Dict, Optional

INTERESTED = "INTERESTED"
NOT_INTERESTED = "NOT_INTERESTED"
QUESTION = "QUESTION"
AUTO_REPLY = "AUTO_REPLY"

_ALL_LABELS = {INTERESTED, NOT_INTERESTED, QUESTION, AUTO_REPLY}


_AUTO_REPLY_PATTERNS = [
    r"\bout of office\b",
    r"\booo\b",
    r"\bauto-?reply\b",
    r"\bautomated response\b",
    r"\baway from the office\b",
    r"\bi am away\b",
    r"\bi'm away\b",
    r"\bvacation\b",
    r"\breturning on\b",
    r"\bnot available\b",
    r"\bdo not reply\b",
]

_INTERESTED_PATTERNS = [
    r"\binterested\b",
    r"\bsounds good\b",
    r"\btell me more\b",
    r"\bmore info\b",
    r"\bsend me\b",
    r"\bi'd like\b",
    r"\bi would like\b",
    r"\blet's talk\b",
    r"\blet us talk\b",
    r"\blet's connect\b",
    r"\bschedule\b",
    r"\bbook a call\b",
    r"\bbook a meeting\b",
    r"\byes please\b",
    r"\bsure\b",
]

_NOT_INTERESTED_PATTERNS = [
    r"\bnot interested\b",
    r"\bno thanks\b",
    r"\bno thank you\b",
    r"\bnot now\b",
    r"\bnot right now\b",
    r"\bpass\b",
    r"\bstop emailing\b",
    r"\bremove me\b",
    r"\bopt out\b",
    r"\bopt-out\b",
    r"\bunsubscribe\b",
    r"\bdo not contact\b",
    r"\bdon't contact\b",
]

_QUESTION_PATTERNS = [
    r"\bhow does\b",
    r"\bhow do\b",
    r"\bwhat is\b",
    r"\bwhat are\b",
    r"\bcan you\b",
    r"\bcould you\b",
    r"\bwould you\b",
    r"\bis it\b",
    r"\bdoes it\b",
    r"\bdo you\b",
    r"\bcan this\b",
    r"\bwhat about\b",
]


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _has_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _looks_like_question(text: str) -> bool:
    if "?" in text:
        return True
    return _has_any(text, _QUESTION_PATTERNS)


def classify_reply(
    subject: str = "",
    body: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Classify a reply into one of:
      - INTERESTED
      - NOT_INTERESTED
      - QUESTION
      - AUTO_REPLY
    """
    metadata = metadata or {}

    parts = [
        _normalize_text(subject),
        _normalize_text(body),
        _normalize_text(metadata.get("snippet")),
        _normalize_text(metadata.get("reply_text")),
        _normalize_text(metadata.get("message")),
    ]
    text = " ".join(p for p in parts if p).strip()

    header_candidates = " ".join(
        _normalize_text(metadata.get(k))
        for k in ("headers", "content_type", "mailbox", "sender_name")
    ).strip()

    # Auto-reply first, because auto replies often contain generic text
    if _has_any(text, _AUTO_REPLY_PATTERNS) or _has_any(header_candidates, _AUTO_REPLY_PATTERNS):
        return AUTO_REPLY

    # Explicit negative intent
    if _has_any(text, _NOT_INTERESTED_PATTERNS):
        return NOT_INTERESTED

    # Strong positive intent
    if _has_any(text, _INTERESTED_PATTERNS):
        return INTERESTED

    # Questions are usually sales-positive or at least engagement-worthy
    if _looks_like_question(text):
        return QUESTION

    # Safe default: keep it in the human-review / follow-up lane
    return QUESTION


def classify_reply_event(reply: Dict[str, Any]) -> str:
    """
    Convenience wrapper for webhook/poller payloads.
    """
    if not isinstance(reply, dict):
        return QUESTION

    subject = reply.get("subject") or ""
    body = reply.get("body") or reply.get("snippet") or reply.get("message") or ""
    return classify_reply(subject=subject, body=body, metadata=reply)


def is_valid_label(value: str) -> bool:
    return value in _ALL_LABELS
