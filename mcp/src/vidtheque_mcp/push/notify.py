"""The rule that turns a stored verdict into one push (companion.md §6).

A verdict notifies once (`notified_at`), when it meets the threshold, and only
for a video published in the last few days: the verdict backfill judges old
videos through the same stage, and a phone that buzzed for each of them would
be switched off by evening.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Protocol

import httpx2 as httpx

from ..config import ConfigError, _env, _int_env
from ..db import Database
from ..verdicts import store
from .fcm import Delivery, FcmSender, ServiceAccount

log = logging.getLogger(__name__)

FRESH_S = 3 * 86_400
REASON_CHARS = 160


class Sender(Protocol):
    async def send(self, device_token: str, data: dict[str, str]) -> Delivery: ...


@dataclass(frozen=True)
class PushSettings:
    credentials: str | None
    min_score: int = 3

    @classmethod
    def from_env(cls) -> "PushSettings":
        min_score = _int_env("VIDTHEQUE_NOTIFY_MIN_SCORE", 3)
        if not 0 <= min_score <= 3:
            raise ConfigError(f"VIDTHEQUE_NOTIFY_MIN_SCORE must be 0-3, got {min_score}")
        path = _env("VIDTHEQUE_FCM_CREDENTIALS") or None
        if path and not os.path.isfile(path):
            raise ConfigError(f"VIDTHEQUE_FCM_CREDENTIALS names {path!r}, which is not a file")
        return cls(path, min_score)


def build_notifier(db: Database, http: httpx.AsyncClient) -> "Notifier | None":
    """None when VIDTHEQUE_FCM_CREDENTIALS is unset: no key, no push."""
    settings = PushSettings.from_env()
    if not settings.credentials:
        return None
    return Notifier(db, FcmSender(ServiceAccount.load(settings.credentials), http), settings.min_score)


class Notifier:
    def __init__(self, db: Database, sender: Sender, min_score: int, clock=time.time) -> None:
        self.db = db
        self.sender = sender
        self.min_score = min_score
        self.clock = clock

    async def after_verdict(self, video_id: int) -> int:
        """Push the video's verdict to every device if it is due; answers the phones reached."""
        due = await self.db.read(lambda c: self._due(c, video_id))
        if due is None:
            return 0
        data, tokens = due
        reached = 0
        gone: list[str] = []
        for token in tokens:
            outcome = await self.sender.send(token, data)
            if outcome is Delivery.SENT:
                reached += 1
            elif outcome is Delivery.GONE:
                gone.append(token)

        def record(c: sqlite3.Connection) -> None:
            c.executemany("DELETE FROM devices WHERE token = ?", [(t,) for t in gone])
            if reached:
                c.execute("UPDATE verdicts SET notified_at = ? WHERE video_id = ?", (int(self.clock()), video_id))

        await self.db.write(record)
        if gone:
            log.info("push: forgot %d device(s) FCM no longer knows", len(gone))
        return reached

    def _due(self, c: sqlite3.Connection, video_id: int) -> tuple[dict[str, str], list[str]] | None:
        row = c.execute(
            "SELECT v.score, v.reason, v.moments, v.notified_at, d.public_id, d.title, d.channel_name, d.published_at"
            " FROM verdicts v JOIN videos d ON d.id = v.video_id WHERE v.video_id = ?",
            (video_id,),
        ).fetchone()
        if row is None or row["notified_at"] is not None or int(row["score"]) < self.min_score:
            return None
        if row["published_at"] is None or self.clock() - int(row["published_at"]) > FRESH_S:
            return None
        tokens = [r["token"] for r in c.execute("SELECT token FROM devices ORDER BY last_seen DESC")]
        if not tokens:
            return None
        moments = store.moments_of(row)
        best = moments[0] if moments else None
        data = {
            "video_id": str(row["public_id"]),
            "title": str(row["title"] or ""),
            "channel": str(row["channel_name"] or ""),
            "score": str(int(row["score"])),
            "reason": str(row["reason"] or "")[:REASON_CHARS],
        }
        if best is not None:
            data["moment_s"] = str(int(best.offset_s))
            data["moment_why"] = best.why[:REASON_CHARS]
        return data, tokens
