"""Building the brief on Sunday morning and pushing it once (companion.md §6.1).

Rides the job runner's poll tick like the nightly update. On Sunday from
`VIDTHEQUE_BRIEF_HOUR`, the week's brief is built if it does not exist, then
pushed if it was not. The one model call ("what speakers said") is optional:
with no model, or a failed call, the brief is built without that section and
says why, rather than arriving late.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime
from typing import Any, Callable

import httpx2 as httpx

from ..config import ConfigError, _bool_env, _int_env
from ..llm import LLMSettings, LLMUnavailable, Model, build_model, is_configured
from . import build

logger = logging.getLogger(__name__)

SUNDAY = 6
CHECK_EVERY_S = 60


class Weekly:
    def __init__(
        self,
        db: Any,
        model: Model | None,
        label: str | None,
        notifier: Any | None = None,
        *,
        hour: int = 9,
        owner_id: int = 1,
        clock: Callable[[], datetime] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.db = db
        self.model = model
        self.label = label
        self.notifier = notifier
        self.hour = hour
        self.owner_id = owner_id
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.rng = rng or random.Random()
        self._task: asyncio.Task[None] | None = None
        self._next_check = 0.0

    async def tick(self) -> None:
        """The runner's pre-claim hook: build and push on Sunday, in the background."""
        if self._task is not None and not self._task.done():
            return
        now = self.clock()
        if now.weekday() != SUNDAY or now.hour < self.hour or now.timestamp() < self._next_check:
            return
        self._next_check = now.timestamp() + CHECK_EVERY_S
        self._task = asyncio.create_task(self._guarded(), name="vidtheque-brief")

    async def aclose(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _guarded(self) -> None:
        try:
            await self.run_once()
        except Exception:  # the tick's task must not die loudly every minute
            logger.exception("weekly brief failed")

    async def run_once(self) -> str | None:
        """This week's brief: built if missing, pushed if not yet. Answers the week key."""
        week = build.week_of(self.clock())
        exists = await self.db.read(
            lambda c: c.execute(
                "SELECT pushed_at FROM briefs WHERE owner_id = ? AND week = ?", (self.owner_id, week.key)
            ).fetchone()
        )
        if exists is None:
            await self.build(week)
        elif exists["pushed_at"] is not None:
            return week.key
        await self._push(week)
        return week.key

    async def build(self, week: build.Week) -> bool:
        picks, audit, topics = await self.db.read(
            lambda c: (
                build.picks(c, week, self.owner_id),
                build.audit_picks(c, week, self.rng, self.owner_id),
                build.said_inputs(c, week, self.owner_id),
            )
        )
        said, note, model = await self._said(topics)
        body = {"picks": picks, "audit": audit, "said": said, "said_note": note}
        stored = await self.db.write(lambda c: build.store(c, week, body, model, self.owner_id))
        logger.info("weekly brief %s: %d pick(s), %d audit, %s", week.key, len(picks), len(audit), note or "said written")
        return stored

    async def _said(self, topics: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None, str | None]:
        if not topics:
            return [], "none of your top entries came up this week", None
        if self.model is None:
            return [], "no model is configured", None
        try:
            answer = await self.model.complete(
                build.said_prompt(topics), system=build.SAID_SYSTEM, schema=build.SAID_SCHEMA, purpose="weekly_brief"
            )
        except LLMUnavailable as exc:
            logger.warning("weekly brief: the model is unavailable (%s)", exc.reason)
            return [], "the model call failed", None
        return build.check_said(topics, answer), None, self.label

    async def _push(self, week: build.Week) -> None:
        if self.notifier is not None:
            line = await self.db.read(lambda c: _line(c, week.key, self.owner_id))
            # No phone reached (FCM down, none registered): the next tick tries again.
            if not await self.notifier.brief(week.key, line):
                return
        await self.db.write(
            lambda c: c.execute(
                "UPDATE briefs SET pushed_at = unixepoch() WHERE owner_id = ? AND week = ?", (self.owner_id, week.key)
            )
        )


def _line(conn: Any, week: str, owner_id: int) -> str:
    """The push's one line: the week's top pick, or that there was none."""
    row = conn.execute("SELECT body FROM briefs WHERE owner_id = ? AND week = ?", (owner_id, week)).fetchone()
    picks = json.loads(row["body"]).get("picks", []) if row else []
    title = conn.execute(
        "SELECT title FROM videos WHERE owner_id = ? AND public_id = ?", (owner_id, picks[0])
    ).fetchone() if picks else None
    if title is None:
        return "Nothing scored worth your time this week."
    more = f" and {len(picks) - 1} more" if len(picks) > 1 else ""
    return f"Top pick: {title[0]}{more}."


def build_weekly(db: Any, notifier: Any | None) -> tuple[Weekly | None, httpx.AsyncClient | None]:
    """The weekly brief and the HTTP client its model owns, or (None, None) when off.

    On unless `VIDTHEQUE_BRIEF=0`; the model is optional (see the module doc).
    """
    if not _bool_env("VIDTHEQUE_BRIEF", True):
        return None, None
    hour = _int_env("VIDTHEQUE_BRIEF_HOUR", 9)
    if not 0 <= hour <= 23:
        raise ConfigError(f"VIDTHEQUE_BRIEF_HOUR must be 0–23, got {hour}")
    settings = LLMSettings.from_env()
    if not is_configured(settings):
        return Weekly(db, None, None, notifier, hour=hour), None
    http = httpx.AsyncClient() if settings.backend == "api" else None
    model = build_model(settings, http, db)  # type: ignore[arg-type]
    return Weekly(db, model, f"{settings.backend}:{settings.model or 'default'}", notifier, hour=hour), http
