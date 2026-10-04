"""The feed's endpoints — dashboard.md §25, companion.md §6.

What a screenshot cannot check: the routes are absent wherever there is no
write side, refused without a credential, paginated with `has_more`, and every
write refuses a malformed body before it reaches the store.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vidtheque_mcp.dashboard import ROOT
from vidtheque_mcp.db.connection import open_write_connection

from .test_dashboard_following import (
    BEARER,
    SAME_ORIGIN,
    _corpus,
    make_client,
    owner_client,
    sign_in,
)

API = f"{ROOT}/api"
JSON_BEARER = {**BEARER, "Content-Type": "application/json"}
JSON_SESSION = {**SAME_ORIGIN, "Content-Type": "application/json"}


def _seed_verdicts(data: Path) -> None:
    conn = open_write_connection(data / "vidtheque.db")
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0]:
            conn.execute("ROLLBACK")
            return
        ids = dict(conn.execute("SELECT source_id, id FROM videos"))
        for source_id in ("skipme00001", "noverdict01"):
            ids[source_id] = conn.execute(
                "INSERT INTO videos (source_id, url, title, duration_s, index_state)"
                " VALUES (?, ?, 'plain', 60, 'ready')",
                (source_id, f"https://youtu.be/{source_id}"),
            ).lastrowid
        cue = conn.execute(
            "SELECT id, start_s, end_s FROM cues WHERE video_id = ? ORDER BY seq LIMIT 1",
            (ids["kCc8FmEb1nY"],),
        ).fetchone()
        moments = [
            {"cue_id": cue[0], "offset_s": cue[1], "end_cue_id": cue[0], "end_s": cue[2], "why": "the setup"},
            # A cue a reindex took away: shown as dropped, never as a link.
            {"cue_id": 999_999, "offset_s": 10.0, "why": "gone"},
        ]
        # Retired, so the profile tests start empty; a retired entry still
        # names the match a past verdict made on it.
        evals = conn.execute(
            "INSERT INTO profile_entries (text, weight, source, retired_at)"
            " VALUES ('Evals', 0.9, 'owner', 1)"
        ).lastrowid
        hype = conn.execute(
            "INSERT INTO profile_entries (text, weight, source, retired_at)"
            " VALUES ('Launch hype', -0.7, 'owner', 1)"
        ).lastrowid
        matches = [
            {"entry_id": evals, "direction": "up", "strength": 2},
            {"entry_id": hype, "direction": "down", "strength": 1},
        ]
        now = int(time.time())
        for source_id, score, age, kept, explored in (
            ("kCc8FmEb1nY", 3, 10, moments, 0),
            ("zduSFxRajkE", 2, 20, [], 1),
            ("eMlx5fFNoYc", 0, 30, [], 0),
            ("skipme00001", 1, 40, [], 0),
        ):
            # The feed orders by publication; the older a verdict here, the older its video.
            conn.execute(
                "UPDATE videos SET published_at = ? WHERE id = ?", (now - age * 86_400, ids[source_id])
            )
            conn.execute(
                "INSERT INTO verdicts (video_id, score, reason, summary, moments,"
                " profile_rev, model, explored, created_at, matches)"
                " VALUES (?, ?, ?, 'summary', ?, 4, 'm:x', ?, ?, ?)",
                (
                    ids[source_id],
                    score,
                    f"reason {source_id}",
                    json.dumps(kept),
                    explored,
                    now - age,
                    json.dumps(matches if source_id == "kCc8FmEb1nY" else []),
                ),
            )
        conn.execute("COMMIT")
    finally:
        conn.close()


@pytest.fixture
def client(tmp_path: Path):
    _seed_verdicts(_corpus(tmp_path))
    with owner_client(tmp_path) as c:
        yield c


def _rows(tmp_path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(tmp_path / "data" / "vidtheque.db")
    try:
        return [tuple(r) for r in conn.execute(sql)]
    finally:
        conn.close()


# --------------------------------------------------------------------- access


GETS = (
    f"{API}/feed",
    f"{API}/feed/facets",
    f"{API}/week",
    f"{API}/verdicts/kCc8FmEb1nY",
    f"{API}/profile",
    f"{API}/costs",
    f"{API}/valued-time",
)
WRITES = (
    ("POST", f"{API}/signals"),
    ("POST", f"{API}/feedback"),
    ("POST", f"{API}/watched"),
    ("POST", f"{API}/shares"),
    ("POST", f"{API}/profile"),
    ("POST", f"{API}/profile/revert"),
    ("POST", f"{API}/devices"),
    ("DELETE", f"{API}/devices"),
    ("POST", f"{API}/budget"),
)


def test_no_credential_is_refused(client: TestClient) -> None:
    for path in GETS:
        refused = client.get(path)
        assert refused.status_code == 401, path
        assert refused.json()["error"] == "E_AUTH_REQUIRED"
        assert "kCc8FmEb1nY" not in refused.text
    for method, path in WRITES:
        refused = client.request(method, path, json={}, headers=SAME_ORIGIN)
        assert refused.status_code == 401, (method, path)


@pytest.mark.parametrize("readonly,auth", [(True, "token"), (False, "none")])
def test_absent_without_a_write_side(tmp_path: Path, readonly: bool, auth: str) -> None:
    """The read-only projection and `AUTH=none` have no feed: 404, not 401."""
    _seed_verdicts(_corpus(tmp_path))
    with make_client(tmp_path, auth_mode=auth, token="s3cret", readonly=readonly) as demo:
        for path in GETS:
            assert demo.get(path, headers=BEARER).status_code == 404, path
        for method, path in WRITES:
            assert demo.request(method, path, json={}, headers=JSON_BEARER).status_code in (404, 405)


# ----------------------------------------------------------------------- feed


def test_feed_pages_the_top_band_newest_first(client: TestClient) -> None:
    first = client.get(f"{API}/feed?limit=1", headers=BEARER).json()
    assert [r["video_id"] for r in first["items"]] == ["kCc8FmEb1nY"]
    assert set(first["items"][0]) >= {"channel", "title", "duration_s", "score", "reason"}
    assert first["pagination"] == {"limit": 1, "offset": 0, "has_more": True, "next_offset": 1}
    assert first["skipped"] == {"count": 2, "capped": False}

    second = client.get(f"{API}/feed?limit=1&offset=1", headers=BEARER).json()
    assert [(r["video_id"], r["explored"]) for r in second["items"]] == [("zduSFxRajkE", True)]
    assert second["pagination"]["has_more"] is False

    skipped = client.get(f"{API}/feed?band=skipped", headers=BEARER).json()
    assert [(r["video_id"], r["score"]) for r in skipped["items"]] == [
        ("eMlx5fFNoYc", 0),
        ("skipme00001", 1),
    ]
    assert client.get(f"{API}/feed?limit=999", headers=BEARER).json()["pagination"]["limit"] == 50


def test_feed_orders_by_when_the_video_came_out_not_when_it_was_judged(
    client: TestClient, tmp_path: Path
) -> None:
    # A backfill judges an old video today: it must not jump the newer one.
    conn = sqlite3.connect(tmp_path / "data" / "vidtheque.db")
    try:
        ids = dict(conn.execute("SELECT source_id, id FROM videos"))
        conn.execute("UPDATE videos SET published_at = 1700000000 WHERE id = ?", (ids["zduSFxRajkE"],))
        conn.execute("UPDATE videos SET published_at = 1790000000 WHERE id = ?", (ids["kCc8FmEb1nY"],))
        conn.execute("UPDATE verdicts SET created_at = ? WHERE video_id = ?", (int(time.time()), ids["zduSFxRajkE"]))
        # Undated videos go last, whatever their verdict's age.
        conn.execute("UPDATE videos SET published_at = NULL WHERE id = ?", (ids["eMlx5fFNoYc"],))
        conn.execute("UPDATE videos SET published_at = 1600000000 WHERE id = ?", (ids["skipme00001"],))
        conn.commit()
    finally:
        conn.close()
    top = client.get(f"{API}/feed", headers=BEARER).json()["items"]
    assert [r["video_id"] for r in top] == ["kCc8FmEb1nY", "zduSFxRajkE"]
    skipped = client.get(f"{API}/feed?band=skipped", headers=BEARER).json()["items"]
    assert [r["video_id"] for r in skipped] == ["skipme00001", "eMlx5fFNoYc"]


def test_feed_stops_paging_at_the_offset_ceiling(client: TestClient, monkeypatch) -> None:
    from vidtheque_mcp.dashboard import feed

    monkeypatch.setattr(feed, "OFFSET_MAX", 0)
    page = client.get(f"{API}/feed?limit=1", headers=BEARER).json()["pagination"]
    assert page["has_more"] is True and page["next_offset"] is None


@pytest.mark.parametrize("query", ["band=every", "limit=ten", "limit=--5", "limit=²", "offset=1e3"])
def test_feed_refuses_a_bad_parameter(client: TestClient, query: str) -> None:
    refused = client.get(f"{API}/feed?{query}", headers=BEARER)
    assert refused.status_code == 400
    assert refused.json()["error"] == "E_BAD_PARAM"


def _ids(client: TestClient, query: str) -> list[str]:
    return [r["video_id"] for r in client.get(f"{API}/feed?{query}", headers=BEARER).json()["items"]]


def test_feed_searches_titles_and_channels_and_filters_a_channel(client: TestClient) -> None:
    assert _ids(client, "q=brrr") == ["zduSFxRajkE"]
    assert _ids(client, "q=karpathy") == ["kCc8FmEb1nY"]  # the channel, any ASCII case
    assert _ids(client, "q=%25") == []  # a literal %, not a wildcard
    assert _ids(client, "q=gpu+mode&band=skipped") == []
    assert _ids(client, "channel=gpu%20mode") == ["zduSFxRajkE"]
    assert _ids(client, "channel=GPU") == []  # a channel is matched whole
    body = client.get(f"{API}/feed?q=GPT", headers=BEARER).json()
    assert (body["q"], body["channel"], body["entry"]) == ("GPT", None, None)
    # "skipped (n)" counts under the same search.
    assert body["skipped"]["count"] == 0
    assert client.get(f"{API}/feed?q=visualizing", headers=BEARER).json()["skipped"]["count"] == 1


def test_feed_sorts_oldest_first_on_request(client: TestClient) -> None:
    assert _ids(client, "order=oldest") == ["zduSFxRajkE", "kCc8FmEb1nY"]
    assert client.get(f"{API}/feed?order=oldest", headers=BEARER).json()["order"] == "oldest"
    assert client.get(f"{API}/feed?order=rating", headers=BEARER).status_code == 400


def _live_entry(tmp_path: Path) -> int:
    """A live entry that only zduSFxRajkE matched; kCc8FmEb1nY matched only retired ones."""
    conn = sqlite3.connect(tmp_path / "data" / "vidtheque.db")
    try:
        entry = conn.execute(
            "INSERT INTO profile_entries (text, weight, source) VALUES ('GPU kernels', 0.8, 'owner')"
        ).lastrowid
        conn.execute(
            "UPDATE verdicts SET matches = ? WHERE video_id = (SELECT id FROM videos WHERE source_id = 'zduSFxRajkE')",
            (json.dumps([{"entry_id": entry, "direction": "up", "strength": 2}]),),
        )
        conn.commit()
        return int(entry)
    finally:
        conn.close()


def test_feed_filters_by_a_profile_entry_and_other(client: TestClient, tmp_path: Path) -> None:
    entry = _live_entry(tmp_path)
    assert _ids(client, f"entry={entry}") == ["zduSFxRajkE"]
    # Other: no match on a live entry — the retired ones count for nothing.
    assert _ids(client, "entry=other") == ["kCc8FmEb1nY"]
    assert client.get(f"{API}/feed?entry=evals", headers=BEARER).status_code == 400

    facets = client.get(f"{API}/feed/facets", headers=BEARER).json()
    assert facets["entries"] == [{"entry_id": entry, "text": "GPU kernels", "direction": "up", "count": 1}]
    assert facets["other"] == 1 and facets["capped"] is False
    assert {c["name"]: c["count"] for c in facets["channels"]} == {"Andrej Karpathy": 1, "GPU MODE": 1}
    skipped = client.get(f"{API}/feed/facets?band=skipped", headers=BEARER).json()
    assert skipped["entries"] == [] and skipped["other"] == 2
    assert client.get(f"{API}/feed/facets?band=every", headers=BEARER).status_code == 400
    everything = client.get(f"{API}/feed/facets?band=all", headers=BEARER).json()
    assert sum(c["count"] for c in everything["channels"]) >= sum(c["count"] for c in skipped["channels"])
    assert client.get(f"{API}/feed/facets").status_code == 401


def test_facets_count_only_the_newest_window(client: TestClient, monkeypatch) -> None:
    from vidtheque_mcp.dashboard import feed

    monkeypatch.setattr(feed, "FACET_SCAN", 1)
    facets = client.get(f"{API}/feed/facets", headers=BEARER).json()
    assert facets["capped"] is True
    assert facets["channels"] == [{"name": "Andrej Karpathy", "count": 1}]


def test_verdict_links_only_the_moments_whose_receipt_holds(client: TestClient) -> None:
    body = client.get(f"{API}/verdicts/kCc8FmEb1nY", headers=BEARER).json()
    assert body["summary"] == "summary" and body["profile_rev"] == 4
    [moment] = body["moments"]
    assert moment["url"].startswith("https://youtu.be/kCc8FmEb1nY?t=")
    assert isinstance(moment["cue_id"], int)
    assert moment["end_cue_id"] == moment["cue_id"] and moment["end_s"] > moment["offset_s"]
    assert body["moments_dropped"] == 1
    # The minutes count the kept moment only, the one a reindex took counts nothing.
    assert body["moments_s"] == pytest.approx(moment["end_s"] - moment["offset_s"])


def test_feed_rows_carry_the_moment_seconds(client: TestClient) -> None:
    items = {i["video_id"]: i for i in client.get(f"{API}/feed", headers=BEARER).json()["items"]}
    # The fixture's other moment has no end, as a verdict written before spans would.
    assert items["kCc8FmEb1nY"]["moments_s"] is None
    assert items["zduSFxRajkE"]["moments_s"] == 0.0


def test_feed_and_verdict_name_the_matched_entries(client: TestClient) -> None:
    expected = [
        {"text": "Evals", "direction": "up", "strength": 2},
        {"text": "Launch hype", "direction": "down", "strength": 1},
    ]
    [item] = [i for i in client.get(f"{API}/feed", headers=BEARER).json()["items"] if i["video_id"] == "kCc8FmEb1nY"]
    body = client.get(f"{API}/verdicts/kCc8FmEb1nY", headers=BEARER).json()
    for matches in (item["matches"], body["matches"]):
        assert [{k: m[k] for k in ("text", "direction", "strength")} for m in matches] == expected
        assert all(isinstance(m["entry_id"], int) for m in matches)
    assert client.get(f"{API}/verdicts/zduSFxRajkE", headers=BEARER).json()["matches"] == []


@pytest.mark.parametrize(
    "video_id,code", [("nope0000000", "E_UNKNOWN_VIDEO"), ("noverdict01", "E_NO_VERDICT")]
)
def test_verdict_refusals(client: TestClient, video_id: str, code: str) -> None:
    refused = client.get(f"{API}/verdicts/{video_id}", headers=BEARER)
    assert refused.status_code == 404
    assert refused.json()["error"] == code


def test_a_video_with_no_verdict_is_still_named(client: TestClient) -> None:
    """A search hit can open a video no verdict scored yet; the page still draws it."""
    body = client.get(f"{API}/verdicts/noverdict01", headers=BEARER).json()
    assert body["video"] == {
        "video_id": "noverdict01", "title": "plain", "channel": None,
        "duration_s": 60.0, "published_at": None,
    }


# -------------------------------------------------------------------- signals


def test_signals_record_with_the_callers_client(client: TestClient, tmp_path: Path) -> None:
    watched = client.post(
        f"{API}/signals",
        json={"kind": "watch", "video_id": "kCc8FmEb1nY", "offset_s": 842.5},
        headers=BEARER,
    )
    assert watched.status_code == 200, watched.text
    sign_in(client)
    tapped = client.post(
        f"{API}/signals", json={"kind": "thumb_up", "video_id": "kCc8FmEb1nY"}, headers=SAME_ORIGIN
    )
    assert tapped.status_code == 200, tapped.text
    assert _rows(tmp_path, "SELECT kind, offset_s, client FROM signals ORDER BY id") == [
        ("watch", 842.5, "app"),
        ("thumb_up", None, "web"),
    ]


def test_a_search_from_the_feed_is_an_mcp_search_signal(client: TestClient, tmp_path: Path) -> None:
    """Same kind as the tool's (companion.md §2.3); `client` says it came from the feed."""
    done = client.post(f"{API}/signals", json={"kind": "mcp_search", "text": " eval harness "}, headers=BEARER)
    assert done.status_code == 200, done.text
    assert done.json()["kind"] == "mcp_search" and done.json()["video_id"] is None
    sign_in(client)
    web = client.post(f"{API}/signals", json={"kind": "mcp_search", "text": "x" * 600}, headers=SAME_ORIGIN)
    assert web.status_code == 200, web.text
    assert _rows(tmp_path, "SELECT kind, video_id, text, client FROM signals ORDER BY id") == [
        ("mcp_search", None, "eval harness", "app"),
        ("mcp_search", None, "x" * 500, "web"),
    ]


