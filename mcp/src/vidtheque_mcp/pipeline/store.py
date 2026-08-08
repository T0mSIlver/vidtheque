"""Every write the pipeline makes, as plain functions over a connection.

They run inside ``Database.write``, which is one ``BEGIN IMMEDIATE`` per call —
so each function here is a transaction boundary, and each is written to be one:
``replace_cues`` deletes and reinserts a whole video's cues in a single pass
because the id-contiguity invariant (index-schema §1.4) is only true if it does.

Nothing here is async, nothing here opens a file, nothing here calls the worker.
That separation is what lets the stage code read as a list of steps.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Sequence

from ..db.queries import pack_f32
from .captions import CueDraft
from .chunking import ChunkDraft
from .keyframes import KeyframeDraft
from .sources import VideoMeta
from .worker_client import OcrLine

STAGES = ("fetch", "stt", "chunk", "text_embed", "keyframe", "ocr", "frame_embed")

# Which `config` key each stage's `model_key` is compared against by the reindex
# planner in index-schema §1.3. `fetch`, `chunk` and `keyframe` have no model,
# so they record the tool version / parameters that produced them instead.
STAGE_CONFIG_KEY = {
    "stt": "stt.model",
    "text_embed": "text_embed.model",
    "ocr": "ocr.model",
    "frame_embed": "frame_embed.model",
}


# ------------------------------------------------------------------- videos


def upsert_video(conn: sqlite3.Connection, meta: VideoMeta) -> int:
    """Insert or refresh the `videos` row, and return its id.

    ``ON CONFLICT (source, source_id)`` rather than a lookup-then-branch: the
    unique key is the identity, and a reindex must not create a second row for
    a video whose title changed.
    """
    row = conn.execute(
        """
        INSERT INTO videos (owner_id, source, source_id, url, title, description,
                            channel_id, channel_name, published_at, duration_s,
                            language, chapters_json, heatmap_json, index_state, updated_at)
        VALUES (1, :source, :source_id, :url, :title, :description, :channel_id,
                :channel_name, :published_at, :duration_s, :language, :chapters_json,
                :heatmap_json, 'indexing', unixepoch())
        ON CONFLICT (source, source_id) DO UPDATE SET
            url = excluded.url,
            title = excluded.title,
            description = excluded.description,
            channel_id = excluded.channel_id,
            channel_name = excluded.channel_name,
            published_at = excluded.published_at,
            duration_s = excluded.duration_s,
            language = COALESCE(excluded.language, videos.language),
            chapters_json = excluded.chapters_json,
            heatmap_json = excluded.heatmap_json,
            index_state = 'indexing',
            updated_at = unixepoch()
        RETURNING id
        """,
        {
            "source": meta.source,
            "source_id": meta.source_id,
            "url": meta.url,
            "title": meta.title,
            "description": meta.description,
            "channel_id": meta.channel_id,
            "channel_name": meta.channel_name,
            "published_at": meta.published_at,
            "duration_s": float(meta.duration_s),
            "language": meta.language,
            "chapters_json": meta.chapters_json,
            "heatmap_json": meta.heatmap_json,
        },
    ).fetchone()
    return int(row["id"])


def replace_chapters(conn: sqlite3.Connection, video_id: int, meta: VideoMeta) -> None:
    conn.execute("DELETE FROM chapters WHERE video_id = ?", (video_id,))
    conn.executemany(
        "INSERT INTO chapters (video_id, seq, start_s, end_s, title) VALUES (?, ?, ?, ?, ?)",
        [(video_id, c.seq, c.start_s, c.end_s, c.title) for c in meta.chapters],
    )
    conn.execute("DELETE FROM video_links WHERE video_id = ?", (video_id,))
    conn.executemany(
        "INSERT INTO video_links (video_id, seq, t_s, url, title) VALUES (?, ?, ?, ?, ?)",
        [(video_id, link.seq, link.t_s, link.url, link.title) for link in meta.links],
    )


def set_media_paths(
    conn: sqlite3.Connection, video_id: int, *, audio: str | None = None, media: str | None = None
) -> None:
    conn.execute(
        "UPDATE videos SET audio_path = COALESCE(?, audio_path), "
        "media_path = ?, updated_at = unixepoch() WHERE id = ?",
        (audio, media, video_id),
    )


def clear_media_path(conn: sqlite3.Connection, video_id: int, *, audio: bool = False) -> None:
    if audio:
        conn.execute("UPDATE videos SET audio_path = NULL WHERE id = ?", (video_id,))
    else:
        conn.execute("UPDATE videos SET media_path = NULL WHERE id = ?", (video_id,))


def mark_ready(conn: sqlite3.Connection, video_id: int) -> None:
    conn.execute(
        "UPDATE videos SET index_state = 'ready', indexed_at = unixepoch(), "
        "updated_at = unixepoch() WHERE id = ?",
        (video_id,),
    )


def mark_failed(conn: sqlite3.Connection, video_id: int) -> None:
    conn.execute(
        "UPDATE videos SET index_state = 'failed', updated_at = unixepoch() WHERE id = ?",
        (video_id,),
    )


# -------------------------------------------------------------------- stages


def stage_map(conn: sqlite3.Connection, video_id: int) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        "SELECT stage, state, model_key, stage_version FROM video_stages WHERE video_id = ?",
        (video_id,),
    ).fetchall()
    return {str(row["stage"]): row for row in rows}


def stage_running(conn: sqlite3.Connection, video_id: int, stage: str) -> None:
    conn.execute(
        """
        INSERT INTO video_stages (video_id, stage, state, started_at, error)
        VALUES (?, ?, 'running', unixepoch(), NULL)
        ON CONFLICT (video_id, stage) DO UPDATE SET
            state = 'running', started_at = unixepoch(), finished_at = NULL, error = NULL
        """,
        (video_id, stage),
    )


def stage_finished(
    conn: sqlite3.Connection,
    video_id: int,
    stage: str,
    state: str,
    model_key: str | None = None,
    error: str | None = None,
) -> None:
    """`model_key` records the model/parameters in force when the stage ran.

    That is the whole mechanism behind incremental reindex: swapping the STT
    model re-runs stt, chunk and text_embed and leaves 40,000 keyframes and
    their OCR alone.
    """
    conn.execute(
        """
        INSERT INTO video_stages (video_id, stage, state, model_key, started_at,
                                  finished_at, error)
        VALUES (?, ?, ?, ?, unixepoch(), unixepoch(), ?)
        ON CONFLICT (video_id, stage) DO UPDATE SET
            state = excluded.state,
            model_key = excluded.model_key,
            finished_at = unixepoch(),
            error = excluded.error
        """,
        (video_id, stage, state, model_key, error[:500] if error else None),
    )


# ---------------------------------------------------------------------- cues


def replace_cues(
    conn: sqlite3.Connection,
    video_id: int,
    cues: Sequence[CueDraft],
    origin: str,
    keep_word_timings: bool = True,
) -> list[int]:
    """Delete and reinsert in one pass, in time order.

    Within a video: id order == seq order == time order, so a time-contiguous
    run of cues is an id-contiguous range. That is what lets `search` print
    `cues 1841-1849` instead of a list, and what makes the chunk span exact.
    Reindexing reallocates the block; ids are stable *between* reindexes only.
    """
    conn.execute("DELETE FROM cues WHERE video_id = ?", (video_id,))
    ids: list[int] = []
    for seq, cue in enumerate(cues):
        cursor = conn.execute(
            "INSERT INTO cues (video_id, seq, start_s, end_s, text, origin, avg_logprob, "
            "words_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                video_id,
                seq,
                float(cue.start_s),
                float(cue.end_s),
                cue.text,
                origin,
                cue.avg_logprob,
                cue.words_json() if keep_word_timings else None,
            ),
        )
        ids.append(int(cursor.lastrowid or 0))
    return ids


def replace_chunks(
    conn: sqlite3.Connection, video_id: int, chunks: Sequence[ChunkDraft], cue_ids: Sequence[int]
) -> list[int]:
    """Chunks first, vectors second — `chunks_ad` cascades vec_chunks on delete."""
    conn.execute("DELETE FROM chunks WHERE video_id = ?", (video_id,))
    ids: list[int] = []
    for chunk in chunks:
        cursor = conn.execute(
            "INSERT INTO chunks (video_id, seq, start_s, end_s, first_cue_id, last_cue_id, "
            "text, n_chars) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                video_id,
                chunk.seq,
                float(chunk.start_s),
                float(chunk.end_s),
                cue_ids[chunk.first_index],
                cue_ids[chunk.last_index],
                chunk.text,
                chunk.n_chars,
            ),
        )
        ids.append(int(cursor.lastrowid or 0))
    return ids


def pending_chunks(conn: sqlite3.Connection, video_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, start_s, text FROM chunks WHERE video_id = ? ORDER BY seq", (video_id,)
    ).fetchall()


def write_chunk_vectors(
    conn: sqlite3.Connection, video_id: int, rows: Sequence[tuple[int, float, Sequence[float]]]
) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO vec_chunks (chunk_id, video_id, start_s, embedding) "
        "VALUES (?, ?, ?, ?)",
        [
            (chunk_id, video_id, float(start_s), pack_f32(vector))
            for chunk_id, start_s, vector in rows
        ],
    )


# ----------------------------------------------------------------- keyframes


def replace_keyframes(
    conn: sqlite3.Connection, video_id: int, drafts: Sequence[KeyframeDraft]
) -> dict[int, int]:
    """Insert the frames, then resolve `dup_of` — it is a self-reference."""
    conn.execute("DELETE FROM keyframes WHERE video_id = ?", (video_id,))
    by_ordinal: dict[int, int] = {}
    for draft in drafts:
        cursor = conn.execute(
            "INSERT INTO keyframes (video_id, ord, t_s, shot_id, shot_start_s, shot_end_s, "
            "phash, sharpness, width, height, jpeg_path, jpeg_bytes, ocr_state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
            (
                video_id,
                draft.ordinal,
                float(draft.t_s),
                draft.shot.index,
                float(draft.shot.start_s),
                float(draft.shot.end_s),
                int(draft.phash),
                float(draft.sharpness),
                int(draft.width),
                int(draft.height),
                draft.relpath,
                int(draft.jpeg_bytes),
            ),
        )
        by_ordinal[draft.ordinal] = int(cursor.lastrowid or 0)
    for draft in drafts:
        if draft.dup_of is None or draft.dup_of not in by_ordinal:
            continue
        conn.execute(
            "UPDATE keyframes SET dup_of = ?, ocr_state = 'skipped' WHERE id = ?",
            (by_ordinal[draft.dup_of], by_ordinal[draft.ordinal]),
        )
    return by_ordinal


def live_keyframes(
    conn: sqlite3.Connection, video_id: int, states: Sequence[str] = ("pending",)
) -> list[sqlite3.Row]:
    """Frames worth spending a worker call on: distinct visuals only."""
    placeholders = ",".join("?" for _ in states)
    return conn.execute(
        f"SELECT id, ord, t_s, jpeg_path, width, height FROM keyframes "
        f"WHERE video_id = ? AND dup_of IS NULL AND ocr_state IN ({placeholders}) ORDER BY ord",
        (video_id, *states),
    ).fetchall()


def all_live_keyframes(conn: sqlite3.Connection, video_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, ord, t_s, jpeg_path, width, height FROM keyframes "
        "WHERE video_id = ? AND dup_of IS NULL ORDER BY ord",
        (video_id,),
    ).fetchall()


def write_ocr(
    conn: sqlite3.Connection,
    video_id: int,
    keyframe_id: int,
    t_s: float,
    lines: Sequence[OcrLine],
    width: int,
    height: int,
) -> int:
    """One frame's OCR. `video_id`/`t_s` are denormalized on purpose (§1.7).

    Coordinates are normalized 0-1 here rather than at query time: the worker
    answers in source pixels, and the consumers of this table (layout
    reasoning, drawing a box on a thumbnail) all want the fraction. Stored
    normalized, the row survives a re-encode at another resolution.
    """
    conn.execute("DELETE FROM ocr_lines WHERE keyframe_id = ?", (keyframe_id,))
    written = 0
    span_x = float(width or 1)
    span_y = float(height or 1)
    for line_no, line in enumerate(lines):
        text = line.text.strip()
        if not text:
            continue
        box = line.bbox or (0.0, 0.0, span_x, span_y)
        conn.execute(
            "INSERT INTO ocr_lines (keyframe_id, video_id, t_s, line_no, text, conf, "
            "x0, y0, x1, y1) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                keyframe_id,
                video_id,
                float(t_s),
                written,
                text,
                line.confidence,
                _clamp01(box[0] / span_x),
                _clamp01(box[1] / span_y),
                _clamp01(box[2] / span_x),
                _clamp01(box[3] / span_y),
            ),
        )
        written += 1
    conn.execute(
        "UPDATE keyframes SET ocr_state = ? WHERE id = ?",
        ("done" if written else "empty", keyframe_id),
    )
    return written


def set_ocr_state(conn: sqlite3.Connection, keyframe_ids: Sequence[int], state: str) -> None:
    conn.executemany(
        "UPDATE keyframes SET ocr_state = ? WHERE id = ?", [(state, kid) for kid in keyframe_ids]
    )


def write_frame_vectors(
    conn: sqlite3.Connection, video_id: int, rows: Sequence[tuple[int, float, Sequence[float]]]
) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO vec_frames (keyframe_id, video_id, t_s, embedding) "
        "VALUES (?, ?, ?, ?)",
        [(kf_id, video_id, float(t_s), pack_f32(vector)) for kf_id, t_s, vector in rows],
    )


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else float(value))


# ---------------------------------------------------------------------- jobs


@dataclass(frozen=True)
class Claim:
    """The outcome of pointing an item at its video.

    ``ok`` is the common case. A refusal is *not* one thing: a duplicate inside
    one expansion (`same_job`) is bookkeeping the caller skips, while another
    job holding the claim is a collision the caller must report — reading the
    second as the first is how an `index-video` that fetched nothing reported
    `done`.
    """

    ok: bool
    same_job: bool = False
    job_public_id: str | None = None


def attach_video(conn: sqlite3.Connection, item_id: int, video_id: int) -> Claim:
    """Point the job item at the video row it resolved to.

    Can legitimately fail: `job_items_one_inflight` is a partial unique index,
    so a second queued item for the same video is refused at this moment rather
    than at insert.
    """
    try:
        conn.execute("UPDATE job_items SET video_id = ? WHERE id = ?", (video_id, item_id))
    except sqlite3.IntegrityError:
        holder = conn.execute(
            "SELECT i.job_id, j.public_id, (i.job_id = (SELECT job_id FROM job_items "
            "WHERE id = ?)) AS same_job FROM job_items i JOIN jobs j ON j.id = i.job_id "
            "WHERE i.video_id = ? AND i.state IN ('queued','running') AND i.id <> ? LIMIT 1",
            (item_id, video_id, item_id),
        ).fetchone()
        if holder is None:  # pragma: no cover - the clash vanished under us
            return Claim(ok=False)
        return Claim(
            ok=False,
            same_job=bool(holder["same_job"]),
            job_public_id=str(holder["public_id"]),
        )
    return Claim(ok=True)


def append_items(conn: sqlite3.Connection, job_id: int, urls: Sequence[str]) -> int:
    """Fan-out: playlist/channel entries become items of the *same* job.

    One `index-video` call covering 200 playlist entries stays one handle the
    model polls, which is the entire point of the jobs/job_items split.
    """
    if not urls:
        return 0
    start = int(
        conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM job_items WHERE job_id = ?", (job_id,)
        ).fetchone()[0]
    )
    added = 0
    for offset, url in enumerate(urls):
        conn.execute(
            "INSERT INTO job_items (job_id, seq, source_url, video_id) VALUES (?, ?, ?, NULL)",
            (job_id, start + offset, url),
        )
        added += 1
    conn.execute("UPDATE jobs SET n_items = n_items + ? WHERE id = ?", (added, job_id))
    return added


def job_args(conn: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT args_json, kind FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:  # pragma: no cover - the row was claimed a moment ago
        return {}
    try:
        args = json.loads(row["args_json"] or "{}")
    except json.JSONDecodeError:  # pragma: no cover - defensive
        args = {}
    args.setdefault("kind", row["kind"])
    return args


def apply_tags(conn: sqlite3.Connection, video_id: int, tags: Sequence[str]) -> None:
    for tag in tags:
        if ":" not in tag:
            continue
        ns, name = tag.split(":", 1)
        conn.execute("INSERT OR IGNORE INTO tags (owner_id, ns, name) VALUES (1, ?, ?)", (ns, name))
        conn.execute(
            "INSERT OR IGNORE INTO video_tags (video_id, tag_id, origin) "
            "SELECT ?, id, 'import' FROM tags WHERE ns = ? AND name = ?",
            (video_id, ns, name),
        )


def existing_video(conn: sqlite3.Connection, source: str, source_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, index_state FROM videos WHERE source = ? AND source_id = ?",
        (source, source_id),
    ).fetchone()
