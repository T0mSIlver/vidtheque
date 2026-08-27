"""Unknown argument names are a typed error, not a silent drop (§3.5).

The MCP SDK builds each tool's argument model with pydantic's default
`extra="ignore"`, so a name the schema does not carry is dropped before the
handler is entered — there is no hook inside the tool for it. The check
therefore sits one layer out, at `tools/call`, where the raw arguments still
exist: `tools.register` wraps `MCPServer.call_tool` with `error_for`.

Why reject at all, when the guide used to document the silence as intended: a
caller that believes it filtered to `tag="topic:test"` and sorted by
`sort_by="recency"` got neither, and a 200 — while the *same* tool answers
`order="nonesuch"` and `fields="…,definitely_not_a_column"` with a typed error
naming the domain. One tool, three standards. Two independent consumers filed
it in one week (terra eval §4.5, and p6's own findings table).
"""

from __future__ import annotations

import difflib
from typing import Collection, Mapping

from ..errors import ToolError, bad_param

# Wrong name -> the right ones, in preference order. The first candidate the
# *called tool* actually has is the one suggested, which is what makes one table
# serve ten tools: `t_start` on `get-segment-context` means `t`, and on
# `list-videos` it means the caller reached for the wrong time axis entirely
# (§3.2) and gets pointed at `published_after`.
ALIASES: dict[str, tuple[str, ...]] = {
    "tag": ("tags",),
    "topic": ("tags",),
    "topics": ("tags",),
    "label": ("tags",),
    "labels": ("tags",),
    "sort": ("order",),
    "sort_by": ("order",),
    "sortby": ("order",),
    "order_by": ("order",),
    "orderby": ("order",),
    "sort_order": ("order",),
    "query": ("q", "video_title"),
    "search": ("q",),
    "search_query": ("q",),
    "keyword": ("q",),
    "keywords": ("q",),
    "term": ("q",),
    "text": ("q",),
    "prompt": ("q",),
    "max_results": ("limit",),
    "num_results": ("limit",),
    "top_k": ("limit",),
    "k": ("limit",),
    "n": ("limit",),
    "count": ("limit",),
    "page": ("offset",),
    "page_size": ("limit",),
    "skip": ("offset",),
    "start_at": ("offset",),
    "video": ("video_id",),
    "videos": ("video_id",),
    "video_ids": ("video_id",),
    "id": ("video_id", "frame_ids", "job_id"),
    "ids": ("frame_ids", "video_id"),
    "frame": ("frame_ids",),
    "frame_id": ("frame_ids",),
    "job": ("job_id",),
    "time": ("t", "t_start", "published_after"),
    "timestamp": ("t", "t_start", "published_after"),
    "seconds": ("t", "t_start"),
    "start": ("t_start", "t", "published_after"),
    "end": ("t_end", "published_before"),
    "offset_start": ("t_start", "t", "published_after"),
    "offset_end": ("t_end", "published_before"),
    "t_start": ("t", "published_after"),
    "t_end": ("t", "published_before"),
    "after": ("published_after", "indexed_after"),
    "before": ("published_before", "indexed_before"),
    "date_after": ("published_after",),
    "date_before": ("published_before",),
    "published": ("published_after",),
    "since": ("published_after", "indexed_after"),
    "until": ("published_before", "indexed_before"),
    "channel_name": ("channel",),
    "creator": ("channel",),
    "author": ("channel", "speaker"),
    "title": ("video_title",),
    "type": ("content_type",),
    "kind": ("content_type",),
    "source": ("content_type",),
    "max_length": ("max_text_chars", "max_chars"),
    "max_len": ("max_text_chars", "max_chars"),
    "truncate": ("max_text_chars", "max_chars"),
    "response_format": ("format",),
    "output": ("format", "return"),
    "columns": ("fields",),
    "select": ("fields",),
    "image": ("return",),
    "images": ("return",),
    "inline": ("return",),
}

# `page → offset` is the one alias whose units differ from the name it fixes,
# and a caller who follows it literally gets a 200 for the wrong rows: on a
# `limit=50` listing, `offset=2` reads rows 2-52 while the caller believes it
# read page 2 — "a filter you think you passed was not", one layer down (terra
# eval §9.3). Dropping the alias would only send them to the generic accepted
# list; naming the unit is what actually lands.
UNIT_HINTS: dict[str, str] = {
    "page": (
        "offset counts ROWS, not pages — page N is offset=(N-1)×limit, so page 2 "
        "of a limit=50 listing is offset=50"
    ),
}

# Enum-typed parameters, per tool, so a rename whose *value* also has to change
# costs one round trip instead of two: the server has the name, the value and
# the domain at the first call, and used to spend a call anyway (terra eval
# §9.4 — `kind="speech"` → `content_type=` → `content_type must be one of …`).
# Imported lazily inside `enum_domain`: the tool modules import this one.
_ENUM_SOURCES: dict[str, dict[str, str | tuple[str, ...]]] = {
    "search": {
        "content_type": "search.CONTENT_TYPES",
        "order": "search.ORDERS",
        "format": ("text", "tsv"),
    },
    "list-videos": {
        "order": "library.LIST_ORDERS",
        "has": "library.HAS_VALUES",
        "index_state": "library.INDEX_STATE_VALUES",
        "format": ("text", "tsv"),
    },
    "get-frames": {"return": ("url", "image")},
    "index-video": {
        "expand": "indexing.EXPANSIONS",
        "priority": ("normal", "high"),
    },
    "job-status": {"state": ("all", "active", "failed", "done")},
}


