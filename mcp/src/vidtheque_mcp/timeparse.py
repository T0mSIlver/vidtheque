"""The two time axes (tool-surface §3.2), normalized in exactly one place.

screenpipe advertised relative date formats while its server rejected them and
returned silent empty results for months (#3124). One normalizer, used by every
tool and by the raw HTTP API, is the fix.

Corpus axis  — `published_after` / `published_before`, absolute unix seconds.
Intra-video  — `t_start` / `t_end` / `t`, REAL seconds from the video start.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

from .errors import ToolError, bad_time

_RELATIVE = re.compile(r"^\s*(\d+)\s*(s|sec|secs|m|min|mins|h|hr|hrs|d|w|mo|y)\s*ago\s*$", re.I)
_UNIT_SECONDS = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "d": 86_400,
    "w": 7 * 86_400,
    "mo": 30 * 86_400,
    "y": 365 * 86_400,
}
_CLOCK = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)\s*$")


def now_utc() -> datetime:
    return datetime.now(UTC)


def parse_corpus_time(value: object, param: str, *, now: datetime | None = None) -> int | None:
    """Normalize a corpus-axis value to unix seconds UTC.

    Bare dates resolve to start-of-day UTC. Unparseable input is a hard typed
    error that echoes the accepted formats — never a silently ignored filter.
    """
    if value is None or value == "":
        return None
    now = now or now_utc()

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)

    if not isinstance(value, str):
        raise bad_time(str(value), param)

    raw = value.strip()
    lowered = raw.lower()

    if lowered == "now":
        return int(now.timestamp())
    if lowered == "today":
        return int(datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC).timestamp())
    if lowered == "yesterday":
        day = now.date() - timedelta(days=1)
        return int(datetime.combine(day, datetime.min.time(), tzinfo=UTC).timestamp())

    match = _RELATIVE.match(lowered)
    if match:
        amount, unit = int(match.group(1)), match.group(2).lower()
        return int(now.timestamp()) - amount * _UNIT_SECONDS[unit]

    if raw.isdigit() and len(raw) >= 9:
        # A bare unix timestamp. Nine digits is 1973 — no real date string is
        # nine bare digits, so this cannot swallow one.
        return int(raw)

    iso = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(raw), datetime.min.time())
        except ValueError as exc:
            raise bad_time(raw, param) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def parse_offset(value: object, param: str) -> float | None:
    """Normalize an intra-video value to REAL seconds.

    Accepts a number (723) or a clock string (12:03, 1:12:03).
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise bad_time(str(value), param)
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        raw = value.strip()
        match = _CLOCK.match(raw)
        if match:
            hours = int(match.group(1) or 0)
            minutes = int(match.group(2))
            secs = float(match.group(3))
            seconds = hours * 3600 + minutes * 60 + secs
        else:
            try:
                seconds = float(raw)
            except ValueError as exc:
                raise bad_time(raw, param) from exc
    else:
        raise bad_time(str(value), param)

    if seconds < 0:
        raise ToolError(
            "E_BAD_PARAM",
            f"{param} must be >= 0, got {seconds}.",
            "intra-video times are seconds from the start of the video.",
        )
    return seconds
