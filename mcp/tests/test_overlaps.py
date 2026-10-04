"""Skip what you have seen, and moment collections — companion.md §3.2, §6.2 (#171).

Chunks get vectors by label: one label, one vector, so two chunks "say the
same thing" exactly when a test gives them the same label.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vidtheque_mcp.app import Assembled
from vidtheque_mcp.dashboard import ROOT
from vidtheque_mcp.db.connection import open_write_connection
from vidtheque_mcp.db.queries import pack_f32
from vidtheque_mcp.verdicts import store
from vidtheque_mcp.verdicts.novelty import Span

from .test_dashboard_following import BEARER, _corpus, owner_client
from .test_verdicts import FakeModel, _seen, run_verdict, verdict, video_id

API = f"{ROOT}/api"


def _vec(label: str) -> bytes:
    rng = random.Random(label)
    raw = [rng.gauss(0, 1) for _ in range(2048)]
    norm = math.sqrt(sum(v * v for v in raw))
    return pack_f32([v / norm for v in raw])


def _chunk_video(conn: sqlite3.Connection, vid: int, labels: list[str]) -> None:
    """Replace the video's chunks with one per label, 45 s long every 30 s."""
    cue = conn.execute("SELECT MIN(id) FROM cues WHERE video_id = ?", (vid,)).fetchone()[0]
    conn.execute("DELETE FROM chunks WHERE video_id = ?", (vid,))
    for seq, label in enumerate(labels):
        chunk = conn.execute(
            "INSERT INTO chunks (video_id, seq, start_s, end_s, first_cue_id, last_cue_id,"
            " text, n_chars) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (vid, seq, seq * 30.0, seq * 30.0 + 45.0, cue, cue, label),
        ).lastrowid
        conn.execute(
            "INSERT INTO vec_chunks (chunk_id, video_id, start_s, embedding) VALUES (?, ?, ?, ?)",
            (chunk, vid, seq * 30.0, _vec(label)),
        )


# ------------------------------------------------------------- the stage


async def test_a_verdict_stores_the_stretches_it_repeats_from_a_seen_video(
    assembled: Assembled,
) -> None:
    db = assembled.db
    vid = await video_id(db, "kCc8FmEb1nY")
    seen = await video_id(db, "zduSFxRajkE")

    def chunks(c: sqlite3.Connection) -> None:
        # Chunks 2–4 repeat the seen video's 1–3 (one unmatched in the middle
        # still joins); chunk 7 alone is one passage, not a stretch.
        _chunk_video(c, seen, ["s0", "s1", "s2", "s3", "s4"])
        _chunk_video(c, vid, ["a0", "a1", "s1", "x", "s3", "a5", "a6", "s4", "a8"])

    await db.write(chunks)
    await _seen(db, seen, "open", 2)
    await run_verdict(assembled, vid, FakeModel(verdict()))

    row = await db.read(lambda c: store.get(c, vid))
    assert store.overlaps_of(row) == [Span(seen, 60.0, 165.0, 30.0)]


async def test_an_unseen_video_repeats_nothing(assembled: Assembled) -> None:
    db = assembled.db
    vid = await video_id(db, "kCc8FmEb1nY")
    other = await video_id(db, "zduSFxRajkE")
    await db.write(lambda c: (_chunk_video(c, other, ["s0", "s1"]), _chunk_video(c, vid, ["s0", "s1"])))
    await run_verdict(assembled, vid, FakeModel(verdict()))
    row = await db.read(lambda c: store.get(c, vid))
    assert store.overlaps_of(row) == []


@pytest.mark.parametrize(
    ("offset", "end", "start", "whole"),
    [
        # A repeat covering the start moves the link to its end, across a
        # second repeat that begins where the first ends.
        (100.0, 400.0, 300.0, False),
        # A repeat starting 10 s into the moment still counts as its start.
        (50.0, 400.0, 300.0, False),
        # Repeated all through: the link stays at the moment's start.
        (110.0, 250.0, 110.0, True),
        # A repeat in the middle leaves the start alone.
        (10.0, 400.0, 10.0, False),
    ],
)
def test_a_moment_link_starts_after_the_repeat_that_covers_its_start(
    offset: float, end: float, start: float, whole: bool
) -> None:
    spans = [Span(7, 60.0, 200.0, 0.0), Span(8, 200.0, 300.0, 0.0)]
    got, touching, all_of_it = store.start_after(offset, end, spans)
    assert (got, all_of_it) == (start, whole)
    assert touching