def enum_domain(tool: str, param: str) -> tuple[str, ...] | None:
    """The allowed values of `param` on `tool`, or None if it is not an enum.

    The tuples live on the tools that validate them (one definition, one error
    text); this resolves the names at call time because `tools/search.py` and
    friends import this module, never the other way round.
    """
    source = _ENUM_SOURCES.get(tool, {}).get(param)
    if source is None:
        return None
    if isinstance(source, tuple):
        return source
    module_name, attribute = source.split(".")
    from . import indexing, library, search  # noqa: PLC0415 - see docstring

    module = {"search": search, "library": library, "indexing": indexing}[module_name]
    return tuple(getattr(module, attribute))


# The two-axis confusion is worth naming rather than merely redirecting: a
# caller who wrote `t_start=2019` on a corpus-wide search meant the year (§3.2,
# and §4.8 of the terra eval).
AXIS_HINT = (
    "published_after/published_before pick videos by upload date; t_start/t_end "
    "pick seconds inside a video (§3.2 — the two axes are never interchangeable)"
)


def _year_shaped(raw: float | str | None) -> int | None:
    """The year a caller typed into an in-video time field, or None.

    Only a *bare* number counts: `t_start="33:39"` is the clock form and means
    what it says, and `t_start=2019` is the same 2019 seconds — the difference
    is what the caller wrote, which is the only evidence of what they meant.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if ":" in text or not text.lstrip("-").replace(".", "", 1).isdigit():
            return None
        value = float(text)
    else:
        value = float(raw)
    return int(value) if value.is_integer() and 1900 <= value <= 2100 else None


def axis_check(
    t_start: float | str | None, t_end: float | str | None, *, scoped: bool
) -> ToolError | str | None:
    """Guard the two-axis confusion in the one direction that was not caught.

    `published_after="2:00"` is a hard `E_BAD_TIME_FORMAT`; the mirror image —
    a year in `t_start`/`t_end` — was read as seconds and answered with five
    tidy, entirely wrong hits and no `note:` (terra eval §4.8). §3.2's "harmless
    otherwise" is exactly what makes it dangerous: a wrong filter that returns
    nothing is self-correcting, a wrong filter that returns six plausible hits
    is not.

    Returns the error to raise, a `note:` line to print, or None. A call scoped
    to named videos gets neither — there the in-video axis is unambiguous, and
    2019 s into a 50-minute talk is a real position.
    """
    if scoped or (t_start is None and t_end is None):
        return None
    for param, raw in (("t_start", t_start), ("t_end", t_end)):
        year = _year_shaped(raw)
        if year is not None:
            return ToolError(
                "E_BAD_PARAM",
                f"{param}={year} on the in-video axis means {year} seconds "
                f"({year // 60}:{year % 60:02d}) into every video, which is "
                "almost certainly not what you meant.",
                f'to select videos published in {year}, use '
                f'published_after="{year}-01-01" published_before="{year + 1}-01-01". '
                f'To really mean {year // 60}:{year % 60:02d} inside a video, write '
                f'{param}="{year // 60}:{year % 60:02d}" — or scope the call with '
                "video_id=, where seconds are taken as written.",
            )
    return (
        "note: t_start/t_end are the in-video axis — seconds from the start of "
        "each video — and this call names no video_id, so the window was applied "
        "inside every video in the pool. Upload dates are published_after/"
        "published_before (§3.2)."
    )


def suggest(name: str, known: Collection[str]) -> str | None:
    """The parameter `name` was probably reaching for, or None."""
    for candidate in ALIASES.get(name.lower(), ()):
        if candidate in known:
            return candidate
    close = difflib.get_close_matches(name.lower(), sorted(known), n=1, cutoff=0.75)
    return close[0] if close else None


def error_for(
    tool: str, arguments: Mapping[str, object], known: Collection[str] | None
) -> ToolError | None:
    """`E_BAD_PARAM` naming every unknown argument and its near miss, or None.

    ``known is None`` means the name is not one of ours (an SDK-registered or
    extension tool): nothing to check against, so nothing is rejected.

    Keys beginning with `_` are left alone. That namespace belongs to the
    protocol and to client vendors (`_meta` and friends), and a server that
    400s a client's own bookkeeping is a worse failure than the one this
    function exists to fix.
    """
    if known is None:
        return None
    unknown = [k for k in arguments if k not in known and not k.startswith("_")]
    if not unknown:
        return None

    pairs = [(name, suggest(name, known)) for name in unknown]
    named = ", ".join(f"{name}=" for name in unknown)
    many = len(unknown) > 1
    message = (
        f"Unknown parameter{'s' if many else ''} for {tool}: {named}. "
        f"{'They were' if many else 'It was'} rejected, not applied — a filter "
        "you think you passed was not."
    )

    guesses = [f'{wrong}= → {right}=' for wrong, right in pairs if right]
    hint_parts: list[str] = []
    if guesses:
        hint_parts.append("did you mean " + ", ".join(guesses) + "?")
    for wrong, right in pairs:
        unit = UNIT_HINTS.get(wrong)
        if unit and right:
            hint_parts.append(f"{unit} ({wrong}=N is not {right}=N).")
        # The value the caller sent, judged against the domain of the parameter
        # it was reaching for. Only when it does not fit: a rename that carries
        # a valid value needs no lecture.
        domain = enum_domain(tool, right) if right else None
        sent = arguments.get(wrong)
        if domain and isinstance(sent, str) and sent not in domain:
            hint_parts.append(
                f"{right} must be one of {', '.join(domain)} — "
                f"{sent!r} is not one of them."
            )
    if any(w in {"t_start", "t_end", "offset_start", "offset_end"} for w, _ in pairs):
        hint_parts.append(AXIS_HINT + ".")
    hint_parts.append(f"{tool} accepts: {', '.join(sorted(known))}.")
    return bad_param(message, " ".join(hint_parts))