def test_a_watch_return_sets_its_time_once(client: TestClient, tmp_path: Path) -> None:
    sent = client.post(
        f"{API}/signals", json={"kind": "watch", "video_id": "kCc8FmEb1nY", "offset_s": 0}, headers=BEARER
    ).json()
    back = client.post(f"{API}/watched", json={"signal_id": sent["signal_id"], "watched_s": 20}, headers=BEARER)
    assert back.status_code == 200, back.text
    assert back.json() == {"signal_id": sent["signal_id"], "watched_s": 20, "already": False}
    again = client.post(f"{API}/watched", json={"signal_id": sent["signal_id"], "watched_s": 99}, headers=BEARER)
    assert again.json()["already"] and again.json()["watched_s"] == 20
    assert _rows(tmp_path, "SELECT kind, watched_s FROM signals") == [("watch", 20.0)]


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"signal_id": 1}, 400),
        ({"signal_id": "1", "watched_s": 3}, 400),
        ({"signal_id": 1, "watched_s": -1}, 400),
        ({"signal_id": 1, "watched_s": 3, "extra": 1}, 400),
        ({"signal_id": 404, "watched_s": 3}, 404),
    ],
)
def test_a_watch_return_refuses_a_malformed_body(client: TestClient, body: dict, status: int) -> None:
    refused = client.post(f"{API}/watched", json=body, headers=BEARER)
    assert refused.status_code == status, refused.text


