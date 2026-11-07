"""Standalone worker process entry point.

Usage:
    AGP_DATABASE_URL=... python -m app.worker
"""

from __future__ import annotations

from app.core.config import get_settings
from app.workers.runner import run_worker_process


def main() -> None:
    settings = get_settings()
    import asyncio

    asyncio.run(run_worker_process(settings))


if __name__ == "__main__":
    main()
