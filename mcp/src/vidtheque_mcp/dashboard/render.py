"""The Jinja2 environment — autoescape on, and the filters the pages share.

``autoescape=True`` is the whole reason this dependency exists (dashboard.md
§10.2). The pages render OCR lines, video titles, channel names and yt-dlp's
error strings, every one of them whatever happened to be on someone's screen:
escaping has to be what happens when nobody thought about it, and ``| safe``
has to be a thing you can grep for. There is no ``| safe`` in this package, and
a test asserts that.

``StrictUndefined`` for the same family of reasons: a typo'd variable is a
loud failure in a test rather than a silently blank cell in a panel whose
entire job is to be believed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..text import clock, duration_clock, iso_day, iso_minute

TEMPLATES = Path(__file__).parent / "templates"

# Recognised vocabulary → a tone the stylesheet knows. dashboard.md §4.5: the
# page prints the tool's word verbatim and maps *from* the string for colour,
# falling back to neutral on anything it does not recognise — a fifth
# vocabulary must never be invented here, and an unrecognised word must render
# rather than disappear.
_TONES = {
    # video.index_state
    "ready": "ok",
    "indexing": "work",
    "pending": "wait",
    "stale": "warn",
    "failed": "bad",
    # video_stages.state
    "done": "ok",
    "running": "work",
    "skipped": "wait",
    # keyframes.ocr_state
    "empty": "wait",
    # data_status, per video and corpus-wide
    "ok": "ok",
    "no_transcript": "warn",
    "no_ocr": "warn",
    "no_frames": "warn",
    "partial": "warn",
    "degraded": "bad",
    # jobs.state and job_items.state — the same five words plus `cancelled`,
    # which is not a failure and must not be coloured as one.
    "queued": "wait",
    "cancelled": "neutral",
    # job_events.level
    "warn": "warn",
    "error": "bad",
    "info": "neutral",
    "debug": "neutral",
    # follows.state — what the clock is doing, not what a video is doing.
    "active": "ok",
    "paused": "wait",
    "failing": "bad",
    # follow_seen.decision. `queued` and `failed` are already above and carry
    # the same tone here, which is the point of mapping from the string: the
    # vocabulary is the schema's and this table is a lookup, not a fifth set of
    # words. A held candidate is `warn` because it is waiting on a human or on
    # the budget and neither resolves itself; a skipped one is `wait`, because
    # a rule that did its job is not a warning.
    "held_budget": "warn",
    "held_review": "warn",
    "already_indexed": "ok",
    "skipped_tab": "wait",
    "skipped_title": "wait",
    "skipped_duration": "wait",
    "skipped_horizon": "wait",
}


def tone(word: str | None) -> str:
    """A tone name for a state word, or ``neutral`` for anything unrecognised."""
    return _TONES.get((word or "").strip().lower(), "neutral")


def bytes_human(value: int | float | None) -> str:
    """`1.4 GB`. Base-10, because that is what a disk reports."""
    if not value:
        return "0 B"
    size = float(value)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000.0
    return f"{size:.1f} TB"  # pragma: no cover - unreachable, the loop returns


def span(seconds: int | float | None) -> str:
    """A number of seconds as a clock a human reads. ``—`` for "not known".

    One formatter for every duration on this surface: how long a stage took,
    how long a job has been running, how much of a backoff is left. They are
    the same unit and they must not read as three different ones.
    """
    if seconds is None:
        return "—"
    total = int(seconds)
    if total < 0:
        return "—"
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def elapsed(start: int | None, finish: int | None) -> str:
    """How long a stage took, from the two timestamps it stores.

    ``—`` rather than a guess when either end is missing: a stage that failed
    has a `started_at` and no `finished_at`, and "running for 4 days" would be
    a sentence about a process that died (dashboard.md §4.1).
    """
    if not start or not finish or finish < start:
        return "—"
    return span(int(finish) - int(start))


def count(value: int | float | None) -> str:
    return f"{int(value or 0):,}"


def dash(value: Any) -> str:
    """The one rendering for "this is not recorded".

    `model_key` is NULL on every failed, skipped and invalidated stage
    (dashboard.md §4.1 caveat 1). Provenance records what *succeeded*; the page
    says so with an em dash and does not guess.
    """
    if value is None or value == "":
        return "—"
    return str(value)


def build_environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        auto_reload=False,
    )
    env.filters.update(
        clock=clock,
        duration=duration_clock,
        day=iso_day,
        minute=iso_minute,
        bytes_human=bytes_human,
        count=count,
        dash=dash,
        tone=tone,
        span=span,
    )
    env.globals.update(elapsed=elapsed)
    return env
