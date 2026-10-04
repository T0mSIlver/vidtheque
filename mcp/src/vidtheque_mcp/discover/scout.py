"""The nightly scout — companion.md §6.2.

Once a night, on the job runner's poll tick, the scout searches YouTube for the
owner's three strongest entries, fetches one new video's captions per entry
and judges it on them; a pick scored 2 or more reaches the feed, at most 3 a
week. Then, once a week, it looks for a speaker to suggest (`speakers.py`).

YouTube blocks this box's IP now and then (research/ytdlp-usage-audit). A bot
check or a 429 stops the night at once, and blocked nights space the next runs
out; nothing retries harder. Every request the run makes is counted in
`scout_runs.requests`.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol

import httpx2 as httpx

from ..brief.build import Week, week_of
from ..config import ConfigError, _bool_env, _int_env
from ..llm import LLMSettings, LLMUnavailable, Model, build_model, is_configured
from ..pipeline.captions import CueDraft, cues_from_json3, cues_from_vtt
from ..pipeline.settings import PipelineSettings
from ..pipeline.sources import (
    RateLimited,
    SearchHit,
    SourceError,
    SubtitleTrack,
    YtDlpSource,
    parse_info,
)
from ..profile import store as profile_store
from ..verdicts.stage import SYSTEM, VERDICT_SCHEMA, middle_lines
from . import picks

logger = logging.getLogger(__name__)

ENTRIES = 3
RESULTS = 8
MIN_DURATION_S = 300
MAX_DURATION_S = 7_200
# About a quarter of a verdict's 40,000: the cheap verdict.
CAPTION_CHARS = 12_000
SHOW_MIN_SCORE = 2
# Nights skipped after 1, 2, 3, 4+ blocked nights in a row.
BACKOFF_NIGHTS = (1, 2, 4, 7)
CHECK_EVERY_S = 60


class ScoutSource(Protocol):
    def search(
        self, query: str, max_items: int, *, this_month: bool = False
    ) -> list[SearchHit]: ...

    def probe(self, url: str) -> dict[str, Any]: ...

    def fetch_subtitle(self, track: SubtitleTrack) -> str: ...


@dataclass(frozen=True)
class ScoutSettings:
    enabled: bool = True
    hour: int = 5

    @classmethod
    def from_env(cls) -> ScoutSettings:
        hour = _int_env("VIDTHEQUE_SCOUT_HOUR", 5)
        if not 0 <= hour <= 23:
            raise ConfigError(f"VIDTHEQUE_SCOUT_HOUR must be 0–23, got {hour}")
        return cls(enabled=_bool_env("VIDTHEQUE_SCOUT", True), hour=hour)


@dataclass
class Run:
    run_id: int
    week: Week
    requests: int = 0
    judged: int = 0
    shown: int = 0
    speaker: str | None = None
    notes: list[str] = field(default_factory=list)


class Scout:
    def __init__(
        self,
        db: Any,
        model: Model,
        label: str,
        source: ScoutSource,
        *,
        hour: int = 5,
        langs: tuple[str, ...] = ("en",),
        owner_id: int = 1,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.model = model
        self.label = label
        self.source = source
        self.hour = hour
        self.langs = langs
        self.owner_id = owner_id
        self.clock = clock or (lambda: datetime.now().astimezone())
        self._task: asyncio.Task[str | None] | None = None
        self._next_check = 0.0

    async def tick(self) -> None:
        """The runner's pre-claim hook: start tonight's run in the background when due."""
        if self._task is not None and not self._task.done():
            return
        now = self.clock()
        if now.hour < self.hour or now.timestamp() < self._next_check:
            return
        self._next_check = now.timestamp() + CHECK_EVERY_S
        self._task = asyncio.create_task(self._guarded(), name="vidtheque-scout")

    async def aclose(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _guarded(self) -> str | None:
        try:
            return await self.run_once()
        except Exception:  # the tick's task must not die loudly every minute
            logger.exception("scout run failed")
            return None

    async def run_once(self) -> str | None:
        """Tonight's run, if it is owed. Its final state, or None when not owed."""
        now = self.clock()
        at = int(now.timestamp())
        run = await self.db.write(lambda c: claim(c, now, self.owner_id))
        if run is None:
            return None
        from . import speakers

        state, error = "done", None
        try:
            await self._topics(run)
            await speakers.suggest(self, run)
        except RateLimited as exc:
            state, error = "blocked", str(exc)[:300]
            logger.warning("scout stopped by YouTube (%s); the next runs are spaced out", error)
        except LLMUnavailable as exc:
            state, error = "failed", f"model unavailable ({exc.reason})"
            logger.warning("scout: %s", error)
        except Exception as exc:  # noqa: BLE001 - the night is settled either way
            state, error = "failed", f"{type(exc).__name__}: {exc}"[:300]
            logger.exception("scout run failed")
        if state == "done" and run.requests == 0 and run.speaker is None:
            state = "idle"
        ended = int(self.clock().timestamp())
        await self.db.write(lambda c: finish(c, run, state, ended, error))
        logger.info(
            "scout %s: %d request(s), %d judged, %d shown, speaker %s",
            state,
            run.requests,
            run.judged,
            run.shown,
            run.speaker or "not tried",
        )
        return state

    # ----------------------------------------------------------- the topics

    async def _topics(self, run: Run) -> None:
        entries = await self.db.read(lambda c: _top_entries(c, self.owner_id))
        for entry_id, weight, text in entries:
            spent = await self.db.read(
                lambda c: picks.week_counts(c, run.week.key, run.week.since_at, self.owner_id)
            )
            if (
                spent["shown"] >= picks.SHOWN_PER_WEEK
                or spent["judged"] >= picks.JUDGED_PER_WEEK
                or spent["calls"] >= picks.CALLS_PER_WEEK
            ):
                return
            run.requests += 1
            hits = await asyncio.to_thread(self.source.search, text, RESULTS, this_month=True)
            hit = await self.db.read(lambda c: _first_new(c, hits, self.owner_id))
            if hit is None:
                continue
            await self._judge(run, hit, entry_id, text, shown_so_far=spent["shown"])

    async def _judge(
        self, run: Run, hit: SearchHit, entry_id: int, because: str, *, shown_so_far: int
    ) -> None:
        url = picks.youtu_be(hit.source_id)
        base: dict[str, Any] = {
            "source_id": hit.source_id,
            "week": run.week.key,
            "entry_id": entry_id,
            "because": because,
            "title": hit.title or hit.source_id,
            "channel_id": hit.channel_id,
            "channel_name": hit.channel_name,
            "channel_url": hit.channel_url,
            "duration_s": float(hit.duration_s or 0),
        }
        run.requests += 1
        try:
            meta = parse_info(await asyncio.to_thread(self.source.probe, url), url)
        except RateLimited:
            raise
        except SourceError as exc:
            # Gone, private or not out yet: never asked about again.
            run.notes.append(f"{hit.source_id}: {exc}")
            await self.db.write(
                lambda c: picks.insert(c, self.owner_id, **base, state="no_captions")
            )
            return
        base.update(
            title=meta.title or base["title"],
            channel_id=meta.channel_id or hit.channel_id,
            channel_name=meta.channel_name or hit.channel_name,
            duration_s=float(meta.duration_s or base["duration_s"]),
            published_at=meta.published_at,
        )
        tracks = meta.candidates(self.langs)
        cues: list[CueDraft] = []
        if tracks:
            run.requests += 1
            track = tracks[0]
            try:
                payload = await asyncio.to_thread(self.source.fetch_subtitle, track)
                cues = (
                    cues_from_json3(payload, word_timed=track.word_timed)
                    if track.ext == "json3"
                    else cues_from_vtt(payload)
                )
            except RateLimited:
                raise
            except (SourceError, ValueError) as exc:
                # A stale or refused track: recorded, so the video is not fetched again.
                run.notes.append(f"{hit.source_id}: {exc}")
                cues = []
        if not cues:
            await self.db.write(
                lambda c: picks.insert(c, self.owner_id, **base, state="no_captions")
            )
            return

        prompt = await self.db.read(lambda c: _prompt(c, base, meta.chapters, cues, self.owner_id))
        try:
            answer = await self.model.complete(
                prompt, system=SYSTEM, schema=VERDICT_SCHEMA, purpose="scout_verdict"
            )
        except LLMUnavailable as exc:
            if exc.reason != "invalid_output":
                raise
            # A bad answer costs this candidate only; it is not judged twice.
            await self.db.write(lambda c: picks.insert(c, self.owner_id, **base, state="judged"))
            run.judged += 1
            return
        moments = receipts(cues, answer["moments"])
        if len(moments) < len(answer["moments"]):
            logger.info(
                "scout: %s kept %d of %d moments; the rest failed the receipt check",
                hit.source_id,
                len(moments),
                len(answer["moments"]),
            )
        score = int(answer["score"])
        state = (
            "shown" if score >= SHOW_MIN_SCORE and shown_so_far < picks.SHOWN_PER_WEEK else "judged"
        )
        await self.db.write(
            lambda c: picks.insert(
                c,
                self.owner_id,
                **base,
                state=state,
                score=score,
                reason=str(answer["reason"]),
                summary=str(answer["summary"]),
                moments=moments,
                model=self.label,
            )
        )
        run.judged += 1
        run.shown += state == "shown"


