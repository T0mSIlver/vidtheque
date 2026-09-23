"""`get-transcript` — the whole talk, in order, paged (tool-surface §4.11).

The eleventh tool, and the only one that exists to return *bulk* text. Every
other reader on the surface samples: `video-summary` picks key texts,
`get-segment-context` opens a ±300 s window around a moment you already found.
Neither answers "read me this talk", and the seven blind window-walks a caller
used to make instead cost more than three honest pages.

Double-capped, like everything else: `limit` cues **and** `max_text_chars`,
whichever binds first, with the binding one named in the payload and the next
`offset` computed from what was actually printed. The transcript is the whole
payload — no OCR, no frame refs, no chapter — because the tools that own those
are one call away and bundling them is how a page budget stops meaning anything.

**Not registered on a public instance** (`public/readonly.py`, tool-surface
§4.11): this is the model-facing half of the `export.md` hatch, and that route
is owner-only on a public deployment for the reason that applies here unchanged
— a corpus whose every transcript is walkable by an anonymous caller is a bulk
download with a tool description on it. The annotations cannot carry that: the
tool is honestly `readOnlyHint: true`, and the axis is bulk.
"""

from __future__ import annotations

from typing import Any

from mcp_types import CallToolResult

from ..db import queries
from ..errors import ToolError, bad_param, unknown_video
from ..text import (
    clamp,
    clamp_text_chars,
    clock,
    deeplink,
    deeplink_t,
    duration_clock,
    pagination_line,
    tsv,
)
from ..timeparse import parse_offset
from .base import Deps, handle_errors, text_result

FORMATS = ("text", "tsv")

MAX_CUES = 500
DEFAULT_CUES = 400
# The same floor `get-segment-context` uses, and for the same reason: one
# value per thing, so a caller who learned the transcript budget on one tool
# does not have to relearn it on the other.
MIN_TEXT_CHARS = 200
MAX_TEXT_CHARS = 40_000
DEFAULT_TEXT_CHARS = 20_000


@handle_errors
async def run(
    deps: Deps,
    video_id: str,
    t_start: float | str | None = None,
    t_end: float | str | None = None,
    limit: int = DEFAULT_CUES,
    offset: int = 0,
    max_text_chars: int = DEFAULT_TEXT_CHARS,
    include_speakers: bool = True,
    format: str = "text",
) -> CallToolResult:
    if format not in FORMATS:
        raise bad_param(f"format must be one of {', '.join(FORMATS)}.")
    limit = clamp(limit, 1, MAX_CUES, DEFAULT_CUES)
    offset = max(0, clamp(offset, 0, 1_000_000, 0))
    # `0` is the documented opt-out and it opts out of *this* cap only: the
    # `limit` cues still bind, so the worst case stays bounded (§7). The other
    # tools' `0` behaves the same way — it lifts a text budget, never a count.
    max_text_chars = clamp_text_chars(
        max_text_chars, MIN_TEXT_CHARS, MAX_TEXT_CHARS, DEFAULT_TEXT_CHARS
    )

    span_start = parse_offset(t_start, "t_start")
    span_end = parse_offset(t_end, "t_end")
    if span_start is not None and span_end is not None and span_end < span_start:
        raise bad_param(
            f"t_end ({int(span_end)}s) is before t_start ({int(span_start)}s).",
            "t_start and t_end are seconds inside one video, in order.",
        )

    row = await deps.db.read(lambda c: queries.lookup_video(c, video_id))
    if row is None:
        raise unknown_video(video_id, can_index=deps.offers("index-video"))
    vid = int(row["id"])
    duration = float(row["duration_s"] or 0.0)

    _require_indexed(deps, row, video_id)

    page = await deps.db.read(
        lambda c: queries.cue_page(c, vid, offset, limit, t_start=span_start, t_end=span_end)
    )
    probe_total, probe_ceiling = await deps.db.read(
        lambda c: queries.probe_cues(
            c, vid, limit, offset, t_start=span_start, t_end=span_end
        )
    )
    more_rows = len(page) > limit
    page = page[:limit]

    lead = deps.settings.deeplink_lead_s
    speakers = include_speakers and deps.db.diarization_enabled
    printed, used, binding = _fit(page, max_text_chars)
    has_more = more_rows or printed < len(page)
    shown = page[:printed]

    body = (
        _tsv_body(shown, video_id, lead, speakers)
        if format == "tsv"
        else _text_body(shown, video_id, lead, speakers)
    )

    # The pager advances by what was *printed*, not by what was asked for:
    # when the char budget binds first, `offset + limit` would skip the cues
    # this page could not afford, and the caller would never know it.
    step = printed if (printed and binding == "max_text_chars") else limit
    nxt = _next_line(
        video_id, has_more, offset + printed, binding, max_text_chars, span_start, span_end
    )

    header = [
        f"{video_id} · {row['title']} — {row['channel_name']}",
        _span_line(shown, duration, used, video_id, span_start, span_end, lead),
        "",
    ]
    footer = [
        "",
        pagination_line("Cues", printed, offset, step, has_more, probe_total, probe_ceiling),
    ]
    if binding == "max_text_chars" and has_more:
        footer.append(
            f"note: max_text_chars ({max_text_chars:,}) bound before limit={limit} — "
            f"{printed} of the {len(page)} cues this page held were printed."
        )
    footer.extend(["", nxt])

    structured: dict[str, Any] = {
        "next": nxt,
        "video_id": video_id,
        "title": str(row["title"]),
        "link": f"https://youtu.be/{video_id}",
        "t_start": span_start,
        "t_end": span_end,
        "cues": [
            {
                "cue_id": int(c["id"]),
                "start": float(c["start_s"]),
                "end": float(c["end_s"]),
                "text": str(c["text"]),
                "speaker": c["speaker"] if speakers else None,
                "link": deeplink(video_id, c["start_s"], lead),
            }
            for c in shown
        ],
        "chars": used,
        "binding_cap": binding,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "shown": printed,
            "has_more": has_more,
            "next_offset": offset + printed if has_more else None,
            "approx_total": probe_total,
            "approx_total_is_floor": probe_ceiling,
        },
    }
    return text_result("\n".join(header + body + footer), structured)


