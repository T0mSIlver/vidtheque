"""What a follow accepts, and the sentence it says when it does not.

Every rule here is cheap enough to run against a flat listing, and that is not
an accident — it is the ordering constraint the whole feature hangs on. A check
extracts one listing per tab and then decides, and the only rule that may need
a second request is the duration one, because the listing carries `duration`
for most entries and not all.

**The order is the point:**

    tab -> title -> already indexed -> duration (listing) -> duration (probe)

Cheapest first, and the one that can cost a request last. `judge` runs the
first two; the caller runs the rest, because "already indexed" is a question
for the database and a probe is a question for YouTube. What comes back from
either is fed to `judge_duration` so the *reason string* is written in one
place whichever half asked.

A rejected candidate is never silently dropped: it becomes a `follow_seen` row
whose `reason` carries the number that made the decision. "Shorter than your
floor" is an opinion; "4:12, shorter than your 8:00 floor" is a receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..text import duration_clock

TABS = ("videos", "streams", "shorts")
CHANNEL_SETS = ("all", "transcript", "ocr", "frames")
MODES = ("auto", "review")
STATES = ("active", "paused", "failing")

# The ceilings the schema also carries. Duplicated for the same reason
# tool-surface §3.7's tag regex is duplicated into a CHECK constraint: this
# copy produces a helpful typed error, the schema copy is the guarantee.
MAX_BACKFILL = 25
MAX_PER_CHECK = 25
MIN_CHECK_INTERVAL_S = 900
DEFAULT_CHECK_INTERVAL_S = 21_600

# A rule may not ask for more of the listing than this in one check. The
# listing itself is one request whatever its length, so this bounds the *work*
# a check can create, not the request it makes.
MAX_LISTING = 50


@dataclass(frozen=True)
class Rules:
    """The follow's conditions, as the check evaluates them."""

    tabs: tuple[str, ...] = ("videos",)
    min_duration_s: int | None = None
    max_duration_s: int | None = None
    title_include: tuple[str, ...] = ()
    title_exclude: tuple[str, ...] = ()
    channels: str = "all"
    tags: tuple[str, ...] = ()
    backfill: int = 0
    max_per_check: int = 5
    mode: str = "auto"
    check_interval_s: int = DEFAULT_CHECK_INTERVAL_S

    @classmethod
    def from_row(cls, row: object) -> "Rules":
        """Build from a `follows` row (anything supporting ``row[...]``)."""
        get = row.__getitem__  # type: ignore[attr-defined]
        return cls(
            tabs=tuple(split_terms(get("tabs"))) or ("videos",),
            min_duration_s=_int_or_none(get("min_duration_s")),
            max_duration_s=_int_or_none(get("max_duration_s")),
            title_include=tuple(split_terms(get("title_include"))),
            title_exclude=tuple(split_terms(get("title_exclude"))),
            channels=str(get("channels") or "all"),
            tags=tuple(split_terms(get("tags"))),
            backfill=int(get("backfill") or 0),
            max_per_check=int(get("max_per_check") or 5),
            mode=str(get("mode") or "auto"),
            check_interval_s=int(get("check_interval_s") or DEFAULT_CHECK_INTERVAL_S),
        )


@dataclass(frozen=True)
class Candidate:
    """One upload as the listing described it, before anything was fetched."""

    source_id: str
    url: str
    title: str | None = None
    duration_s: float | None = None
    published_at: int | None = None
    tab: str = "videos"


@dataclass(frozen=True)
class Verdict:
    """A `follow_seen.decision` and the sentence that goes with it."""

    decision: str
    reason: str


def split_terms(raw: object) -> list[str]:
    """Comma-separated, trimmed, empties dropped. The one parser for all of them."""
    if not raw:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def judge(rules: Rules, candidate: Candidate) -> Verdict | None:
    """The listing-only rules. ``None`` means "nothing here rejects it".

    Deliberately not a duration check even when the listing carried one: the
    caller decides whether it has a number to judge, because the answer to
    "already indexed?" comes first and is cheaper than a probe.
    """
    if candidate.tab not in rules.tabs:
        return Verdict(
            "skipped_tab",
            f"from /{candidate.tab}; this follow watches "
            + ", ".join(f"/{tab}" for tab in rules.tabs),
        )
    title = (candidate.title or "").lower()
    for term in rules.title_exclude:
        if term.lower() in title:
            # Exclude wins over include, and it wins by being asked first: a
            # title matching both is a title the operator named twice, and the
            # negative is the one they meant.
            return Verdict("skipped_title", f"title contains {term!r}, which you exclude")
    if rules.title_include and not any(term.lower() in title for term in rules.title_include):
        return Verdict(
            "skipped_title",
            "title contains none of " + ", ".join(repr(t) for t in rules.title_include),
        )
    return None


