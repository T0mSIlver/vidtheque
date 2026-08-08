"""`get-frames` — authenticated URLs by default, inline base64 by opt-in.

MCP's `ImageContent` is the correct implementation and it is badly broken in
real clients: Claude Code passes it through as raw base64 *text*, ~15,000-25,000
tokens per image instead of ~1,600, and the model cannot see it
(claude-code#31208, closed not-planned). The two mitigations in the wild are
local file paths (useless for a remote server) and serving a URL from the MCP
server itself. We default to the URL.

When more images are requested than the inline budget allows, the extras
**downgrade to URLs rather than failing**.
"""

from __future__ import annotations

import base64
import sqlite3
from pathlib import Path
from typing import Any

from mcp_types import CallToolResult, ContentBlock, ImageContent, TextContent

from ..db import queries
from ..errors import ToolError, bad_param, unknown_frame, unknown_video
from ..http.frames import parse_frame_id
from ..text import clamp, clock, deeplink, iso_z, middle_truncate
from ..timeparse import parse_offset
from .base import Deps, blocks_result, handle_errors

MAX_SPAN_S = 600.0
OCR_CHARS_PER_FRAME = 300


@handle_errors
async def run(
    deps: Deps,
    frame_ids: list[str] | None = None,
    video_id: str | None = None,
    t_start: float | str | None = None,
    t_end: float | str | None = None,
    return_: str = "url",
    limit: int = 3,
    width: int = 512,
    quality: int = 75,
    include_ocr: bool = True,
) -> CallToolResult:
    mode = (return_ or "url").lower()
    if mode not in ("url", "image"):
        raise bad_param('return must be "url" or "image".', 'omit it for "url".')
    limit = clamp(limit, 1, 12, 3)
    width = clamp(width, 128, 1280, 512)
    quality = clamp(quality, 20, 95, 75)

    if not frame_ids and not video_id:
        raise bad_param(
            "pass either frame_ids or video_id.",
            "frame ids come from search, video-summary or get-segment-context.",
        )
    if frame_ids and len(frame_ids) > 12:
        raise bad_param("frame_ids accepts at most 12 ids.", "call again with fewer ids.")

    failures: list[str] = []
    if frame_ids:
        rows, failures = await _by_ids(deps, frame_ids[:limit])
    else:
        assert video_id is not None
        span_start = parse_offset(t_start, "t_start")
        span_end = parse_offset(t_end, "t_end")
        if span_start is not None and span_end is not None and span_end - span_start > MAX_SPAN_S:
            raise bad_param(
                f"the requested span is {int(span_end - span_start)}s; the limit is "
                f"{int(MAX_SPAN_S)}s.",
                "narrow t_start/t_end, or pass explicit frame_ids.",
            )
        video = await deps.db.read(lambda c: queries.lookup_video(c, video_id))
        if video is None:
            raise unknown_video(video_id)
        vid = int(video["id"])
        rows = await deps.db.read(
            lambda c: queries.keyframes_in_span(c, vid, span_start, span_end, limit)
        )

    blocks: list[ContentBlock] = []
    records: list[dict[str, Any]] = []
    inline_used = 0
    inline_bytes = 0
    header_placeholder = ""
    lines: list[str] = []
    expiry_note: int | None = None

    for row in rows:
        public_id = str(row["public_id"])
        ordinal = int(row["ord"])
        frame_id = f"{public_id}-{ordinal:05d}"
        t_s = float(row["t_s"])
        link = deeplink(public_id, t_s, deps.settings.deeplink_lead_s)
        ocr_text = middle_truncate(str(row["ocr_text"] or ""), OCR_CHARS_PER_FRAME)
        record: dict[str, Any] = {
            "frame_id": frame_id,
            "video_id": public_id,
            "t": round(t_s, 2),
            "link": link,
        }

        want_inline = mode == "image" and inline_used < deps.settings.inline_frame_max
        payload: bytes | None = None
        if want_inline:
            path = _resolve(deps.settings.data_dir, str(row["jpeg_path"]))
            payload = path.read_bytes() if path is not None and path.is_file() else None
            if payload is None:
                failures.append(f"{frame_id}: keyframe file is missing on disk")
                continue
            if inline_bytes + len(payload) > deps.settings.inline_frame_bytes:
                if inline_used == 0:
                    raise ToolError(
                        "E_TOO_LARGE",
                        f"{frame_id} is {len(payload):,} bytes, over the inline budget.",
                        'use return="url", or lower width/quality.',
                    )
                want_inline = False
                payload = None

        if want_inline and payload is not None:
            inline_used += 1
            inline_bytes += len(payload)
            head = f"📷 {frame_id} · {row['title']} · {clock(t_s)} · {link}"
            blocks.append(TextContent(type="text", text=head))
            # mimeType is image/jpeg and the bytes ARE JPEG. screenpipe labels
            # ffmpeg-emitted MJPEG as image/png; a mislabelled image is a
            # client-side decode failure with no useful error.
            blocks.append(
                ImageContent(
                    type="image",
                    data=base64.b64encode(payload).decode("ascii"),
                    mime_type="image/jpeg",
                )
            )
            record["inline"] = True
        else:
            url, expires_at = _signed_url(deps, frame_id, width, quality)
            expiry_note = expires_at
            lines.append(f"{frame_id} · {clock(t_s)} · {link}")
            lines.append(f"  image: {url}")
            if include_ocr and ocr_text:
                lines.append(f'  ocr: "{ocr_text}"')
            record["url"] = url
            record["expires_at"] = expires_at
            record["inline"] = False
        if include_ocr and ocr_text:
            record["ocr"] = ocr_text
        records.append(record)

    total = len(records) + len(failures)
    if mode == "image" and inline_used and len(records) > inline_used:
        header_placeholder = (
            f"Frames: {len(records)}/{total} ({inline_used} inline, "
            f"{len(records) - inline_used} as URLs — inline cap is "
            f"{deps.settings.inline_frame_max} images / "
            f"{deps.settings.inline_frame_bytes // (1024 * 1024)}MB per call)"
        )
    else:
        header_placeholder = f"Frames: {len(records)}/{total}"

    text_lines = [header_placeholder, *lines]
    for failure in failures:
        text_lines.append(f"failed: {failure}")
    # `_signed_url` returns 0 for "unsigned" (auth `none`), which is not None —
    # so this said "URLs expire None. They are signed", both halves wrong, on
    # every default-mode call (research/e2e-smoke-2026-08-08.md §4.5).
    if expiry_note:
        text_lines.append(
            f"URLs expire {iso_z(expiry_note)}. They are signed — no auth header "
            "needed to fetch them."
        )
    elif expiry_note is not None:
        text_lines.append(
            "URLs do not expire and are not signed: this server runs with auth "
            "disabled, so the frame route is open to anyone who can reach it."
        )

    blocks.insert(0, TextContent(type="text", text="\n".join(text_lines)))
    return blocks_result(
        blocks,
        {"frames": records, "failed": failures, "return": mode},
    )


