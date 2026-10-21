"""`get-segment-context` — the drill-down leaf (tool-surface §4.5).

Double-capped: `window` seconds **and** `max_text_chars`, whichever binds
first, with the binding one named in the payload. OCR is capped at 8 frames /
1200 chars and frame refs at 12 ids — all independent of `window`, so
`window=300` does not multiply the image-adjacent cost.

Never returns image content. Ids only.
"""

from __future__ import annotations

from typing import Any

from mcp_types import CallToolResult

from ..db import queries
from ..errors import bad_param, unknown_video
from ..text import clamp, clamp_text_chars, clock, deeplink, deeplink_t, middle_truncate
from ..timeparse import parse_offset
from .base import Deps, handle_errors, text_result
from .transcript import _require_indexed

MAX_OCR_FRAMES = 8
MAX_OCR_CHARS = 1200
OCR_CHARS_PER_FRAME = 300
MAX_FRAME_REFS = 12


@handle_errors
async def run(
    deps: Deps,
    video_id: str,
    t: float | str | None = None,
    window: float = 45,
    cue_id: int | None = None,
    include_ocr: bool = True,
    include_frame_refs: bool = True,
    include_chapter: bool = True,
    include_links: bool = False,
    max_text_chars: int = 4000,
) -> CallToolResult:
    window = float(clamp(int(window), 5, 300, 45))
    max_text_chars = clamp_text_chars(max_text_chars, 200, 20_000, 4000)

    row = await deps.db.read(lambda c: queries.lookup_video(c, video_id))
    if row is None:
        raise unknown_video(video_id, can_index=deps.offers("index-video"))
    _require_indexed(deps, row, video_id)
    vid = int(row["id"])
    duration = float(row["duration_s"] or 0.0)

    clamped = False
    if cue_id is not None:
        cue = await deps.db.read(lambda c: queries.cue_by_id(c, int(cue_id)))
        if cue is None:
            raise bad_param(f"cue_id {cue_id} does not exist.", "use a cue_id from a search result.")
        if int(cue["video_id"]) != vid:
            raise bad_param(
                f"cue_id {cue_id} belongs to video {cue['public_id']}, not {video_id}.",
                f'call again with video_id="{cue["public_id"]}".',
            )
        centre = float(cue["start_s"])
    else:
        parsed = parse_offset(t, "t")
        if parsed is None:
            raise bad_param("t or cue_id is required.", "pass t exactly as a result gave it.")
        centre = parsed
        if duration and centre > duration:
            centre = duration
            clamped = True

    transcript = await deps.db.read(lambda c: queries.context_transcript(c, vid, centre, window))
    chapter = (
        await deps.db.read(lambda c: queries.context_chapter(c, vid, centre))
        if include_chapter
        else None
    )
    frames = (
        await deps.db.read(lambda c: queries.context_ocr(c, vid, centre, window, MAX_OCR_FRAMES))
        if (include_ocr or include_frame_refs)
        else []
    )
    links = (
        await deps.db.read(
            lambda c: queries.video_links(c, vid, 10, centre - window, centre + window)
        )
        if include_links
        else []
    )

    lead = deps.settings.deeplink_lead_s
    lines = [f"{video_id} · {row['title']} — {row['channel_name']}"]
    if chapter is not None:
        lines.append(
            f"Chapter: \"{chapter['title']}\" "
            f"({clock(chapter['start_s'])}-{clock(chapter['end_s'])})"
        )
    lo, hi = max(0.0, centre - window), centre + window
    lines.append(
        f"Window: {clock(lo)}-{clock(hi)} (t={int(centre)} ±{int(window)}s) · "
        f"{deeplink(video_id, centre, lead)}"
    )
    if clamped:
        lines.append(f"note: t was past the end of the video and was clamped to {int(centre)}s.")

    lines.append("")
    # Twenty to forty stamped lines under exactly one link is what made a
    # consumer cite two different minutes of the same talk with the same
    # `?t=982` — 27 s wrong on the second, in a product whose third pillar is
    # "the link that lands on the second" (terra eval §4.4). Every line now
    # carries its own `?t=`, in the compact §3.6 form: the whole URL on 40 lines
    # would cost a third of the transcript budget, the seven-character suffix
    # costs ~2%, and the base URL is on the line above.
    lines.append(f"TRANSCRIPT (cite one line: https://youtu.be/{video_id} + the ?t= printed on it)")
    lo_i, hi_i, used, binding = _fit_around(transcript, centre, max_text_chars)
    printed = transcript[lo_i:hi_i]
    for cue in printed:
        speaker = f" {cue['speaker']}:" if cue["speaker"] else ""
        lines.append(
            f"[{clock(cue['start_s'])} ?t={deeplink_t(cue['start_s'], lead)}]{speaker} {cue['text']}"
        )
    if printed:
        first_id = int(printed[0]["cue_id"])
        last_id = int(printed[-1]["cue_id"])
        budget = "under" if binding == "window" else "capped by"
        lines.append(
            f"(cues {first_id}-{last_id} · {used:,} chars, {budget} the "
            f"{max_text_chars or 'unbounded'} budget; {binding} bound first)"
        )
    else:
        lines.append("(no transcript cues in this window)")

    frame_ids = [f"{video_id}-{int(f['ord']):05d}" for f in frames][:MAX_FRAME_REFS]
    if include_ocr and frames:
        lines.append("")
        lines.append(f"ON-SCREEN TEXT ({len(frames)} keyframes)")
        ocr_used = 0
        # `max_text_chars=0` is the documented opt-out, and the truncation
        # marker this block prints names it by name — so it has to work here
        # too, not only on the transcript above (demo-queries-2026-08-09 §7.3).
        # The independent 1200-char block cap still binds; it announces itself
        # in words rather than through a marker that promises an opt-out.
        per_frame = OCR_CHARS_PER_FRAME if max_text_chars else 0
        ocr_capped = False
        for frame in frames:
            # Charged at what is shown, and a frame that does not fit is skipped,
            # not a stop: one wall of code must not hide every slide after it.
            shown = middle_truncate(str(frame["screen_text"] or ""), per_frame)
            if not shown:
                continue
            if ocr_used + len(shown) > MAX_OCR_CHARS:
                ocr_capped = True
                continue
            ocr_used += len(shown)
            lines.append(
                f"[{clock(frame['t_s'])} ?t={deeplink_t(frame['t_s'], lead)}] "
                f"{video_id}-{int(frame['ord']):05d}  {shown}"
            )
        if ocr_capped:
            lines.append(f"… ({len(frames)} keyframes, OCR capped at {MAX_OCR_CHARS} chars)")

    if include_frame_refs and frame_ids:
        lines.append("")
        lines.append("FRAMES: " + ", ".join(frame_ids))
        lines.append(f'  → get-frames frame_ids=["{frame_ids[0]}"] to look at it')

    if include_links and links:
        lines.append("")
        lines.append("LINKS")
        for link in links:
            lines.append(f"  {clock(link['t_s'])} {link['url']} {link['title'] or ''}")

    nxt = (
        "next: if the line you want runs past this window, call again with a larger "
        "window= (up to 300) rather than guessing a new t; or "
        f'search q="…" video_id="{video_id}" to find where else this comes up.'
    )
    lines.append("")
    lines.append(nxt)

    structured: dict[str, Any] = {
        "next": nxt,
        "video_id": video_id,
        "t": centre,
        "window": window,
        "cues": [
            {
                "cue_id": int(c["cue_id"]),
                "start": float(c["start_s"]),
                "end": float(c["end_s"]),
                "text": str(c["text"]),
                "speaker": c["speaker"],
                # One key, and a conformant client never composes a URL at all.
                "link": deeplink(video_id, c["start_s"], lead),
            }
            for c in printed
        ],
        "frame_ids": frame_ids,
        "chapter": dict(chapter) if chapter is not None else None,
        "binding_cap": binding,
    }
    return text_result("\n".join(lines), structured)