def test_a_share_logs_the_link_and_says_whether_the_feed_missed_it(
    client: TestClient, tmp_path: Path
) -> None:
    # In the corpus and scored 1: a miss.
    low = client.post(f"{API}/shares", json={"url": "https://youtu.be/skipme00001?si=abc"}, headers=BEARER)
    assert low.status_code == 200, low.text
    assert low.json() == {
        "video_id": "skipme00001",
        "job_id": None,
        "indexed": True,
        "miss": True,
        "why": "scored 1",
    }
    # Not in the corpus: queued, and the miss waits for the verdict.
    new = client.post(
        f"{API}/shares", json={"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa&t=3"}, headers=BEARER
    ).json()
    assert new["job_id"] and new["indexed"] is False and new["miss"] is None
    assert _rows(tmp_path, "SELECT source_id, client FROM shares ORDER BY id") == [
        ("skipme00001", "app"),
        ("aaaaaaaaaaa", "app"),
    ]
    week = client.get(f"{API}/valued-time", headers=BEARER).json()["weeks"][0]
    assert week["misses"] == {"count": 1, "pending": 1, "shared": 2, "skipped": 0, "capped": False}


@pytest.mark.parametrize(
    "body",
    [{}, {"url": "https://example.com/watch?v=aaaaaaaaaaa"}, {"url": "https://youtube.com/@channel"}, {"url": 3}],
)
def test_a_share_refuses_anything_but_a_youtube_video(client: TestClient, tmp_path: Path, body: dict) -> None:
    assert client.post(f"{API}/shares", json=body, headers=BEARER).status_code == 400
    assert _rows(tmp_path, "SELECT COUNT(*) FROM shares") == [(0,)]


