"""Structured logging (structlog) with secret redaction.

JSON output in production and in any environment where ``AGP_LOG_JSON`` is
set; pretty console output locally.  A redaction processor masks well-known
secret-bearing keys anywhere in the event payload, and the request-context
middleware binds ``request_id`` / ``user_id`` context variables so every log
line inside a request is correlated.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.config import Settings

REDACTED = "****"

_REDACT_KEYS = {
    "password",
    "password_hash",
    "new_password",
    "current_password",
    "api_key",
    "apikey",
    "authorization",
    "token",
    "access_token",
    "refresh_token",
    "reset_token",
    "secret",
    "secret_key",
    "client_secret",
    "x-api-key",
    "cookie",
    "set-cookie",
    "session_token",
    "s3_secret_access_key",
}


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (REDACTED if str(k).lower() in _REDACT_KEYS else _redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(v) for v in value]
    return value


def redact_processor(
    logger: Any, method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    return _redact_value(event_dict)


def configure_logging(settings: Settings) -> None:
    """Configure stdlib + structlog exactly once per process."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer()
            if not settings.log_json
            else structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Quiet down noisy third-party loggers without silencing errors.
    for noisy in ("uvicorn.access", "httpx", "httpcore", "alembic"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))


def get_logger(name: str | None = None) -> Any:
    return structlog.stdlib.get_logger(name)
