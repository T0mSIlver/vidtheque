"""`vidtheque-mcp` — serve the ASGI app with uvicorn; `vidtheque-mcp verdicts …` for the backfill."""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from .app import build_app
from .config import ConfigError, Settings


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper())
    if sys.argv[1:2] == ["verdicts"]:
        from .verdicts.cli import main as verdicts_main

        return verdicts_main(sys.argv[2:])
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        # Configuration mistakes fail at boot, not at request time.
        logging.getLogger("vidtheque").error("configuration error: %s", exc)
        return 2
    uvicorn.run(
        build_app(settings),
        host=os.environ.get("VIDTHEQUE_HOST", "0.0.0.0"),  # container-local by default
        port=int(os.environ.get("VIDTHEQUE_PORT", "8080")),
        log_level=settings.log_level,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
