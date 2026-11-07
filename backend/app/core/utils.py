"""Small shared helpers (time, ids, hashing, text)."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC timestamp.

    Convention: every timestamp persisted by the platform is naive UTC
    (``TIMESTAMP WITHOUT TIME ZONE`` in PostgreSQL).  This keeps SQLite and
    PostgreSQL behaviour identical and avoids ambiguous local times.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def uuid_str() -> str:
    return str(uuid.uuid4())


def new_token() -> str:
    """URL-safe high-entropy opaque token (refresh tokens, reset tokens...)."""
    return secrets.token_urlsafe(48)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate (chars/4).

    Used only for budget accounting and telemetry when the model runtime does
    not report usage; never presented as the model's own count.
    """
    return max(1, len(text) // 4) if text else 0


def truncate_text(text: str, limit: int, marker: str = "\n…[truncated]") -> str:
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(marker))
    return text[:keep] + marker


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))