def _require_indexed(deps: Deps, row, video_id: str) -> None:
    """The same two pre-conditions `video-summary` states, said the same way."""
    state = str(row["index_state"])
    if state == "pending":
        raise ToolError(
            "E_NOT_INDEXED",
            f'Video "{video_id}" is in the corpus but the pipeline never ran.',
            deps.hint(
                "index-video",
                "index-video force_reindex=true to build it.",
                "nothing in it is queryable, and this read-only server cannot "
                "build it. list-videos has=all shows what is complete.",
            ),
        )
    if state == "indexing":
        raise ToolError(
            "E_INDEXING",
            f'Video "{video_id}" is mid-pipeline; its transcript is incomplete.',
            "job-status to see progress; cues arrive when the stt stage finishes.",
        )


def _fit(page, max_text_chars: int) -> tuple[int, int, str]:
    """How many cues the char budget affords. Returns (printed, chars, binding)."""
    if not max_text_chars:
        return len(page), sum(len(str(c["text"])) for c in page), "limit"
    used = 0
    for i, cue in enumerate(page):
        length = len(str(cue["text"]))
        if used + length > max_text_chars:
            if i == 0:
                # A budget too small for even one cue still returns one, whole:
                # otherwise the page is empty, the next offset equals this one,
                # and the caller loops forever on the same call.
                return 1, length, "max_text_chars"
            return i, used, "max_text_chars"
        used += length
    return len(page), used, "limit"


def _span_line(
    shown,
    duration: float,
    used: int,
    video_id: str,
    span_start: float | None,
    span_end: float | None,
    lead: int,
) -> str:
    if not shown:
        asked = ""
        if span_start is not None or span_end is not None:
            asked = f" in {clock(span_start or 0)}-{clock(span_end) if span_end else 'end'}"
        return f"No transcript cues{asked} · https://youtu.be/{video_id}"
    first, last = float(shown[0]["start_s"]), float(shown[-1]["end_s"])
    of_total = f" of {duration_clock(duration)}" if duration else ""
    return (
        f"Transcript {clock(first)}-{clock(last)}{of_total} · {len(shown)} cues · "
        f"{used:,} chars · {deeplink(video_id, first, lead)}"
    )


def _text_body(shown, video_id: str, lead: int, speakers: bool) -> list[str]:
    if not shown:
        return [
            "(nothing to read here — video-summary shows which channels of data "
            "this video has, and list-videos has=all which videos have a transcript)"
        ]
    # §3.6's compact form, for the same reason `get-segment-context` uses it:
    # the whole URL on 400 lines would cost a third of the page budget, and the
    # base URL is on the line above.
    lines = [f"TRANSCRIPT (cite one line: https://youtu.be/{video_id} + the ?t= printed on it)"]
    for cue in shown:
        speaker = f" {cue['speaker']}:" if (speakers and cue["speaker"]) else ""
        text = str(cue["text"])
        lines.append(
            f"[{clock(cue['start_s'])} ?t={deeplink_t(cue['start_s'], lead)}]{speaker} {text}"
        )
    return lines


def _tsv_body(shown, video_id: str, lead: int, speakers: bool) -> list[str]:
    if not shown:
        return ["(no cues)"]
    fields = ["t", "clock", "cue_id"] + (["speaker"] if speakers else []) + ["text"]
    rows = [
        {
            "t": deeplink_t(cue["start_s"], lead),
            "clock": clock(cue["start_s"]),
            "cue_id": int(cue["id"]),
            "speaker": cue["speaker"] or "",
            "text": str(cue["text"]),
        }
        for cue in shown
    ]
    return [
        f"# cite a line as https://youtu.be/{video_id}?t=<the t column>",
        tsv(rows, fields),
    ]


def _next_line(
    video_id: str,
    has_more: bool,
    next_offset: int,
    binding: str,
    max_text_chars: int,
    span_start: float | None = None,
    span_end: float | None = None,
) -> str:
    if has_more:
        # The offset counts cues inside the span, so it only means the right
        # thing when the next call repeats the span.
        span = "".join(
            f" {name}={value:g}"
            for name, value in (("t_start", span_start), ("t_end", span_end))
            if value is not None
        )
        raise_hint = (
            ""
            if binding == "limit" or max_text_chars >= MAX_TEXT_CHARS
            else f" (or raise max_text_chars, up to {MAX_TEXT_CHARS:,}, for fewer, bigger pages)"
        )
        return (
            f'next: offset={next_offset}{span} continues this transcript{raise_hint}. '
            f'To stop reading and jump instead, search q="…" video_id="{video_id}".'
        )
    return (
        "next: that is the end of the transcript. "
        f'get-frames or get-segment-context video_id="{video_id}" t=… for what the '
        "words alone do not carry — slides, diagrams, on-screen code."
    )