def test_valued_time_answers_eight_weeks(client: TestClient) -> None:
    body = client.get(f"{API}/valued-time", headers=BEARER).json()
    assert body["regret_target"] == 0.1
    assert len(body["weeks"]) == 8 and body["weeks"][0]["current"]
    assert set(body["weeks"][0]) == {"start", "current", "hits", "regret", "misses"}


def test_feedback_is_a_state_the_verdict_shows_and_a_second_call_takes_back(
    client: TestClient, tmp_path: Path
) -> None:
    def shown() -> str:
        return client.get(f"{API}/verdicts/kCc8FmEb1nY", headers=BEARER).json()["feedback"]

    assert shown() == "none"
    for state in ("up", "muted", "none"):
        done = client.post(f"{API}/feedback", json={"video_id": "kCc8FmEb1nY", "state": state}, headers=BEARER)
        assert done.status_code == 200, done.text
        assert done.json() == {"video_id": "kCc8FmEb1nY", "state": state}
        assert shown() == state
    # Each set is an event; taking back writes none.
    assert _rows(tmp_path, "SELECT kind, client FROM signals ORDER BY id") == [
        ("thumb_up", "app"),
        ("mute", "app"),
    ]
    assert _rows(tmp_path, "SELECT state, seen FROM feedback") == [("none", "none")]


