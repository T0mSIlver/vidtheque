"""`vidtheque-mcp verdicts backfill` — queue verdicts for videos indexed before them.

Never automatic (companion.md §3.2): an owner runs it, a batch at a time. It
only queues `verdict` jobs; the running server's job runner writes them.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from ..config import ConfigError, Settings
from ..db import Database
from ..db.queries import lookup_video_ids
from . import store
from .stage import configured


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="vidtheque-mcp verdicts")
    sub = parser.add_subparsers(dest="command", required=True)
    backfill = sub.add_parser(
        "backfill",
        help="queue verdicts for indexed videos without one, newest first",
    )
    backfill.add_argument(
        "--limit", type=int, default=25, help=f"videos to queue this run (1-{store.BACKFILL_MAX})"
    )
    backfill.add_argument(
        "--video",
        action="append",
        default=[],
        metavar="VIDEO_ID",
        help="queue this video even if it has a verdict; repeatable",
    )
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
        if configured() is None:
            print(
                "verdicts are off: set VIDTHEQUE_LLM_BASE_URL and VIDTHEQUE_LLM_MODEL "
                "(or another VIDTHEQUE_LLM_BACKEND), and leave VIDTHEQUE_VERDICTS on.",
                file=sys.stderr,
            )
            return 2
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if not settings.db_path.exists():
        print(f"no database at {settings.db_path}", file=sys.stderr)
        return 2
    return asyncio.run(_backfill(settings, args.limit, args.video))


async def _backfill(settings: Settings, limit: int, videos: list[str]) -> int:
    db = Database(path=settings.db_path)
    await db.open()
    try:
        ids: list[int] = []
        if videos:
            found = await db.read(lambda c: lookup_video_ids(c, videos))
            missing = [v for v in videos if v not in found]
            if missing:
                print("not in the corpus: " + ", ".join(missing), file=sys.stderr)
                return 1
            ids = list(found.values())
        jobs, waiting = await db.write(lambda c: store.backfill(c, limit, ids))
    finally:
        await db.close()
    print(f"queued {len(jobs)} verdict job(s); {waiting} more video(s) to queue.")
    if waiting:
        print("run the same command again to queue the next batch.")
    return 0