# ---------------------------------------------------------- the endpoints


def _verdict(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    moments: list[tuple[float, float, str]],
    matches: list[dict] = (),
    overlaps: list[Span] = (),
    score: int = 2,
    published_days_ago: int = 1,
) -> int:
    vid = conn.execute("SELECT id FROM videos WHERE source_id = ?", (source_id,)).fetchone()[0]

    def cue(start: float, end: float) -> int:
        """A cue at these times, so the moment passes its receipt check."""
        seq = conn.execute("SELECT MAX(seq) + 1 FROM cues WHERE video_id = ?", (vid,)).fetchone()[0]
        return conn.execute(
            "INSERT INTO cues (video_id, seq, start_s, end_s, text) VALUES (?, ?, ?, ?, 'cue')",
            (vid, seq, start, end),
        ).lastrowid

    conn.execute(
        "UPDATE videos SET published_at = ? WHERE id = ?",
        (int(time.time()) - published_days_ago * 86_400, vid),
    )
    store.save(
        conn,
        vid,
        score=score,
        reason="r",
        summary="s",
        moments=[store.Moment(cue(o, o + 1), o, why, cue(e - 1, e), e) for o, e, why in moments],
        matches=[store.Match(**m) for m in matches],
        overlaps=overlaps,
        profile_rev=1,
        model="m:x",
    )
    return vid


@pytest.fixture
def data(tmp_path: Path) -> Path:
    return _corpus(tmp_path)


def _write(data: Path, fn) -> object:
    conn = open_write_connection(data / "vidtheque.db")
    try:
        conn.execute("BEGIN IMMEDIATE")
        out = fn(conn)
        conn.execute("COMMIT")
        return out
    finally:
        conn.close()


def test_the_video_screen_names_each_repeat_and_starts_moments_after_it(
    data: Path, tmp_path: Path
) -> None:
    def seed(c: sqlite3.Connection) -> None:
        seen = c.execute("SELECT id FROM videos WHERE source_id = 'zduSFxRajkE'").fetchone()[0]
        _verdict(
            c,
            "kCc8FmEb1nY",
            moments=[(100.0, 400.0, "the setup"), (120.0, 180.0, "said before"), (500.0, 600.0, "new")],
            overlaps=[Span(seen, 90.0, 200.0, 12.0)],
        )

    _write(data, seed)
    with owner_client(tmp_path) as client:
        body = client.get(f"{API}/verdicts/kCc8FmEb1nY", headers=BEARER).json()

    assert body["overlaps"] == [
        {
            "video": {"video_id": "zduSFxRajkE", "title": "Making LLMs go brrr", "channel": "GPU MODE"},
            "start_s": 90.0,
            "end_s": 200.0,
            "seen_s": 12.0,
            "url": "https://youtu.be/zduSFxRajkE?t=10",
        }
    ]
    setup, said, new = body["moments"]
    assert (setup["start_s"], setup["url"]) == (200.0, "https://youtu.be/kCc8FmEb1nY?t=198")
    assert setup["repeat"] == {
        "video_id": "zduSFxRajkE",
        "title": "Making LLMs go brrr",
        "channel": "GPU MODE",
        "whole": False,
    }
    assert (said["start_s"], said["repeat"]["whole"]) == (120.0, True)
    assert (new["start_s"], new["repeat"]) == (500.0, None)


def test_a_repeat_of_a_deleted_video_is_not_shown(data: Path, tmp_path: Path) -> None:
    _write(
        data,
        lambda c: _verdict(
            c, "kCc8FmEb1nY", moments=[(100.0, 400.0, "x")], overlaps=[Span(999_999, 90.0, 200.0, 0.0)]
        ),
    )
    with owner_client(tmp_path) as client:
        body = client.get(f"{API}/verdicts/kCc8FmEb1nY", headers=BEARER).json()
    assert body["overlaps"] == []
    assert body["moments"][0]["start_s"] == 100.0 and body["moments"][0]["repeat"] is None