def test_a_thumb_through_signals_sets_the_state_too(client: TestClient) -> None:
    client.post(f"{API}/signals", json={"kind": "thumb_down", "video_id": "kCc8FmEb1nY"}, headers=BEARER)
    assert client.get(f"{API}/verdicts/kCc8FmEb1nY", headers=BEARER).json()["feedback"] == "down"


@pytest.mark.parametrize(
    "body,status",
    [
        ({"video_id": "kCc8FmEb1nY", "state": "thumb_up"}, 400),
        ({"video_id": "kCc8FmEb1nY", "state": None}, 400),
        ({"video_id": "kCc8FmEb1nY"}, 400),
        ({"state": "up"}, 400),
        ({"video_id": "kCc8FmEb1nY", "state": "up", "kind": "x"}, 400),
        ({"video_id": "nope0000000", "state": "up"}, 404),
    ],
)
def test_feedback_refuses_a_malformed_body(
    client: TestClient, tmp_path: Path, body: dict, status: int
) -> None:
    assert client.post(f"{API}/feedback", json=body, headers=BEARER).status_code == status
    assert _rows(tmp_path, "SELECT COUNT(*) FROM feedback") == [(0,)]


@pytest.mark.parametrize(
    "body,status",
    [
        ({"kind": "watch", "video_id": "kCc8FmEb1nY"}, 400),
        ({"kind": "open", "video_id": "kCc8FmEb1nY", "offset_s": 3}, 400),
        ({"kind": "watch", "video_id": "kCc8FmEb1nY", "offset_s": -1}, 400),
        ({"kind": "watch", "video_id": "kCc8FmEb1nY", "offset_s": True}, 400),
        ({"kind": "watch", "video_id": "kCc8FmEb1nY", "offset_s": 10**400}, 400),
        ({"kind": "mcp_search", "video_id": "kCc8FmEb1nY"}, 400),
        ({"kind": "mcp_search", "text": "evals", "video_id": "kCc8FmEb1nY"}, 400),
        ({"kind": "mcp_search", "text": "evals", "offset_s": 3}, 400),
        ({"kind": "mcp_search", "text": "  "}, 400),
        ({"kind": "mcp_read", "video_id": "kCc8FmEb1nY"}, 400),
        ({"kind": "open"}, 400),
        ({"kind": "open", "video_id": "kCc8FmEb1nY", "text": "x"}, 400),
        ({"kind": "open", "video_id": "nope0000000"}, 404),
    ],
)
def test_signals_refuse_a_malformed_body(
    client: TestClient, tmp_path: Path, body: dict, status: int
) -> None:
    assert client.post(f"{API}/signals", json=body, headers=BEARER).status_code == status
    assert _rows(tmp_path, "SELECT COUNT(*) FROM signals") == [(0,)]


