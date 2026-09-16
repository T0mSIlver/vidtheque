"""The committed edition fixtures and the read model the facade serves from them
(aie-paris-2026.md §2, §3).

An edition is a schedule somebody typed off a conference page, plus a mapping
from its main-stage rows onto videos this corpus has actually indexed. The
schedule is data the repository owns — a committed JSON file loaded with
``importlib.resources`` — and the mapping is whatever the SQLite tables say
*now*, which is why the two are joined per request and the answer is never
cached.

Three rules make this module worth its own package rather than a handler in
``public/api.py``:

* **The fixture is validated, all of it, before anything renders.** A missing
  field or a duplicate id fails at boot (``validate_editions`` runs in
  ``assemble``) and fails the read, rather than producing a schedule with a hole
  in it. A public page that half-renders a conference programme is worse than
  one that says it is broken.
* **A video is only usable when its own tags say so.** The edition tag alone is
  not enough to call a video a talk upload or a day stream; the subtype tag is
  what an operator applies deliberately (§5), so a missed classification shows
  up as an unaligned row and a note, never as the wrong video under a speaker's
  name.
* **Offsets are never invented.** ``start_s``/``end_s`` come from the committed
  mapping or from nowhere. The three alignment states are the honest answers to
  "can a visitor click this yet", and two of them have no timestamp at all.
"""

from __future__ import annotations

import json
import re
import sqlite3
from copy import deepcopy
from datetime import date
from functools import lru_cache
from importlib import resources
from typing import Any

from ..db import queries

KNOWN_EDITIONS = ("aie-paris-2026",)
MAX_SESSIONS = 100
# The facade's own ceiling (demo-site.md §2.2), applied to the whole payload:
# items and characters are independent caps, and this is the second one.
MAX_RESPONSE_CHARS = 60_000

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_STAGES = {"main", "discovery-1", "discovery-2", "workshop"}

# The wire tag is `<namespace>:<value>` and the namespace is bounded at 64
# characters by `text.validate_tag`; `series:` plus that is the longest thing a
# fixture may name.
_MAX_TAG_CHARS = len("series:") + 64


class EditionValidationError(ValueError):
    """A committed edition fixture violates its schema."""


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise EditionValidationError(message)


def _text(value: Any, field: str) -> str:
    _need(isinstance(value, str) and bool(value.strip()), f"{field} must be a non-empty string")
    return value


def validate_edition(raw: Any) -> dict[str, Any]:
    """Every rule in §2.1 and §2.2, applied to one loaded fixture.

    Raises rather than repairing. The file is committed, so a violation is a
    review that let something through, not a caller's mistake to be tolerated.
    """
    _need(isinstance(raw, dict), "edition must be an object")
    edition = deepcopy(raw)
    _need(edition.get("schema_version") == 1, "schema_version must be 1")
    slug = _text(edition.get("slug"), "slug")
    _need(bool(_SLUG.fullmatch(slug)), "slug is invalid")
    for field in (
        "title",
        "timezone",
        "source_url",
        "source_captured_on",
        "organizer",
        "streamed_stage",
    ):
        _text(edition.get(field), field)
    try:
        starts = date.fromisoformat(_text(edition.get("starts_on"), "starts_on"))
        ends = date.fromisoformat(_text(edition.get("ends_on"), "ends_on"))
        date.fromisoformat(edition["source_captured_on"])
    except ValueError as exc:
        raise EditionValidationError("edition dates must be ISO dates") from exc
    _need(starts <= ends, "starts_on must not follow ends_on")
    _need(edition["streamed_stage"] == "main", "streamed_stage must be main")

    tags = edition.get("tags")
    _need(isinstance(tags, dict), "tags must be an object")
    for key in ("edition", "stream", "talk"):
        value = _text(tags.get(key), f"tags.{key}")
        _need(
            value.startswith("series:") and len(value) <= _MAX_TAG_CHARS,
            f"tags.{key} is invalid",
        )

    sessions = edition.get("sessions")
    _need(
        isinstance(sessions, list) and len(sessions) <= MAX_SESSIONS,
        f"sessions must contain at most {MAX_SESSIONS} rows",
    )
    seen: set[str] = set()
    for index, session in enumerate(sessions):
        _validate_session(session, f"sessions[{index}]", starts, ends, seen)
    return edition