def _collections_corpus(c: sqlite3.Connection) -> dict[str, int]:
    evals = c.execute(
        "INSERT INTO profile_entries (text, weight, source) VALUES ('Coding agent evals', 0.9, 'owner')"
    ).lastrowid
    hype = c.execute(
        "INSERT INTO profile_entries (text, weight, source) VALUES ('Launch hype', -0.7, 'owner')"
    ).lastrowid
    up2 = {"entry_id": evals, "direction": "up", "strength": 2}
    up1 = {"entry_id": evals, "direction": "up", "strength": 1}
    down = {"entry_id": hype, "direction": "down", "strength": 2}
    ids = {
        # Central to it: first, though the oldest.
        "first": _verdict(
            c, "kCc8FmEb1nY", moments=[(0.0, 120.0, "the eval harness")], matches=[up2, down],
            published_days_ago=30,
        ),
        # Comes up, and its moment opens on what the first one said.
        "second": _verdict(
            c, "zduSFxRajkE", moments=[(30.0, 150.0, "harness again, then new")], matches=[up1], score=3,
        ),
        # Thumbed down: left out.
        "down": _verdict(c, "eMlx5fFNoYc", moments=[(0.0, 60.0, "x")], matches=[up2]),
    }
    _chunk_video(c, ids["first"], ["h0", "h1", "h2", "h3"])
    _chunk_video(c, ids["second"], ["z0", "h1", "h2", "z3", "z4"])
    c.execute("INSERT INTO feedback (video_id, state) VALUES (?, 'down')", (ids["down"],))
    return {"evals": evals, "hype": hype}


def test_collections_count_each_positive_entrys_moments(data: Path, tmp_path: Path) -> None:
    entries = _write(data, _collections_corpus)
    with owner_client(tmp_path) as client:
        body = client.get(f"{API}/collections", headers=BEARER).json()
    assert body == {
        "collections": [
            {
                "entry_id": entries["evals"],
                "text": "Coding agent evals",
                "moments": 2,
                "moments_s": 240.0,
                "videos": 2,
                "has_more": False,
            }
        ]
    }


def test_a_collection_marks_what_repeats_an_earlier_moment(data: Path, tmp_path: Path) -> None:
    entries = _write(data, _collections_corpus)
    with owner_client(tmp_path) as client:
        body = client.get(f"{API}/collections/{entries['evals']}", headers=BEARER).json()
        missing = client.get(f"{API}/collections/{entries['hype']}", headers=BEARER)
        junk = client.get(f"{API}/collections/abc", headers=BEARER)

    first, second = body["moments"]
    assert first["video"]["video_id"] == "kCc8FmEb1nY" and first["repeat"] is None
    assert first["url"] == "https://youtu.be/kCc8FmEb1nY?t=0"
    # Its chunks 1–2 (30–105 s) say what the first moment said: the link starts after.
    assert second["video"]["video_id"] == "zduSFxRajkE"
    assert (second["start_s"], second["url"]) == (105.0, "https://youtu.be/zduSFxRajkE?t=103")
    assert second["repeat"] == {
        "item": 0,
        "video_id": "kCc8FmEb1nY",
        "title": "Let's build GPT: from scratch",
        "whole": False,
    }
    assert body["moments_s"] == 240.0 and body["has_more"] is False
    # A negative entry has no collection; neither does a made-up id.
    for refused in (missing, junk):
        assert refused.status_code == 404
        assert refused.json()["error"] == "E_UNKNOWN_ENTRY"


def test_a_collection_is_capped_and_says_so(
    data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vidtheque_mcp.verdicts import collections

    monkeypatch.setattr(collections, "MAX_MOMENTS", 1)
    entries = _write(data, _collections_corpus)
    with owner_client(tmp_path) as client:
        body = client.get(f"{API}/collections/{entries['evals']}", headers=BEARER).json()
    assert len(body["moments"]) == 1 and body["has_more"] is True
    assert json.dumps(body)  # serializable all through
