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
from urllib.parse import urlsplit

from ..db import queries

KNOWN_EDITIONS = ("aie-paris-2026",)
MAX_SESSIONS = 100
# The facade's own ceiling (demo-site.md §2.5, aie-paris-2026.md §3.1), applied
# to the whole payload: items and characters are independent caps, and this is
# the second one.
MAX_RESPONSE_CHARS = 60_000

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_STAGES = {"main", "discovery-1", "discovery-2", "workshop"}
_TIMEZONE = "Europe/Paris"
_MAX_TITLE_CHARS = 120
_MAX_SESSION_TITLE_CHARS = 256
_MAX_CATEGORY_CHARS = 120

# The field lists are exact in both directions: a fixture carrying a key these
# do not name fails to load, and the payload is built from these names rather
# than copied from the file, so a key that reaches the fixture anyway cannot
# reach a reader (§2.1, §2.2, §3.2).
_EDITION_KEYS = {
    "schema_version",
    "slug",
    "title",
    "timezone",
    "starts_on",
    "ends_on",
    "source_url",
    "source_captured_on",
    "organizer",
    "streamed_stage",
    "tags",
    "context_bias",
    "sessions",
}
_SESSION_KEYS = {
    "id",
    "day",
    "start",
    "end",
    "stage",
    "title",
    "speakers",
    "category",
    "alignment",
}
_SPEAKER_KEYS = {"name", "company"}
_TAG_KEYS = {"edition", "stream", "talk"}
_ALIGNMENT_FIELDS = ("talk_video_id", "stream_video_id", "start_s", "end_s")
_ALIGNMENT_KEYS = set(_ALIGNMENT_FIELDS)
# What the `edition` object carries, beside `tags`. `sessions` are paged on
# their own and `context_bias` is the worker's input, so neither is here.
_PUBLIC_EDITION_KEYS = (
    "schema_version",
    "slug",
    "title",
    "timezone",
    "starts_on",
    "ends_on",
    "source_url",
    "source_captured_on",
    "organizer",
    "streamed_stage",
)

# The wire tag is `<namespace>:<value>` and the namespace is bounded at 64
# characters by `text.validate_tag`; `series:` plus that is the longest thing a
# fixture may name.
_MAX_TAG_CHARS = len("series:") + 64


class EditionValidationError(ValueError):
    """A committed edition fixture violates its schema."""


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise EditionValidationError(message)


def _text(value: Any, field: str, *, max_chars: int | None = None) -> str:
    _need(isinstance(value, str) and bool(value.strip()), f"{field} must be a non-empty string")
    if max_chars is not None:
        _need(len(value) <= max_chars, f"{field} must be at most {max_chars} characters")
    return value


def _exact_keys(value: Any, allowed: set[str], field: str) -> dict[str, Any]:
    """The documented fields, all of them and nothing else.

    An unknown key fails rather than being ignored: the facade answers from the
    fixture, so a field no review saw is a field a reader could be shown.
    """
    _need(isinstance(value, dict), f"{field} must be an object")
    wrong = set(value) ^ allowed
    _need(not wrong, f"{field} has unknown or missing fields: {', '.join(sorted(wrong))}")
    return value