def test_writes_take_json_only(client: TestClient) -> None:
    form = client.post(f"{API}/signals", data={"kind": "open"}, headers=BEARER)
    assert form.status_code == 400 and "JSON" in form.json()["message"]
    nan = client.post(
        f"{API}/signals",
        content=b'{"kind": "watch", "video_id": "kCc8FmEb1nY", "offset_s": NaN}',
        headers=JSON_BEARER,
    )
    assert nan.status_code == 400
    big = client.post(f"{API}/profile", content=b" " * 20_000, headers=JSON_BEARER)
    assert big.status_code == 413


def test_a_session_write_needs_same_origin_evidence(client: TestClient) -> None:
    sign_in(client)
    refused = client.post(f"{API}/signals", json={"kind": "open", "video_id": "kCc8FmEb1nY"})
    assert refused.status_code == 403
    assert refused.json()["error"] == "E_BAD_ORIGIN"


# -------------------------------------------------------------------- profile


def test_profile_ops_carry_the_actor_and_revert(client: TestClient) -> None:
    added = client.post(
        f"{API}/profile",
        json={"add": [{"text": "Eval harnesses", "weight": 0.9}], "reason": "from the app"},
        headers=BEARER,
    ).json()
    [entry] = added["entries"]
    assert entry["source"] == "app" and entry["evidence"] == "from the app"
    assert added["revision"] == added["applied"]["events"][0]

    sign_in(client)
    owner = client.post(
        f"{API}/profile",
        json={"add": [{"text": "Local inference", "weight": 0.5}],
              "reweight": [{"id": entry["id"], "weight": 0.4}]},
        headers=SAME_ORIGIN,
    ).json()
    assert {e["text"]: e["source"] for e in owner["entries"]} == {
        "Eval harnesses": "app",
        "Local inference": "owner",
    }
    assert [e["actor"] for e in owner["history"]["events"]] == ["owner", "owner", "app"]

    page = client.get(f"{API}/profile?limit=1", headers=BEARER).json()["history"]
    assert page["has_more"] is True
    rest = client.get(f"{API}/profile?limit=5&before={page['next_before']}", headers=BEARER).json()
    assert len(rest["history"]["events"]) == 2 and rest["history"]["has_more"] is False

    back = client.post(f"{API}/profile/revert", json={"revision": added["revision"]}, headers=BEARER)
    assert back.status_code == 200, back.text
    assert [(e["text"], e["weight"]) for e in back.json()["entries"]] == [("Eval harnesses", 0.9)]
    assert len(back.json()["reverted"]["events"]) == 2