def _fit_around(transcript, centre: float, max_text_chars: int) -> tuple[int, int, int, str]:
    """The contiguous cues `[lo, hi)` the budget affords, grown outward from t.

    Filling forward from the window's start could end before t. The cue at t
    (else the nearest one) is always printed whole, even over budget, like
    get-transcript's `_fit`; then the nearer neighbour is added while it fits.
    """
    if not transcript:
        return 0, 0, 0, "window"
    lengths = [len(str(c["text"])) for c in transcript]
    if not max_text_chars:
        return 0, len(transcript), sum(lengths), "window"

    def distance(i: int) -> float:
        cue = transcript[i]
        return max(float(cue["start_s"]) - centre, centre - float(cue["end_s"]), 0.0)

    at = min(range(len(transcript)), key=lambda i: (distance(i), i))
    lo, hi, used = at, at + 1, lengths[at]
    left_open, right_open = lo > 0, hi < len(transcript)
    binding = "max_text_chars" if used > max_text_chars else "window"
    while left_open or right_open:
        go_left = left_open and (not right_open or distance(lo - 1) <= distance(hi))
        i = lo - 1 if go_left else hi
        if used + lengths[i] > max_text_chars:
            binding = "max_text_chars"
            # that side is closed: skipping a cue would break contiguity
            if go_left:
                left_open = False
            else:
                right_open = False
            continue
        used += lengths[i]
        if go_left:
            lo -= 1
            left_open = lo > 0
        else:
            hi += 1
            right_open = hi < len(transcript)
    return lo, hi, used, binding
