"""The public box's sample feed (demo-site.md §8): the profile it writes, the
verdicts it queues, and what `GET /api/feed` lets out."""

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from vidtheque_mcp.db.connection import open_write_connection
from vidtheque_mcp.profile import store as profile_store
from vidtheque_mcp.public import sample
from vidtheque_mcp.public.feed import EXCERPT_CHARS
from vidtheque_mcp.public.settings import PublicSettings
from vidtheque_mcp.verdicts import store as verdicts_store

from .test_public import PUBLIC, _tag_video, make_client, public_client  # noqa: F401


def _db(tmp_path: Path):
    return open_write_connection(tmp_path / "data" / "vidtheque.db")


def _judge(tmp_path: Path, public_id: str, score: int, cue_text: str | None = None) -> None:
    conn = _db(tmp_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        sample.sync_profile(conn)
        video_id, cue_id, start = conn.execute(
            "SELECT v.id, c.id, c.start_s FROM videos v JOIN cues c ON c.video_id = v.id"
            " WHERE v.public_id = ? ORDER BY c.seq LIMIT 1",
            (public_id,),
        ).fetchone()
        if cue_text is not None:
            conn.execute("UPDATE cues SET text = ? WHERE id = ?", (cue_text, cue_id))
        verdicts_store.save(
            conn,
            video_id,
            score=score,
            reason="Matches coding agent evals.",
            summary="A talk.",
            moments=[verdicts_store.Moment(cue_id, float(start) + 1, "the eval loop")],
            matches=[verdicts_store.Match(int(profile_store.entries(conn)[0]["id"]), "up", 2)],
            profile_rev=profile_store.revision(conn),
            model="api:test",
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


def test_the_profile_sync_writes_once_and_requeues_only_after_a_change(tmp_path: Path) -> None:
    with make_client(tmp_path, PUBLIC):
        pass
    conn = _db(tmp_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        assert sample.sync_profile(conn) == len(sample.ENTRIES)
        assert sample.sync_profile(conn) == 0, "an unchanged profile writes nothing"
        videos = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        assert sample.queue_stale(conn) == videos
        assert sample.queue_stale(conn) == 0, "a queued video is not queued twice"
        conn.execute("UPDATE jobs SET state = 'done'")
        rev = profile_store.revision(conn)
        for (video_id,) in conn.execute("SELECT id FROM videos").fetchall():
            conn.execute(
                "INSERT INTO verdicts (video_id, score, reason, summary, profile_rev, model)"
                " VALUES (?, 2, 'r', 's', ?, 'm')",
                (video_id, rev),
            )
        assert sample.queue_stale(conn) == 0
        entry = profile_store.entries(conn)[0]
        profile_store.apply(conn, profile_store.Ops(reweight=[(int(entry["id"]), 0.1)]), "owner")
        assert sample.sync_profile(conn) == 1, "a hand edit is put back"
        assert sample.queue_stale(conn) == videos, "a changed profile rejudges everything"
        conn.execute("COMMIT")
    finally:
        conn.close()


def test_the_feed_is_best_first_with_short_excerpts_and_youtube_links(tmp_path: Path) -> None:
    with make_client(tmp_path, PUBLIC):
        pass
    long_cue = "word " * 200
    _judge(tmp_path, "kCc8FmEb1nY", 2, cue_text=long_cue)
    _judge(tmp_path, "zduSFxRajkE", 3)
    with make_client(tmp_path, PUBLIC, fresh=False) as client:
        payload = client.get("/api/feed").json()
        assert [i["score"] for i in payload["items"]] == [3, 2]
        assert payload["profile"]["name"] == sample.NAME
        assert payload["has_more"] is False
        moment = payload["items"][1]["moments"][0]
        assert len(moment["excerpt"]) <= EXCERPT_CHARS
        assert moment["url"].startswith("https://youtu.be/kCc8FmEb1nY?t=")
        assert payload["items"][1]["matches"] == [
            {"text": sample.ENTRIES[0][0], "direction": "up"}
        ]

        assert [i["score"] for i in client.get("/api/feed?min_score=3").json()["items"]] == [3]
        page = client.get("/api/feed?limit=1").json()
        assert page["has_more"] is True and page["next_offset"] == 1

        assert client.get("/api/feed?tags=Paris").status_code == 400
        assert client.get("/api/feed?order=random").status_code == 400


def test_the_feed_narrows_to_a_tag(tmp_path: Path) -> None:
    with make_client(tmp_path, PUBLIC):
        pass
    _judge(tmp_path, "kCc8FmEb1nY", 2)
    _judge(tmp_path, "zduSFxRajkE", 3)
    _tag_video(tmp_path, "kCc8FmEb1nY", "series:aie-paris-2026")
    with make_client(tmp_path, PUBLIC, fresh=False) as client:
        items = client.get("/api/feed?tags=series:aie-paris-2026").json()["items"]
    assert [i["video_id"] for i in items] == ["kCc8FmEb1nY"]


def test_the_feed_is_absent_outside_public_mode(tmp_path: Path) -> None:
    with make_client(tmp_path, PublicSettings(enabled=False)) as client:
        assert client.get("/api/feed").status_code == 404


def test_an_empty_feed_says_there_is_no_profile(public_client: TestClient) -> None:
    assert public_client.get("/api/feed").json() == {
        "profile": None,
        "items": [],
        "has_more": False,
        "next_offset": None,
    }


def test_only_the_newest_videos_are_judged(tmp_path: Path) -> None:
    with make_client(tmp_path, PUBLIC):
        pass
    conn = _db(tmp_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        sample.sync_profile(conn)
        assert sample.queue_stale(conn, videos=1) == 1
        newest = conn.execute(
            "SELECT id FROM videos ORDER BY published_at DESC, id DESC LIMIT 1"
        ).fetchone()[0]
        queued = conn.execute(
            "SELECT json_extract(args_json, '$.video_id') FROM jobs WHERE kind = 'verdict'"
        ).fetchall()
        assert [r[0] for r in queued] == [newest]
        conn.execute("COMMIT")
    finally:
        conn.close()


def test_the_cheap_input_is_title_chapters_and_a_quarter_of_the_transcript(
    tmp_path: Path,
) -> None:
    import asyncio

    from vidtheque_mcp.verdicts.stage import CHEAP_TRANSCRIPT_CHARS, VerdictStage

    with make_client(tmp_path, PUBLIC) as client:
        deps = client.app.state.assembled.deps
        stage = VerdictStage(deps, model=None, label="t", cheap=True)  # type: ignore[arg-type]
        video_id = asyncio.run(
            deps.db.read(
                lambda c: c.execute(
                    "SELECT id FROM videos WHERE public_id = 'kCc8FmEb1nY'"
                ).fetchone()[0]
            )
        )
        inputs = asyncio.run(stage._inputs(video_id, "kCc8FmEb1nY"))
    assert inputs.summary.startswith("Title: Let's build GPT: from scratch\nChannel: Andrej Karpathy")
    assert len(inputs.transcript) <= CHEAP_TRANSCRIPT_CHARS + 40
