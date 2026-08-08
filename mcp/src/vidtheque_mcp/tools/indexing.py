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
from typing import Any, Sequence

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

    # A claim whose process died must not stand in the way of a new job: sweep
    # before looking, so "already indexing" only ever means a live job.
    await _reclaim_stale(deps)

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

    # Every video we already have a row for, whatever state it is in — an
    # `indexing` row is exactly the one whose claim has to be checked, and
    # looking only at `ready` rows is what let a second job through with a
    # NULL `video_id` and no guard on it.
    known = await deps.db.read(lambda c: _known(c, normalized))
    # Partition *before* the job exists. A mixed wave — nine current videos and
    # one new one — used to queue all ten, and `fetch` probes and downloads
    # before any later stage discovers the video was current. With the retention
    # default having deleted the mp4, that is nine redundant downloads a wave.
    current = {} if force_reindex else existing
    items = [_item_for(url, known.get(url), url in current) for url in normalized]
    queued = [item for item in items if not item.terminal]
    already = [str(current[url]["public_id"]) for url in normalized if url in current]
    args: dict[str, Any] = {
        "expand": expand,
        "max_items": max_items,
        "tags": tag_list,
        "force_reindex": force_reindex,
        "channels": channels,
    }
    try:
        job_public_id = await deps.db.write(
            lambda c: _create_job(
                c,
                items,
                "reindex" if force_reindex else "index",
                args,
                priority=50 if priority == "high" else 100,
                force=force_reindex,
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
        f"{len(queued)} video(s):",
    ]
    for item in queued[:10]:  # never echoes the full expansion
        lines.append(f"  {item.url}")
    if len(queued) > 10:
        lines.append(f"  … and {len(queued) - 10} more")
    if already:
        lines.append(
            f"{len(already)} already indexed and left alone: " + ", ".join(already[:10])
        )
    lines.append("Stages: download → transcribe → keyframes → ocr → embed")
    lines.append(f"Queue position {active} · estimated 1-3 minutes per hour of video")
    if tag_list:
        lines.append("Tags to apply: " + ", ".join(tag_list))
    lines.append("")
    lines.append("Nothing from this video is searchable until the job reports done.")
    lines.append(f'next: job-status job_id="{job_public_id}" (poll no more than every 15s).')

    return text_result(
        "\n".join(lines),
        {
            "job_id": job_public_id,
            "items": len(queued),
            "n_items": len(items),
            "already_indexed": already,
            "tags": tag_list,
        },
    )


def _item_for(url: str, row: sqlite3.Row | None, current: bool) -> jobs_store.NewItem:
    """One URL's row: work to do, or a terminal `skipped` that says why not."""
    video_id = int(row["id"]) if row is not None else None
    if not current or row is None:
        return jobs_store.NewItem(url, video_id)
    return jobs_store.NewItem(
        url,
        video_id,
        "skipped",
        "E_ALREADY_INDEXED",
        f"already indexed as {row['public_id']} "
        f"(indexed {iso_z(row['indexed_at']) or 'unknown'}); nothing was re-fetched — "
        "index-video force_reindex=true to rebuild it.",
    )


def _existing(conn: sqlite3.Connection, urls: list[str]) -> dict[str, sqlite3.Row]:
    return _lookup(conn, urls, ready_only=True)


def _known(conn: sqlite3.Connection, urls: list[str]) -> dict[str, sqlite3.Row]:
    return _lookup(conn, urls, ready_only=False)


def _lookup(
    conn: sqlite3.Connection, urls: list[str], *, ready_only: bool
) -> dict[str, sqlite3.Row]:
    found: dict[str, sqlite3.Row] = {}
    clause = " AND index_state = 'ready'" if ready_only else ""
    for url in urls:
        source_id = source_id_of(url)
        if source_id is None:
            continue
        row = conn.execute(
            "SELECT id, public_id, title, indexed_at, index_state FROM videos "
            "WHERE source_id = ?" + clause,
            (source_id,),
        ).fetchone()
        if row is not None:
            found[url] = row
    return found


async def _reclaim_stale(deps: Deps) -> None:
    """Requeue claims whose process is gone, through the runner when there is one.

    The runner knows which jobs *this* process is driving; a sweep that does
    not would eventually reclaim a job out from under a live event loop.
    """
    if deps.runner is not None:
        await deps.runner.reclaim_stale()
        return
    stale_after_s = deps.settings.stale_claim_s
    if await deps.db.read(lambda c: jobs_store.stale_claims(c, stale_after_s)):
        await deps.db.write(lambda c: jobs_store.reclaim_stale(c, stale_after_s))


def _create_job(
    conn: sqlite3.Connection,
    items: list[jobs_store.NewItem],
    kind: str,
    args: dict[str, Any],
    *,
    priority: int,
    force: bool,
) -> str:
    """Resolve every in-flight claim, then insert the job — one transaction.

    ``force_reindex`` **supersedes** a claim nobody is holding (a queued item,
    or one just reclaimed from a dead process) and **refuses** a live one. What
    it must never do is what it used to: create an item, let the partial unique
    index refuse it mid-pipeline, and call the resulting no-op job `done`.
    """
    for item in items:
        url, video_id = item.url, item.video_id
        if video_id is None or item.terminal:
            # A terminal item claims nothing, so nothing can clash with it.
            continue
        claim = jobs_store.inflight_claim(conn, video_id)
        if claim is None:
            continue
        if not force or claim["live"]:
            raise _already_indexing(url, claim)
        reason = f"superseded by a force_reindex of {url}"
        jobs_store.cancel_item(conn, int(claim["item_id"]), reason)
        jobs_store.log(conn, int(claim["job_id"]), reason, "warn", int(claim["item_id"]))
    return jobs_store.create_job(conn, kind, args, items, priority)


def _already_indexing(url: str, claim: sqlite3.Row) -> ToolError:
    """Live claim -> refuse and say so; queued claim -> point at force_reindex.

    Only ever raised with the video *not* queued: the caller is told what holds
    it, never handed a job id that will do no work.
    """
    job = str(claim["public_id"])
    if claim["live"]:
        return ToolError(
            "E_INDEXING",
            f"{url} is being indexed right now by {job} "
            f"(last progress {int(claim['quiet_s'])}s ago). Nothing was queued.",
            f'job-status job_id="{job}" — wait for it to finish, or cancel it and '
            "then retry with force_reindex=true.",
            extra={"job_id": job},
        )
    return ToolError(
        "E_INDEXING",
        f"{url} is already queued by {job}. Nothing was queued.",
        f'job-status job_id="{job}", or index-video force_reindex=true to '
        "supersede that item and start fresh.",
        extra={"job_id": job},
    )


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
    item_errors = await deps.db.read(lambda c: jobs_store.item_error_counts(c, job_id))
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
    elif str(row["error_code"] or "") == "E_RATE_LIMIT":
        # No item ended up failing, so nothing above would have said it: the
        # source throttled this box mid-job and the retry got through. It still
        # decides whether the *next* job should start now.
        lines.append("")
        lines.append(
            "rate-limited: " + str(row["error_message"] or "")[-MAX_ERROR_CHARS:]
        )

    n_done = int(row["n_done"])
    n_failed = int(row["n_failed"])
    n_skipped = int(row["n_skipped"])
    n_cancelled = int(row["n_cancelled"])
    skipped_note = _skipped_note(items)

    lines.append("")
    if row["state"] == "done":
        # "everything from this job" was printed unconditionally, including for
        # a job whose only item was skipped and which therefore indexed
        # nothing. The line says what the job produced, or that it produced
        # nothing and why.
        if n_done:
            tail = _aside(n_failed, n_skipped, n_cancelled)
            lines.append(f"Queryable now: the {n_done} video(s) this job indexed{tail}.")
            lines.append('next: video-summary video_id="…"')
        else:
            lines.append("Queryable now: nothing — this job indexed no video.")
            if skipped_note:
                lines.append(skipped_note)
            lines.append('next: index-video url="…" force_reindex=true to index it now.')
    elif row["state"] == "failed":
        lines.append("Queryable now: nothing from this job.")
        lines.append('next: index-video url="…" force_reindex=true to retry.')
    elif row["state"] == "cancelled":
        lines.append(
            f"Queryable now: the {n_done} video(s) this job finished before it stopped."
            if n_done
            else "Queryable now: nothing — the job was cancelled before any video finished."
        )
        lines.append('next: index-video url="…" force_reindex=true to run it again.')
    else:
        done_so_far = f"the {n_done} video(s) finished so far" if n_done else "nothing"
        lines.append(
            f"Queryable now: {done_so_far} (data is written on stage completion)."
        )
        lines.append(f'next: job-status job_id="{row["public_id"]}" again in ~60s.')

    return text_result(
        "\n".join(lines),
        {
            "job_id": str(row["public_id"]),
            "state": str(row["state"]),
            "progress": float(row["progress"] or 0.0),
            # n_done + n_failed + n_skipped + n_cancelled == n_items once the
            # job is terminal, so "done with nothing indexed" is visible in the
            # numbers and not only in the prose.
            "n_items": int(row["n_items"]),
            "n_done": n_done,
            "n_failed": n_failed,
            "n_skipped": n_skipped,
            "n_cancelled": n_cancelled,
            # Every typed item code, counted. A job can be `done` and still have
            # been rate-limited on the way; the job-level code says the loudest
            # of these, this says all of them.
            "item_errors": item_errors,
            "error_code": row["error_code"],
            "note": row["error_message"] if row["state"] == "done" else None,
        },
    )


def _aside(n_failed: int, n_skipped: int, n_cancelled: int) -> str:
    parts = [
        f"{count} {word}"
        for count, word in (
            (n_failed, "failed"),
            (n_skipped, "skipped"),
            (n_cancelled, "superseded"),
        )
        if count
    ]
    return f" ({', '.join(parts)})" if parts else ""


def _skipped_note(items: Sequence[sqlite3.Row]) -> str | None:
    """Name what was skipped and why, from the rows themselves."""
    reasons = [
        f"  {i['source_url']}: {str(i['error_message'])[:MAX_ERROR_CHARS]}"
        for i in items
        if i["state"] in ("skipped", "cancelled") and i["error_message"]
    ]
    if not reasons:
        return None
    return "skipped:\n" + "\n".join(reasons[:5])


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
