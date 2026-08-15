"""One validator for the follow rules, because there are two callers.

`tools/follows.py` is the model's way in and `dashboard/writes.py` is the
operator's, and the dashboard is built *on* the tool rather than beside it
(dashboard.md §2.2) — so the form adds no policy and this module is where the
policy is. Duplicated validation is a smell this codebase has written about
before; the only copies it keeps are the schema `CHECK` constraints, which have
a different job (a helpful typed error here, a guarantee there).

Durations are parsed by `timeparse.parse_offset`, which already reads `480`,
`8:00` and `1:30:00`. A follow's length rule is an intra-video quantity by
definition, so it is the offset axis, and adding an `8m` spelling would be
inventing a third notation for a contract that deliberately has two
(tool-surface §3.2).
"""

from __future__ import annotations

from typing import Any

from ..errors import ToolError, bad_param
from ..text import split_csv, validate_tag
from ..timeparse import parse_offset
from .rules import (
    CHANNEL_SETS,
    DEFAULT_CHECK_INTERVAL_S,
    MAX_BACKFILL,
    MAX_PER_CHECK,
    MIN_CHECK_INTERVAL_S,
    MODES,
    TABS,
    Rules,
    normalize_tabs,
    split_terms,
    unknown_tabs,
)

# A title term is a plain substring and stays one. Bounded because it is stored
# and then run against every listing entry forever.
MAX_TITLE_TERMS = 10
MAX_TERM_CHARS = 80


def build_rules(
    *,
    tabs: Any = "videos",
    min_duration: Any = None,
    max_duration: Any = None,
    title_include: Any = None,
    title_exclude: Any = None,
    channels: Any = "all",
    tags: Any = None,
    backfill: Any = 0,
    max_per_check: Any = 5,
    mode: Any = "auto",
    check_interval_s: Any = None,
    default_interval_s: int = DEFAULT_CHECK_INTERVAL_S,
) -> Rules:
    """Validate and normalize every rule. Raises `ToolError` on anything bad."""
    bad_tabs = unknown_tabs(tabs)
    if bad_tabs:
        raise bad_param(
            f"tabs must be a subset of {', '.join(TABS)} — got {', '.join(bad_tabs)}.",
            "a channel's /videos, /streams and /shorts are three listings; "
            "each one this follow watches is one more request per check.",
        )
    for part in split_terms(channels):
        if part not in CHANNEL_SETS:
            raise bad_param(
                f"channels must be 'all' or a subset of transcript,ocr,frames — got {part!r}."
            )
    if mode not in MODES:
        raise bad_param(f"mode must be one of {', '.join(MODES)}.")

    low = parse_offset(min_duration, "min_duration")
    high = parse_offset(max_duration, "max_duration")
    if low is not None and high is not None and low > high:
        raise ToolError(
            "E_BAD_PARAM",
            f"min_duration ({low:g}s) is longer than max_duration ({high:g}s), "
            "so nothing could ever match.",
            "swap them, or drop one.",
        )

    tag_list = split_csv(tags if isinstance(tags, str) or tags is None else ",".join(tags), 10, "tags")
    for tag in tag_list:
        validate_tag(tag)

    interval = _interval(check_interval_s, default_interval_s)
    return Rules(
        tabs=normalize_tabs(tabs),
        min_duration_s=None if low is None else int(low),
        max_duration_s=None if high is None else int(high),
        title_include=_terms(title_include, "title_include"),
        title_exclude=_terms(title_exclude, "title_exclude"),
        channels=str(channels or "all"),
        tags=tuple(tag_list),
        backfill=_bounded(backfill, 0, MAX_BACKFILL, "backfill"),
        max_per_check=_bounded(max_per_check, 1, MAX_PER_CHECK, "max_per_check"),
        mode=str(mode),
        check_interval_s=interval,
    )


def rule_columns(rules: Rules) -> dict[str, Any]:
    """The `follows` columns for these rules, ready for `store.update_rules`."""
    from .rules import joined

    return {
        "tabs": ",".join(rules.tabs),
        "min_duration_s": rules.min_duration_s,
        "max_duration_s": rules.max_duration_s,
        "title_include": joined(rules.title_include),
        "title_exclude": joined(rules.title_exclude),
        "channels": rules.channels,
        "tags": joined(rules.tags),
        "backfill": rules.backfill,
        "max_per_check": rules.max_per_check,
        "mode": rules.mode,
        "check_interval_s": rules.check_interval_s,
    }


def _terms(raw: Any, param: str) -> tuple[str, ...]:
    terms = split_csv(raw if isinstance(raw, str) or raw is None else ",".join(raw), MAX_TITLE_TERMS, param)
    for term in terms:
        if len(term) > MAX_TERM_CHARS:
            raise bad_param(
                f"{param} terms are at most {MAX_TERM_CHARS} characters — "
                f"{term[:40]!r} is {len(term)}.",
                "these are plain substrings, matched case-insensitively; "
                "they are not patterns.",
            )
    return tuple(terms)


def _bounded(value: Any, low: int, high: int, param: str) -> int:
    """Server-side clamp, and it says so. Never a prompt-only limit."""
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise bad_param(f"{param} must be a whole number, got {value!r}.") from exc
    return max(low, min(high, number))


def _interval(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise bad_param(f"check_interval_s must be a whole number of seconds, got {value!r}.") from exc
    if seconds < MIN_CHECK_INTERVAL_S:
        raise ToolError(
            "E_BAD_PARAM",
            f"check_interval_s must be at least {MIN_CHECK_INTERVAL_S} seconds "
            f"(fifteen minutes), got {seconds}.",
            "a check is a request against a source that rate-limits; the floor "
            "is there so a follow cannot become the reason this box is blocked.",
        )
    # A week is the far end of "unhurried", and past it the follow stops being
    # one. Clamped rather than refused: the intent is legible and the number is
    # not dangerous, it is just not a follow any more.
    return min(seconds, 7 * 86_400)