@pytest.mark.parametrize(
    "body,status,code",
    [
        ({}, 400, "E_BAD_PARAM"),
        ({"add": [{"text": "x", "weight": 2}]}, 400, "E_BAD_PARAM"),
        ({"add": [{"text": "x", "weight": "high"}]}, 400, "E_BAD_PARAM"),
        ({"add": [{"text": "x", "weight": -(10**400)}]}, 400, "E_BAD_PARAM"),
        ({"add": [{"text": "x", "weight": 0.5, "id": 1}]}, 400, "E_BAD_PARAM"),
        ({"add": [{"text": 5, "weight": 0.5}]}, 400, "E_BAD_PARAM"),
        ({"add": {"text": "x", "weight": 0.5}}, 400, "E_BAD_PARAM"),
        ({"drop": ["1"]}, 400, "E_BAD_PARAM"),
        ({"drop": [41]}, 404, "E_UNKNOWN_ENTRY"),
        ({"reweight": [{"id": 1}]}, 400, "E_BAD_PARAM"),
        ({"rewrite": []}, 400, "E_BAD_PARAM"),
        ({"add": [{"text": f"t{i}", "weight": 0.1} for i in range(41)]}, 413, "E_TOO_LARGE"),
    ],
)
def test_profile_ops_refuse_a_malformed_body(
    client: TestClient, tmp_path: Path, body: dict, status: int, code: str
) -> None:
    refused = client.post(f"{API}/profile", json=body, headers=BEARER)
    assert refused.status_code == status, refused.text
    assert refused.json()["error"] == code
    assert _rows(tmp_path, "SELECT COUNT(*) FROM profile_events") == [(0,)]


@pytest.mark.parametrize(
    "body,status,code",
    [
        ({}, 400, "E_BAD_PARAM"),
        ({"event_id": 1, "revision": 0}, 400, "E_BAD_PARAM"),
        ({"revision": -1}, 400, "E_BAD_PARAM"),
        ({"event_id": "1"}, 400, "E_BAD_PARAM"),
        ({"event_id": 10**21}, 400, "E_BAD_PARAM"),
        ({"event_id": 77}, 404, "E_UNKNOWN_EVENT"),
    ],
)
def test_profile_revert_refuses_a_malformed_body(
    client: TestClient, body: dict, status: int, code: str
) -> None:
    refused = client.post(f"{API}/profile/revert", json=body, headers=BEARER)
    assert refused.status_code == status, refused.text
    assert refused.json()["error"] == code


# -------------------------------------------------------------------- devices


def test_devices_register_refresh_and_remove(client: TestClient, tmp_path: Path) -> None:
    token = "dGVzdA:APA91b-x_y"
    for _ in range(2):
        registered = client.post(f"{API}/devices", json={"token": token}, headers=BEARER).json()
        assert registered == {"registered": True, "devices": 1, "evicted": 0}
    for i in range(20):
        client.post(f"{API}/devices", json={"token": f"tok{i}"}, headers=BEARER)
    # Past 20, the device seen longest ago goes.
    assert _rows(tmp_path, "SELECT COUNT(*) FROM devices") == [(20,)]
    assert _rows(tmp_path, f"SELECT COUNT(*) FROM devices WHERE token = '{token}'") == [(0,)]

    gone = client.request("DELETE", f"{API}/devices", json={"token": "tok3"}, headers=BEARER)
    assert gone.json() == {"removed": True}
    again = client.request("DELETE", f"{API}/devices", json={"token": "tok3"}, headers=BEARER)
    assert again.json() == {"removed": False}


@pytest.mark.parametrize("body", [{}, {"token": ""}, {"token": "a b"}, {"token": 5}, {"token": "x", "os": "a"}])
def test_devices_refuse_a_malformed_token(client: TestClient, body: dict) -> None:
    refused = client.post(f"{API}/devices", json=body, headers=BEARER)
    assert refused.status_code == 400
    assert refused.json()["error"] == "E_BAD_PARAM"


def test_feed_rows_show_the_weeks_tier_beside_the_verdicts_score(client: TestClient) -> None:
    items = {i["video_id"]: i for i in client.get(f"{API}/feed", headers=BEARER).json()["items"]}
    karpathy = items["kCc8FmEb1nY"]
    # Unranked, alone among its week's 3s: the fallback keeps it a 3.
    assert (karpathy["score"], karpathy["tier"], karpathy["week_rank"]) == (3, 3, 1)
    assert len(karpathy["week"]) == 10
    body = client.get(f"{API}/verdicts/kCc8FmEb1nY", headers=BEARER).json()
    assert (body["tier"], body["week"], body["week_rank"]) == (3, karpathy["week"], 1)
    skipped = client.get(f"{API}/feed?band=skipped", headers=BEARER).json()["items"]
    assert all(i["tier"] == i["score"] and i["week_rank"] is None for i in skipped)


# --------------------------------------------------------------------- week


