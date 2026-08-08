"""``vidtheque-worker`` entry point."""

from __future__ import annotations

import logging

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(
        "vidtheque_worker.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