def _validate_session(
    session: Any, prefix: str, starts: date, ends: date, seen: set[str]
) -> None:
    _need(isinstance(session, dict), f"{prefix} must be an object")
    session_id = _text(session.get("id"), f"{prefix}.id")
    _need(session_id not in seen, f"duplicate session id {session_id}")
    seen.add(session_id)
    try:
        day = date.fromisoformat(_text(session.get("day"), f"{prefix}.day"))
    except ValueError as exc:
        raise EditionValidationError(f"{prefix}.day must be an ISO date") from exc
    _need(starts <= day <= ends, f"{prefix}.day is outside the edition")
    # Schedule time, in the edition's own timezone — never a VOD offset. The two
    # axes stay apart here as they do everywhere else (CLAUDE.md).
    start = _text(session.get("start"), f"{prefix}.start")
    end = _text(session.get("end"), f"{prefix}.end")
    _need(
        bool(_TIME.fullmatch(start)) and bool(_TIME.fullmatch(end)) and start < end,
        f"{prefix} has invalid times",
    )
    stage = _text(session.get("stage"), f"{prefix}.stage")
    _need(stage in _STAGES, f"{prefix}.stage is invalid")
    _text(session.get("title"), f"{prefix}.title")
    _text(session.get("category"), f"{prefix}.category")
    speakers = session.get("speakers")
    _need(isinstance(speakers, list) and bool(speakers), f"{prefix}.speakers must not be empty")
    for speaker in speakers:
        _need(isinstance(speaker, dict), f"{prefix}.speakers entries must be objects")
        _text(speaker.get("name"), f"{prefix}.speakers.name")
        _need(
            isinstance(speaker.get("company"), str),
            f"{prefix}.speakers.company must be a string",
        )

    alignment = session.get("alignment")
    # Only the streamed stage can have an alignment at all: a track nobody
    # filmed has no video to point at, and a null here is the fixture saying so
    # rather than a field somebody forgot (§2.3).
    if stage != "main":
        _need(alignment is None, f"{prefix}.alignment must be null off the main stage")
        return
    _need(isinstance(alignment, dict), f"{prefix}.alignment must be an object")
    _need(
        set(alignment) == {"talk_video_id", "stream_video_id", "start_s", "end_s"},
        f"{prefix}.alignment has the wrong fields",
    )
    for key in ("talk_video_id", "stream_video_id"):
        _need(
            alignment[key] is None or isinstance(alignment[key], str),
            f"{prefix}.alignment.{key} is invalid",
        )
    for key in ("start_s", "end_s"):
        _need(
            alignment[key] is None or isinstance(alignment[key], (int, float)),
            f"{prefix}.alignment.{key} is invalid",
        )


@lru_cache(maxsize=len(KNOWN_EDITIONS))
def load_edition(slug: str) -> dict[str, Any] | None:
    """One validated fixture, or `None` for a slug this build does not ship.

    Cached: the file is packaged with the wheel and cannot change under a
    running process, and validating 34 sessions on every page view would be
    work done to reach the same answer.
    """
    if slug not in KNOWN_EDITIONS or not _SLUG.fullmatch(slug):
        return None
    data = resources.files(__package__).joinpath(f"{slug}.json").read_text(encoding="utf-8")
    return validate_edition(json.loads(data))


def validate_editions() -> None:
    """Load every shipped fixture at boot, so a bad one fails there (§2)."""
    for slug in KNOWN_EDITIONS:
        if load_edition(slug) is None:  # pragma: no cover - known tuple is static
            raise EditionValidationError(f"missing edition {slug}")