# ---------------------------------------------------------------- the claim


def claim(conn: sqlite3.Connection, now: datetime, owner_id: int = 1) -> Run | None:
    """Claim tonight, or None: already run, backing off, or the box cooling off."""
    day = now.date()
    at = int(now.timestamp())
    if conn.execute(
        "SELECT 1 FROM scout_runs WHERE owner_id = ? AND day = ?", (owner_id, day.isoformat())
    ).fetchone():
        return None
    if day < next_allowed(conn, owner_id):
        return None
    # Indexing is waiting out a YouTube block: the scout does not add to it.
    if conn.execute(
        "SELECT 1 FROM jobs WHERE state = 'queued' AND error_code = 'E_RATE_LIMIT'"
        " AND not_before > ? LIMIT 1",
        (at,),
    ).fetchone():
        return None
    run_id = conn.execute(
        "INSERT INTO scout_runs (owner_id, day, state, started_at) VALUES (?, ?, 'running', ?)",
        (owner_id, day.isoformat(), at),
    ).lastrowid
    return Run(int(run_id), week_of(now))


def next_allowed(conn: sqlite3.Connection, owner_id: int = 1) -> date:
    """The first day the scout may ask again, after the latest run of blocked nights."""
    blocked: list[str] = []
    for row in conn.execute(
        "SELECT day, state FROM scout_runs WHERE owner_id = ? AND state <> 'running'"
        " ORDER BY day DESC LIMIT ?",
        (owner_id, len(BACKOFF_NIGHTS) + 1),
    ):
        if row["state"] != "blocked":
            break
        blocked.append(row["day"])
    if not blocked:
        return date.min
    skip = BACKOFF_NIGHTS[min(len(blocked), len(BACKOFF_NIGHTS)) - 1]
    return date.fromisoformat(blocked[0]) + timedelta(days=1 + skip)