def validate_edition(raw: Any) -> dict[str, Any]:
    """Every rule in §2.1 and §2.2, applied to one loaded fixture.

    Raises rather than repairing. The file is committed, so a violation is a
    review that let something through, not a caller's mistake to be tolerated.
    """
    edition = _exact_keys(deepcopy(raw), _EDITION_KEYS, "edition")
    _need(edition.get("schema_version") == 1, "schema_version must be 1")
    slug = _text(edition.get("slug"), "slug")
    _need(bool(_SLUG.fullmatch(slug)), "slug is invalid")
    _need(slug in KNOWN_EDITIONS, f"slug {slug} is not an edition this build ships")
    _text(edition.get("title"), "title", max_chars=_MAX_TITLE_CHARS)
    for field in ("timezone", "source_url", "source_captured_on", "organizer", "streamed_stage"):
        _text(edition.get(field), field)
    _need(edition["timezone"] == _TIMEZONE, f"timezone must be {_TIMEZONE}")
    source = urlsplit(edition["source_url"])
    _need(
        source.scheme == "https" and bool(source.netloc),
        "source_url must be an HTTPS URL",
    )
    try:
        starts = date.fromisoformat(_text(edition.get("starts_on"), "starts_on"))
        ends = date.fromisoformat(_text(edition.get("ends_on"), "ends_on"))
        date.fromisoformat(edition["source_captured_on"])
    except ValueError as exc:
        raise EditionValidationError("edition dates must be ISO dates") from exc
    _need(starts <= ends, "starts_on must not follow ends_on")
    _need(edition["streamed_stage"] == "main", "streamed_stage must be main")

    tags = _exact_keys(edition.get("tags"), _TAG_KEYS, "tags")
    for key in sorted(_TAG_KEYS):
        value = _text(tags.get(key), f"tags.{key}")
        _need(
            value.startswith("series:") and len(value) <= _MAX_TAG_CHARS,
            f"tags.{key} is invalid",
        )

    # The worker's term list (§7): validated here because it is committed here,
    # even though nothing in the payload carries it.
    bias = _exact_keys(edition.get("context_bias"), {"fixed"}, "context_bias")
    _need(isinstance(bias["fixed"], list), "context_bias.fixed must be an array")
    for index, term in enumerate(bias["fixed"]):
        _text(term, f"context_bias.fixed[{index}]")

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
    _exact_keys(session, _SESSION_KEYS, prefix)
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
    _text(session.get("title"), f"{prefix}.title", max_chars=_MAX_SESSION_TITLE_CHARS)
    _text(session.get("category"), f"{prefix}.category", max_chars=_MAX_CATEGORY_CHARS)
    speakers = session.get("speakers")
    _need(isinstance(speakers, list) and bool(speakers), f"{prefix}.speakers must not be empty")
    for index, speaker in enumerate(speakers):
        _exact_keys(speaker, _SPEAKER_KEYS, f"{prefix}.speakers[{index}]")
        _text(speaker.get("name"), f"{prefix}.speakers[{index}].name")
        _need(
            isinstance(speaker.get("company"), str),
            f"{prefix}.speakers[{index}].company must be a string",
        )

    alignment = session.get("alignment")
    # Only the streamed stage can have an alignment at all: a track nobody
    # filmed has no video to point at, and a null here is the fixture saying so
    # rather than a field somebody forgot (§2.3).
    if stage != "main":
        _need(alignment is None, f"{prefix}.alignment must be null off the main stage")
        return
    _exact_keys(alignment, _ALIGNMENT_KEYS, f"{prefix}.alignment")
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
    edition = validate_edition(json.loads(data))
    _need(edition["slug"] == slug, f"{slug}.json declares slug {edition['slug']}")
    return edition


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
        "speakers": _speakers(session),
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


def _speakers(session: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"name": speaker["name"], "company": speaker["company"]}
        for speaker in session["speakers"]
    ]


def _session_row(session: dict[str, Any]) -> dict[str, Any]:
    """One schedule record, named field by field (§2.2)."""
    row = {key: session[key] for key in ("id", "day", "start", "end", "stage", "title")}
    row["speakers"] = _speakers(session)
    row["category"] = session["category"]
    alignment = session["alignment"]
    row["alignment"] = (
        None if alignment is None else {key: alignment[key] for key in _ALIGNMENT_FIELDS}
    )
    return row


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
    # Named, not copied: the payload carries the documented fields and only
    # those, so a fixture key nobody put in §3.2 cannot ride out to a reader.
    metadata: dict[str, Any] = {key: edition[key] for key in _PUBLIC_EDITION_KEYS}
    metadata["tags"] = {key: edition["tags"][key] for key in ("edition", "stream", "talk")}
    payload = {
        "edition": metadata,
        "sessions": [_session_row(row) for row in session_page],
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

    Neither page is trimmed to nothing, and that is the whole reason the loops
    stop at one row. `next_offset` is the offset plus what the page kept, so an
    emptied page hands back the offset it was asked for and a client following
    the hint asks for the same trimmed page forever. The first row stays, the
    ceiling is exceeded by that row, and `notes` says so — over the cap once and
    visibly beats a pagination hint that never moves. It is also why the video
    leg keeps a row while the schedule is being trimmed: one row of programme is
    a smaller loss than a hint that cannot advance.

    A payload over the ceiling with no rows at all — the metadata by itself — is
    a committed file this facade cannot serve at any offset, so it becomes the
    `E_INTERNAL` §3.2 documents rather than an oversized response.
    """
    while _size(payload) > MAX_RESPONSE_CHARS and len(payload["videos"]) > 1:
        payload["videos"].pop()
        payload["video_pagination"]["has_more"] = True
        payload["video_pagination"]["next_offset"] = video_offset + len(payload["videos"])
        _once(notes, "video page shortened by the response character cap")
    while _size(payload) > MAX_RESPONSE_CHARS and len(payload["sessions"]) > 1:
        removed = payload["sessions"].pop()
        payload["talks"] = [
            talk for talk in payload["talks"] if talk["session_id"] != removed["id"]
        ]
        payload["pagination"]["has_more"] = True
        payload["pagination"]["next_offset"] = offset + len(payload["sessions"])
        _once(notes, "session page shortened by the response character cap")
    if _size(payload) > MAX_RESPONSE_CHARS:
        _need(
            _size(payload | {"sessions": [], "talks": [], "videos": []}) <= MAX_RESPONSE_CHARS,
            f"edition metadata exceeds {MAX_RESPONSE_CHARS} characters with every session "
            "and video dropped",
        )
        _once(
            notes,
            f"one row kept over the {MAX_RESPONSE_CHARS}-character cap "
            "so the next offset advances",
        )


def _size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _once(notes: list[str], line: str) -> None:
    if line not in notes:
        notes.append(line)