def _videos(
    conn: sqlite3.Connection, edition: dict[str, Any], limit: int, offset: int
) -> tuple[list[dict[str, Any]], bool, dict[str, dict[str, Any]]]:
    """The tagged page, its `has_more`, and the videos the mapping names.

    Two reads, because they answer two questions. The first is "what does this
    corpus hold for the edition", which is paged. The second is "are the videos
    the fixture points at queryable", which is not: a row's state must not
    depend on which page of videos a caller happened to ask for.
    """
    tag = edition["tags"]["edition"]
    rows = conn.execute(
        """
        SELECT v.id, v.public_id, v.title, v.channel_name, v.duration_s, v.index_state
        FROM videos v JOIN video_tags vt ON vt.video_id = v.id JOIN tags t ON t.id = vt.tag_id
        WHERE t.full = ? AND v.index_state IN ('ready', 'stale')
        ORDER BY v.published_at DESC, v.id DESC LIMIT ? OFFSET ?
        """,
        (tag, limit + 1, offset),
    ).fetchall()
    page = rows[:limit]
    tag_map = queries.video_tags(conn, [int(row["id"]) for row in page])
    videos = []
    for row in page:
        tags = tag_map.get(int(row["id"]), [])
        kind = (
            "talk"
            if edition["tags"]["talk"] in tags
            else "stream"
            if edition["tags"]["stream"] in tags
            else "other"
        )
        videos.append(
            {
                "video_id": row["public_id"],
                "title": row["title"],
                "channel": row["channel_name"] or "",
                "duration": float(row["duration_s"]),
                "tags": tags,
                "index_state": row["index_state"],
                "kind": kind,
                "link": f"https://youtu.be/{row['public_id']}",
            }
        )

    referenced = {
        value
        for session in edition["sessions"]
        if session["stage"] == "main"
        for value in (
            session["alignment"]["talk_video_id"],
            session["alignment"]["stream_video_id"],
        )
        if value
    }
    lookup: dict[str, dict[str, Any]] = {}
    if referenced:
        found = conn.execute(
            "SELECT id, public_id, title, channel_name, duration_s, index_state FROM videos "
            "WHERE public_id IN (SELECT value FROM json_each(?)) "
            "AND index_state IN ('ready','stale')",
            (json.dumps(sorted(referenced)),),
        ).fetchall()
        found_tags = queries.video_tags(conn, [int(row["id"]) for row in found])
        lookup = {
            str(row["public_id"]): {**dict(row), "tags": found_tags.get(int(row["id"]), [])}
            for row in found
        }
    return videos, len(rows) > limit, lookup


def _talk(
    session: dict[str, Any],
    edition: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
    notes: list[str],
) -> dict[str, Any]:
    """One main-stage row, in whichever of the three states it is actually in."""
    alignment = session["alignment"]
    talk_id, stream_id = alignment["talk_video_id"], alignment["stream_video_id"]
    talk = lookup.get(talk_id) if talk_id else None
    stream = lookup.get(stream_id) if stream_id else None
    # Both tags, or the video does not count: the edition tag says which event,
    # the subtype tag says which *kind of upload*, and a heuristic on the title
    # is exactly what the explicit mapping exists to avoid (§2.3).
    talk_ok = bool(
        talk
        and edition["tags"]["edition"] in talk["tags"]
        and edition["tags"]["talk"] in talk["tags"]
    )
    stream_ok = bool(
        stream
        and edition["tags"]["edition"] in stream["tags"]
        and edition["tags"]["stream"] in stream["tags"]
    )
    # A per-talk upload wins when both are usable: it is the same content with
    # eight hours of unrelated context removed.
    selected = talk if talk_ok else stream if stream_ok else None
    source_kind = "talk" if talk_ok else "stream" if stream_ok else None
    # A talk upload *is* the talk, so its span is the whole video; a day VOD
    # carries the committed offsets and nothing else.
    start: float | None = 0.0 if talk_ok else alignment["start_s"] if stream_ok else None
    end: float | None = (
        float(talk["duration_s"]) if talk_ok else alignment["end_s"] if stream_ok else None
    )
    aligned = bool(
        selected
        and start is not None
        and end is not None
        and 0 <= float(start) < float(end) <= float(selected["duration_s"])
    )
    state = (
        "aligned"
        if aligned
        else "indexed_not_aligned"
        if (talk_ok or stream_ok)
        else "not_yet_indexed"
    )
    if selected and not aligned:
        notes.append(f"session {session['id']} has an incomplete or invalid alignment")
    video_id = str(selected["public_id"]) if aligned and selected else None
    return {
        "session_id": session["id"],
        "day": session["day"],
        "scheduled_start": session["start"],
        "scheduled_end": session["end"],
        "title": session["title"],
        "speakers": session["speakers"],
        "category": session["category"],
        "alignment_state": state,
        "video_id": video_id,
        "source_kind": source_kind if aligned else None,
        "start_s": float(start) if aligned else None,
        "end_s": float(end) if aligned else None,
        "source": (
            f"https://youtu.be/{video_id}?t={int(float(start))}" if aligned and video_id else None
        ),
    }