def _signed_url(deps: Deps, frame_id: str, width: int, quality: int) -> tuple[str, int]:
    signer = deps.frame_signer
    if signer is None:
        # `none` mode: the whole server is open, so an unsigned path is honest.
        return (
            f"{deps.settings.public_url.rstrip('/')}/frames/{frame_id}.jpg?w={width}&q={quality}",
            0,
        )
    return signer.url(deps.settings.public_url, frame_id, width, quality)


async def _by_ids(deps: Deps, ids: list[str]) -> tuple[list[sqlite3.Row], list[str]]:
    """Per-frame failures are collected, not fail-fast."""
    parsed: list[tuple[str, int]] = []
    failures: list[str] = []
    for frame_id in ids:
        pair = parse_frame_id(frame_id)
        if pair is None:
            failures.append(f"{frame_id}: not a valid frame id (<video_id>-NNNNN)")
            continue
        parsed.append(pair)

    public_ids = [p for p, _ in parsed]
    known = await deps.db.read(lambda c: queries.lookup_video_ids(c, public_ids))
    pairs = [(known[p], o) for p, o in parsed if p in known]
    for public_id, ordinal in parsed:
        if public_id not in known:
            failures.append(f"{public_id}-{ordinal:05d}: video {public_id} is not in the corpus")

    rows = await deps.db.read(lambda c: queries.keyframes_by_ord(c, pairs))
    found = {(int(r["video_id"]), int(r["ord"])) for r in rows}
    for video_row_id, ordinal in pairs:
        if (video_row_id, ordinal) in found:
            continue
        row_id = video_row_id
        public_id = next(p for p, i in known.items() if i == row_id)
        highest = await deps.db.read(lambda c: queries.max_ord(c, row_id))
        if len(pairs) == 1:
            raise unknown_frame(f"{public_id}-{ordinal:05d}", public_id, highest)
        failures.append(
            f"{public_id}-{ordinal:05d}: no such keyframe "
            f"(valid ordinals 00000-{(highest or 0):05d})"
        )
    return rows, failures


def _resolve(data_dir: Path, relative: str) -> Path | None:
    candidate = (data_dir / relative).resolve()
    try:
        candidate.relative_to(data_dir.resolve())
    except ValueError:
        return None
    return candidate