def _this_week(tmp_path: Path, videos: list[tuple[str, int, float, float | None]]) -> None:
    """(source_id, score, duration_s, moment seconds or None) published this week, an hour apart."""
    from vidtheque_mcp.verdicts import week

    start, _ = week.bounds(week.week_of(time.time()))
    conn = open_write_connection(tmp_path / "data" / "vidtheque.db")
    try:
        conn.execute("BEGIN IMMEDIATE")
        for i, (source_id, score, duration, spans) in enumerate(videos):
            vid = conn.execute(
                "INSERT INTO videos (source_id, url, title, duration_s, published_at, index_state)"
                " VALUES (?, ?, ?, ?, ?, 'ready')",
                (source_id, f"https://youtu.be/{source_id}", f"talk {source_id}", duration, start + 3600 * (i + 1)),
            ).lastrowid
            cue = conn.execute(
                "INSERT INTO cues (video_id, seq, start_s, end_s, text) VALUES (?, 0, 0, ?, 'x')", (vid, duration)
            ).lastrowid
            moments = [] if spans is None else [
                {"cue_id": cue, "offset_s": 0.0, "end_cue_id": cue, "end_s": spans, "why": "w"}
            ]
            conn.execute(
                "INSERT INTO verdicts (video_id, score, reason, summary, moments, profile_rev, model)"
                " VALUES (?, ?, 'r', 's', ?, 0, 'm')",
                (vid, score, json.dumps(moments)),
            )
        conn.execute("COMMIT")
    finally:
        conn.close()


def test_the_week_fits_the_ranked_videos_under_the_budget(client: TestClient, tmp_path: Path) -> None:
    _this_week(
        tmp_path,
        [
            ("weekwhole01", 3, 3000.0, 600.0),  # the week's top: asks the whole 50 min
            ("weeklong001", 2, 7200.0, 1200.0),  # asks 20 min: past what is left
            ("weekshort01", 2, 1800.0, 300.0),  # asks 5 min: still fits
            ("weekskip001", 1, 600.0, None),  # not a candidate
        ],
    )
    assert client.post(f"{API}/budget", json={"week_budget_min": 60}, headers=JSON_BEARER).json() == {
        "week_budget_min": 60
    }
    body = client.get(f"{API}/week", headers=BEARER).json()
    assert body["budget_min"] == 60 and body["next"] is None
    assert [(i["video_id"], i["tier"], i["asks_s"]) for i in body["items"]] == [
        ("weekwhole01", 3, 3000.0),
        ("weekshort01", 2, 300.0),
    ]
    assert body["asks_s"] == 3300.0
    assert body["rest"] == {"count": 1, "asks_s": 1200.0}
    assert len(body["days"]) == 7 and body["days"][0]["day"] == body["week"]
    assert sum(d["asks_s"] for d in body["days"]) == 3300.0
    assert sum(d["candidates"] for d in body["days"]) == 3
    # The week before has its own list, and a way back to this one.
    before = client.get(f"{API}/week?week={body['previous']}", headers=BEARER).json()
    assert before["next"] == body["week"] and before["budget_min"] == 60


@pytest.mark.parametrize(
    "query", ["week=2026-10-06", "week=next", "week=2026-02-30", "week=2999-01-07", "week=0001-01-01"]
)
def test_week_refuses_a_day_that_starts_no_past_week(client: TestClient, query: str) -> None:
    # 2026-10-06 is a Tuesday; 2999-01-07 a Monday not yet begun.
    assert client.get(f"{API}/week?{query}", headers=BEARER).json()["error"] == "E_BAD_PARAM"


@pytest.mark.parametrize("body", [{"week_budget_min": 10_081}, {"week_budget_min": -1}, {"week_budget_min": "60"},
                                  {"week_budget_min": True}, {"week_budget_min": 60, "daily": 9}])
def test_budget_refuses_anything_but_whole_minutes_in_a_week(client: TestClient, body: dict) -> None:
    refused = client.post(f"{API}/budget", json=body, headers=JSON_BEARER)
    assert refused.status_code == 400
    assert client.get(f"{API}/week", headers=BEARER).json()["budget_min"] == 210


def test_show_all_lists_every_verdict_in_one_list(client: TestClient) -> None:
    every = client.get(f"{API}/feed?band=all", headers=BEARER).json()
    top = client.get(f"{API}/feed?band=top", headers=BEARER).json()["items"]
    skipped = client.get(f"{API}/feed?band=skipped", headers=BEARER).json()["items"]
    assert every["band"] == "all"
    assert {i["video_id"] for i in every["items"]} == {i["video_id"] for i in top + skipped}
    published = [i["published_at"] for i in every["items"]]
    assert published == sorted(published, reverse=True)