def build_payload(
    conn: sqlite3.Connection,
    edition: dict[str, Any],
    *,
    limit: int,
    offset: int,
    video_limit: int,
    video_offset: int,
) -> dict[str, Any]:
    """The §3.2 payload: the schedule page, its talk rows, and the tagged videos."""
    sessions = edition["sessions"][offset : offset + limit + 1]
    session_page = sessions[:limit]
    videos, video_more, lookup = _videos(conn, edition, video_limit, video_offset)
    notes: list[str] = []
    # A video carrying the edition tag and neither subtype tag is a
    # classification an operator has not made yet (§5). It is named rather than
    # dropped, because silence here reads as "nothing arrived".
    unclassified = [video["video_id"] for video in videos if video["kind"] == "other"]
    if unclassified:
        notes.append("tagged but not classified as stream or talk: " + ", ".join(unclassified))
    talks = [_talk(row, edition, lookup, notes) for row in session_page if row["stage"] == "main"]
    # `context_bias` is the worker's input, not the page's: it stays in the
    # fixture and out of the payload.
    metadata = {
        key: value for key, value in edition.items() if key not in {"sessions", "context_bias"}
    }
    payload = {
        "edition": metadata,
        "sessions": session_page,
        "pagination": _page(limit, offset, len(sessions) > limit),
        "talks": talks,
        "videos": videos,
        "video_pagination": _page(video_limit, video_offset, video_more),
        "notes": notes,
    }
    _fit(payload, offset, video_offset, notes)
    return payload


def _page(limit: int, offset: int, has_more: bool) -> dict[str, Any]:
    return {
        "limit": limit,
        "offset": offset,
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
    }


def _fit(payload: dict[str, Any], offset: int, video_offset: int, notes: list[str]) -> None:
    """The character cap, applied after the item caps and independently of them.

    Videos go first: the schedule is what the page is *for*, and a reader who
    loses the tail of the corpus listing still has the programme. Whatever is
    dropped says so in `has_more` and `next_offset`, so a second request
    resumes exactly where this one stopped.
    """
    while _size(payload) > MAX_RESPONSE_CHARS and payload["videos"]:
        payload["videos"].pop()
        payload["video_pagination"]["has_more"] = True
        payload["video_pagination"]["next_offset"] = video_offset + len(payload["videos"])
        _once(notes, "video page shortened by the response character cap")
    while _size(payload) > MAX_RESPONSE_CHARS and payload["sessions"]:
        removed = payload["sessions"].pop()
        payload["talks"] = [
            talk for talk in payload["talks"] if talk["session_id"] != removed["id"]
        ]
        payload["pagination"]["has_more"] = True
        payload["pagination"]["next_offset"] = offset + len(payload["sessions"])
        _once(notes, "session page shortened by the response character cap")


def _size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _once(notes: list[str], line: str) -> None:
    if line not in notes:
        notes.append(line)