def needs_duration(rules: Rules, candidate: Candidate) -> bool:
    """Would a probe change the answer? Only if there is a rule and no number."""
    if rules.min_duration_s is None and rules.max_duration_s is None:
        return False
    return candidate.duration_s is None


def judge_duration(rules: Rules, duration_s: float | None) -> Verdict | None:
    """The one rule that may have cost a request. ``None`` means it passes.

    An unknown duration is not zero and is not infinity — it is unknown, and a
    follow with a length rule that cannot measure a candidate says so rather
    than guessing in either direction. It is held for review, not skipped:
    guessing "too short" loses a talk, guessing "fine" spends the GPU.
    """
    if rules.min_duration_s is None and rules.max_duration_s is None:
        return None
    if duration_s is None:
        return Verdict(
            "held_review",
            "neither the listing nor a probe gave a duration, and this follow "
            "has a length rule — deciding it needs you",
        )
    clock = duration_clock(duration_s)
    if rules.min_duration_s is not None and duration_s < rules.min_duration_s:
        return Verdict(
            "skipped_duration",
            f"{clock}, shorter than your {duration_clock(rules.min_duration_s)} floor",
        )
    if rules.max_duration_s is not None and duration_s > rules.max_duration_s:
        return Verdict(
            "skipped_duration",
            f"{clock}, longer than your {duration_clock(rules.max_duration_s)} ceiling",
        )
    return None


def describe(rules: Rules, *, name: str) -> str:
    """The rule as one sentence — what the follow detail page reads by default.

    A form states fields; a sentence states a policy, and the operator is
    checking a policy. The form is one click behind this.
    """
    every = _interval_phrase(rules.check_interval_s)
    length = _length_phrase(rules)
    tabs = ", ".join(f"/{tab}" for tab in rules.tabs)
    parts = [
        f"Every {every}, take up to {rules.max_per_check} new upload"
        f"{'' if rules.max_per_check == 1 else 's'} from {name} on {tabs}",
    ]
    if length:
        parts.append(length)
    if rules.title_include:
        parts.append("titled like " + ", ".join(repr(t) for t in rules.title_include))
    if rules.title_exclude:
        parts.append("never " + ", ".join(repr(t) for t in rules.title_exclude))
    parts.append(
        "index all three channels"
        if rules.channels == "all"
        else f"index {rules.channels} only"
    )
    if rules.tags:
        parts.append("tag " + ", ".join(rules.tags))
    if rules.mode == "review":
        parts.append("and hold them for you rather than queueing them")
    return ", ".join(parts) + "."


def _length_phrase(rules: Rules) -> str:
    low, high = rules.min_duration_s, rules.max_duration_s
    if low is not None and high is not None:
        return f"between {duration_clock(low)} and {duration_clock(high)} long"
    if low is not None:
        return f"longer than {duration_clock(low)}"
    if high is not None:
        return f"shorter than {duration_clock(high)}"
    return ""


def _interval_phrase(seconds: int) -> str:
    if seconds % 86_400 == 0:
        days = seconds // 86_400
        return "day" if days == 1 else f"{days} days"
    if seconds % 3_600 == 0:
        hours = seconds // 3_600
        return "hour" if hours == 1 else f"{hours} hours"
    minutes = max(1, seconds // 60)
    return "minute" if minutes == 1 else f"{minutes} minutes"


def normalize_tabs(raw: object) -> tuple[str, ...]:
    """Validated, ordered, deduplicated. Raises nothing — the caller types the error."""
    wanted = [tab for tab in split_terms(raw) if tab in TABS]
    return tuple(tab for tab in TABS if tab in wanted) or ("videos",)


def unknown_tabs(raw: object) -> list[str]:
    return [tab for tab in split_terms(raw) if tab not in TABS]


def clamp_int(value: object, low: int, high: int, default: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _int_or_none(value: object) -> int | None:
    return None if value is None else int(value)  # type: ignore[arg-type]


def joined(terms: Sequence[str]) -> str | None:
    """Store a term list the way it was parsed, or NULL when it is empty."""
    return ", ".join(terms) if terms else None
