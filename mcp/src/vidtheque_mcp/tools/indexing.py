"""`index-video` and `job-status`.

The job *bookkeeping* is real: rows are created through the state machine in
``jobs/store.py``, the in-flight guard is the partial unique index, progress is
a trigger-maintained rollup, and ``job-status`` renders ``state`` verbatim.

The pipeline *execution* is the next milestone, so a claimed item fails with
``E_NOT_IMPLEMENTED`` and says so plainly. Nothing pretends a video is
searchable when it is not.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from mcp_types import CallToolResult

from ..db import queries
from ..errors import ToolError, bad_param, unknown_job
from ..jobs import store as jobs_store
from ..jobs.store import DuplicateInFlight
from ..text import clamp, duration_clock, iso_z, split_csv, validate_tag
from .base import Deps, handle_errors, text_result

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_URL = re.compile(r"^https?://", re.I)
EXPANSIONS = ("none", "playlist", "channel_recent")
CHANNEL_SETS = ("all", "transcript", "ocr", "frames")
MAX_ERROR_CHARS = 400


def normalize_url(raw: str) -> str:
    """Bare 11-char ids are accepted; anything else must be a URL yt-dlp knows."""
    candidate = raw.strip()
    if _YT_ID.match(candidate):
        return f"https://youtu.be/{candidate}"
    if not _URL.match(candidate):
        raise ToolError(
            "E_UNSUPPORTED_SOURCE",
            f"{raw!r} is not a URL or a YouTube id.",
            "supported: youtube.com / youtu.be video, playlist and channel URLs, "
            "or a bare 11-character video id.",
        )
    return candidate


def source_id_of(url: str) -> str | None:
    match = re.search(r"(?:youtu\.be/|v=|/shorts/|/embed/)([A-Za-z0-9_-]{11})", url)
    return match.group(1) if match else None


@handle_errors
async def index_video(
    deps: Deps,
    url: str | None = None,
    urls: list[str] | None = None,
    expand: str = "playlist",
    max_items: int = 25,
    tags: str | None = None,
    force_reindex: bool = False,
    channels: str = "all",
    priority: str = "normal",
) -> CallToolResult:
    if expand not in EXPANSIONS:
        raise bad_param(f"expand must be one of {', '.join(EXPANSIONS)}.")
    if priority not in ("normal", "high"):
        raise bad_param('priority must be "normal" or "high".')
    for part in [c.strip() for c in channels.split(",") if c.strip()]:
        if part not in CHANNEL_SETS:
            raise bad_param(
                f"channels must be 'all' or a subset of transcript,ocr,frames — got {part!r}."
            )
    max_items = clamp(max_items, 1, 200, 25)
    tag_list = split_csv(tags, 10, "tags")
    for tag in tag_list:
        validate_tag(tag)

    raw = [u for u in ([url] if url else []) + list(urls or []) if u]
    if not raw:
        raise bad_param("one of url or urls is required.", 'e.g. url="https://youtu.be/…".')
    if len(raw) > 10:
        raise bad_param("urls accepts at most 10 entries.", "split the batch.")
    normalized = [normalize_url(u) for u in raw][:max_items]

    if not deps.db.writes_allowed:
        raise ToolError(
            "E_FEATURE_DISABLED",
            "indexing is disabled: the corpus config and the vector tables "
            f"disagree ({deps.db.vectors.reason})",
            "fix the config/dimension mismatch and restart; search still works.",
        )

    # Already indexed, no force_reindex -> no job, return the existing id.
    existing = await deps.db.read(lambda c: _existing(c, normalized))
    if len(existing) == len(normalized) and not force_reindex:
        row = existing[normalized[0]]
        lines = [
            f"Already indexed: {row['public_id']} — \"{row['title']}\" "
            f"(indexed {iso_z(row['indexed_at']) or 'unknown'})",
            f'No job created. next: video-summary video_id="{row["public_id"]}", '
            "or index-video force_reindex=true to rebuild.",
        ]
        return text_result(
            "\n".join(lines),
            {"job_id": None, "already_indexed": [r["public_id"] for r in existing.values()]},
        )

    items = [(u, existing[u]["id"] if u in existing else None) for u in normalized]
    args: dict[str, Any] = {
        "expand": expand,
        "max_items": max_items,
        "tags": tag_list,
        "force_reindex": force_reindex,
        "channels": channels,
    }
    try:
        job_public_id = await deps.db.write(
            lambda c: jobs_store.create_job(
                c,
                "reindex" if force_reindex else "index",
                args,
                items,
                priority=50 if priority == "high" else 100,
            )
        )
    except DuplicateInFlight as clash:
        raise ToolError(
            "E_INDEXING",
            "one of those videos is already queued or indexing.",
            f'job-status job_id="{clash.job_public_id}"'
            if clash.job_public_id
            else "job-status to see what is running.",
        ) from clash

    active = await deps.db.read(jobs_store.active_job_count)
    lines = [
        f"Job queued: {job_public_id}",
        f"{len(items)} video(s):",
    ]
    for entry in normalized[:10]:  # never echoes the full expansion
        lines.append(f"  {entry}")
    if len(normalized) > 10:
        lines.append(f"  … and {len(normalized) - 10} more")
    lines.append("Stages: download → transcribe → keyframes → ocr → embed")
    lines.append(f"Queue position {active} · estimated 1-3 minutes per hour of video")
    if tag_list:
        lines.append("Tags to apply: " + ", ".join(tag_list))
    lines.append("")
    lines.append("Nothing from this video is searchable until the job reports done.")
    lines.append(f'next: job-status job_id="{job_public_id}" (poll no more than every 15s).')

    return text_result(
        "\n".join(lines),
        {"job_id": job_public_id, "items": len(items), "tags": tag_list},
    )


def _existing(conn: sqlite3.Connection, urls: list[str]) -> dict[str, sqlite3.Row]:
    found: dict[str, sqlite3.Row] = {}
    for url in urls:
        source_id = source_id_of(url)
        if source_id is None:
            continue
        row = conn.execute(
            "SELECT id, public_id, title, indexed_at, index_state FROM videos "
            "WHERE source_id = ? AND index_state = 'ready'",
            (source_id,),
        ).fetchone()
        if row is not None:
            found[url] = row
    return found


# ------------------------------------------------------------------ job-status


@handle_errors
async def job_status(
    deps: Deps,
    job_id: str | None = None,
    video_id: str | None = None,
    state: str = "active",
    limit: int = 5,
) -> CallToolResult:
    if state not in ("all", "active", "failed", "done"):
        raise bad_param("state must be one of all, active, failed, done.")
    limit = clamp(limit, 1, 20, 5)

    if job_id:
        row = await deps.db.read(lambda c: jobs_store.get_job(c, job_id))
        if row is None:
            raise unknown_job(job_id)
        return await _single(deps, row)

    if video_id:
        video = await deps.db.read(lambda c: queries.lookup_video(c, video_id))
        if video is None:
            from ..errors import unknown_video

            raise unknown_video(video_id)
        row = await deps.db.read(
            lambda c: jobs_store.latest_job_for_video(c, int(video["id"]))
        )
        if row is None:
            return text_result(
                f"No indexing job has ever run for {video_id}.\n"
                f'next: index-video url="https://youtu.be/{video_id}"',
                {"job": None},
            )
        return await _single(deps, row)

    rows = await deps.db.read(lambda c: jobs_store.list_jobs(c, state, limit))
    return await _list(deps, rows, state)


async def _single(deps: Deps, row: sqlite3.Row) -> CallToolResult:
    job_id = int(row["id"])
    items = await deps.db.read(lambda c: jobs_store.job_items(c, job_id, 20))
    pct = int(round(float(row["progress"] or 0.0) * 100))
    started = iso_z(row["started_at"])
    lines = [
        f"{row['public_id']} · state: {row['state']} · {pct}%"
        + (f" · started {started}" if started else "")
    ]
    if row["cancel_requested"]:
        lines.append("cancel requested — the job stops at the next stage boundary.")

    running = next((i for i in items if i["state"] == "running"), None)
    current = running or (items[0] if items else None)
    if current is not None:
        label = (
            f"{current['public_id']} \"{current['title']}\" — {current['channel_name']} "
            f"({duration_clock(current['duration_s'])})"
            if current["public_id"]
            else str(current["source_url"])
        )
        lines.append(f"Video: {label}")
        positions = {
            stage: i
            for i, (_wire, internals) in enumerate(jobs_store.WIRE_STAGES)
            for stage in internals
        }
        current_pos = positions.get(current["stage"])
        for i, (wire, _internal) in enumerate(jobs_store.WIRE_STAGES):
            lines.append(f"  {wire:<11}{_wire_state(current, i, current_pos)}")

    failed = [i for i in items if i["state"] == "failed"]
    if failed:
        first = failed[0]
        message = str(first["error_message"] or "")[-MAX_ERROR_CHARS:]
        lines.append("")
        lines.append(f"error: {first['error_code']} — {message}")

    lines.append("")
    if row["state"] == "done":
        lines.append("Queryable now: everything from this job.")
        lines.append('next: video-summary video_id="…"')
    elif row["state"] == "failed":
        lines.append("Queryable now: nothing from this job.")
        lines.append('next: index-video url="…" force_reindex=true to retry.')
    else:
        lines.append("Queryable now: nothing (data is written on stage completion).")
        lines.append(f'next: job-status job_id="{row["public_id"]}" again in ~60s.')

    return text_result(
        "\n".join(lines),
        {
            "job_id": str(row["public_id"]),
            "state": str(row["state"]),
            "progress": float(row["progress"] or 0.0),
            "n_items": int(row["n_items"]),
            "n_done": int(row["n_done"]),
            "n_failed": int(row["n_failed"]),
            "error_code": row["error_code"],
        },
    )


def _wire_state(item: sqlite3.Row, wire_pos: int, current_pos: int | None) -> str:
    """State of one wire stage, inferred from the item's current position.

    The item row only carries the stage it is *on*; wire stages before it in
    WIRE_STAGES order are complete (the runner advances strictly in order),
    later ones pending. Before this, every stage except the running one
    printed "pending" — including the ones already done.
    """
    if item["state"] == "done":
        return "done"
    if current_pos is None:
        return "pending"
    if wire_pos < current_pos:
        return "done"
    if wire_pos == current_pos:
        if item["state"] == "failed":
            return "failed"
        if item["state"] == "running":
            return f"running  {int(float(item['stage_pct']) * 100)}%"
    return "pending"


async def _list(deps: Deps, rows: list[sqlite3.Row], state: str) -> CallToolResult:
    active = sum(1 for r in rows if r["state"] in ("queued", "running"))
    failed = sum(1 for r in rows if r["state"] == "failed")
    lines = [f"Jobs: {active} active, {failed} failed (filter state={state})"]
    for row in rows:
        pct = int(round(float(row["progress"] or 0.0) * 100))
        lines.append(
            f"{row['public_id']}  {str(row['state']):<10} {pct:>3}%  "
            f"{int(row['n_done'])}/{int(row['n_items'])} items"
        )
    if not rows:
        lines.append("(none)")
    lines.append('next: index-video url="…" force_reindex=true to retry a failed job.')
    return text_result(
        "\n".join(lines),
        {
            "jobs": [
                {
                    "job_id": str(r["public_id"]),
                    "state": str(r["state"]),
                    "progress": float(r["progress"] or 0.0),
                }
                for r in rows
            ]
        },
    )