def finish(conn: sqlite3.Connection, run: Run, state: str, at: int, error: str | None) -> None:
    conn.execute(
        "UPDATE scout_runs SET state = ?, finished_at = ?, requests = ?, judged = ?, shown = ?,"
        " speaker = ?, error = ? WHERE id = ?",
        (state, at, run.requests, run.judged, run.shown, run.speaker, error, run.run_id),
    )


# --------------------------------------------------------------- the inputs


def _top_entries(conn: sqlite3.Connection, owner_id: int) -> list[tuple[int, float, str]]:
    return [
        (int(e["id"]), float(e["weight"]), str(e["text"]))
        for e in profile_store.entries(conn, owner_id)
        if float(e["weight"]) > 0
    ][:ENTRIES]


def _first_new(conn: sqlite3.Connection, hits: list[SearchHit], owner_id: int) -> SearchHit | None:
    """The first result worth a caption fetch: new, the right length, not followed."""
    for hit in hits:
        if hit.duration_s is None or not MIN_DURATION_S <= hit.duration_s <= MAX_DURATION_S:
            continue
        if picks.known(conn, hit.source_id, owner_id):
            continue
        if picks.follow_of(conn, hit.channel_id, hit.channel_url, owner_id) is not None:
            continue
        return hit
    return None


def _prompt(
    conn: sqlite3.Connection,
    base: dict[str, Any],
    chapters: tuple[Any, ...],
    cues: list[CueDraft],
    owner_id: int,
) -> str:
    profile = (
        "\n".join(
            f"[{e['id']}] {float(e['weight']):+.1f}  {e['text']}"
            for e in profile_store.entries(conn, owner_id)
        )
        or "(empty: score on general interest and say so in the reason)"
    )
    chapter_lines = "\n".join(f"  {c.start_s:.0f}s {c.title}" for c in chapters[:30])
    video = f"Title: {base['title']}\nChannel: {base['channel_name'] or 'unknown'}"
    if chapter_lines:
        video += f"\nChapters:\n{chapter_lines}"
    transcript = middle_lines(
        [f"[cue {i} {q.start_s:.1f}–{q.end_s:.1f}] {q.text}" for i, q in enumerate(cues, 1)],
        CAPTION_CHARS,
    )
    return (
        f"Interest profile ([id] weight, entry):\n{profile}\n\n"
        f"Video:\n{video}\n\n"
        f"Transcript (YouTube captions):\n{transcript}"
    )


def receipts(cues: list[CueDraft], answered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """§3.1's receipt check against the fetched captions: kept or dropped, never moved."""
    kept: list[dict[str, Any]] = []
    for m in answered:
        first, last, offset = int(m["cue_id"]), int(m["end_cue_id"]), float(m["offset_s"])
        if not 1 <= first <= last <= len(cues):
            continue
        start = cues[first - 1]
        if not start.start_s <= offset <= start.end_s:
            continue
        end_s = cues[last - 1].end_s
        if end_s < offset:
            continue
        kept.append({"offset_s": round(offset, 1), "end_s": round(end_s, 1), "why": str(m["why"])})
    return kept


# ------------------------------------------------------------------- build


def build_scout(db: Any) -> tuple[Scout | None, httpx.AsyncClient | None]:
    """The scout and the HTTP client its model owns, or (None, None) when off."""
    scout = ScoutSettings.from_env()
    if not scout.enabled:
        return None, None
    settings = LLMSettings.from_env()
    if not is_configured(settings):
        return None, None
    http = httpx.AsyncClient() if settings.backend == "api" else None
    model = build_model(settings, http, db)  # type: ignore[arg-type]
    assert model is not None
    pipeline = PipelineSettings.from_env()
    label = f"{settings.backend}:{settings.model or 'default'}"
    return (
        Scout(
            db, model, label, YtDlpSource(pipeline), hour=scout.hour, langs=pipeline.subtitle_langs
        ),
        http,
    )
