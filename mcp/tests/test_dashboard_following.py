"""The Following surface — dashboard.md §2.2, §2.3, §3.2, §5.5, §13 and §17.

Two pages and seven POSTs, built on `follows/store.py` for the reads and on
`tools/follows.follow_channel` plus `follows/params.build_rules` for the
writes. Nothing here downloads, embeds or reaches a network: a check is a job
this suite never runs, and the ledger it would write is seeded directly.

The three things this file is most interested in are the ones a screenshot
cannot check:

* **the whole surface is absent, not refused,** in
  ``VIDTHEQUE_PUBLIC_READONLY=1`` and in ``VIDTHEQUE_AUTH=none`` — both read
  pages as well as every write, plus the rail item that points at them;
* a corpus string — a video title, a yt-dlp failure, a rule's own reason
  sentence — never becomes markup;
* the derived line above the ledger is read out of the rows on the page and is
  printed only when it is true.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

import httpx2 as httpx
from starlette.testclient import TestClient

from vidtheque_mcp.app import build_app
from vidtheque_mcp.config import Settings
from vidtheque_mcp.dashboard import ROOT, WRITE_ROUTES
from vidtheque_mcp.dashboard.settings import DashboardSettings
from vidtheque_mcp.db.connection import open_write_connection
from vidtheque_mcp.follows import store as follows_store
from vidtheque_mcp.follows.rules import Rules
from vidtheque_mcp.public.settings import PublicSettings

from .conftest import FakeEmbeddings, seed

# What was on someone's screen, and what a check wrote about it. Both are
# attacker-controlled in exactly the same way.
HOSTILE = '<script>alert(document.cookie)</script> <img src=x onerror=alert(1)>'

TOKEN = "s3cret"
PASSWORD = "correct-horse"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
SAME_ORIGIN = {"Origin": "http://localhost:8080"}
CROSS_ORIGIN = {"Origin": "http://evil.example"}

# The write side of this surface, as the router sees it.
FOLLOW_POSTS = (
    f"{ROOT}/following",
    f"{ROOT}/following/karpathy/state",
    f"{ROOT}/following/karpathy/check",
    f"{ROOT}/following/karpathy/rules",
    f"{ROOT}/following/karpathy/delete",
    f"{ROOT}/following/karpathy/queue",
)


# --------------------------------------------------------------------- setup


def _corpus(tmp_path: Path) -> Path:
    """The shared fixture corpus, plus two follows and a ledger to read."""
    data = tmp_path / "data"
    if data.exists():  # a test that builds two apps seeds the corpus once
        return data
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    conn = open_write_connection(data / "vidtheque.db")
    try:
        conn.execute("BEGIN IMMEDIATE")
        _seed_follows(conn)
        conn.execute("COMMIT")
    finally:
        conn.close()
    return data


def _seed_follows(conn: sqlite3.Connection) -> None:
    """One active follow with a length rule and a ledger, one paused follow.

    The ledger is the shape the page exists for: a candidate the floor turned
    away by twelve seconds, one it turned away by an hour, one the budget is
    holding, one waiting on a person, and one carrying a hostile title and a
    hostile reason.
    """
    karpathy = follows_store.create(
        conn,
        title="Andrej Karpathy",
        source_url="https://www.youtube.com/@karpathy",
        kind="channel",
        rules=Rules(
            tabs=("videos",),
            min_duration_s=480,
            max_per_check=5,
            tags=("topic:llm",),
            channels="all",
        ),
    )
    follows_store.create(
        conn,
        title="Paused Channel",
        source_url="https://www.youtube.com/@paused",
        kind="channel",
        rules=Rules(tabs=("videos", "shorts"), mode="review"),
    )
    conn.execute("UPDATE follows SET state = 'paused' WHERE collection_id <> ?", (karpathy,))
    conn.execute(
        "UPDATE follows SET last_new_at = unixepoch() - 3600, "
        "last_error_code = 'E_RATE_LIMIT', last_error_message = ? "
        "WHERE collection_id = ?",
        ("the source rate-limited this box", karpathy),
    )
    conn.execute(
        "UPDATE collections SET last_sync_at = unixepoch() - 7200 WHERE id = ?", (karpathy,)
    )

    ledger = [
        # 7:48 against an 8:00 floor: twelve seconds, which is what the derived
        # line above the band is for.
        ("nearmiss001", "Near the floor", 468.0, "skipped_duration",
         "7:48, shorter than your 8:00 floor", "listing"),
        ("nearmiss002", "Also near the floor", 450.0, "skipped_duration",
         "7:30, shorter than your 8:00 floor", "probe"),
        # An hour under is not a near miss and must not be counted as one.
        ("faraway0001", "Nowhere near it", 30.0, "skipped_duration",
         "0:30, shorter than your 8:00 floor", "listing"),
        ("heldbudget1", "Held by the budget", 5400.0, "held_budget",
         "the day's 8h of video is already spoken for", "listing"),
        ("heldreview1", f"Held for you {HOSTILE}", None, "held_review",
         f"neither the listing nor a probe gave a duration {HOSTILE}", "probe"),
        ("alreadyidx1", "Already in the corpus", 1800.0, "already_indexed",
         "this one is already indexed", "listing"),
        # The one that was accepted: it must not appear on the passed-over band.
        ("acceptedvid", "Accepted and queued", 3600.0, "queued",
         "nothing in this rule rejects it", "listing"),
    ]
    for source_id, title, duration, decision, reason, judged in ledger:
        follows_store.record_seen(
            conn,
            karpathy,
            source_id=source_id,
            url=f"https://youtu.be/{source_id}",
            title=title,
            duration_s=duration,
            published_at=1740000000,
            tab="videos",
            decision=decision,
            reason=reason,
            judged_from=judged,
        )

    # The spend the accepted candidate cost, written the way the check writes
    # it: in the same transaction as its `queued` ledger row, into the table
    # the delete does not cascade to (migration 0007). The band the list prints
    # sums this table, so a corpus with only `follow_seen` rows reports a day
    # nothing spent.
    follows_store.record_spend(conn, karpathy, "acceptedvid", 3600.0)

    # The two job kinds a follow's page reads back: its own checks, and the
    # index job a check enqueued.
    conn.execute(
        "INSERT INTO jobs (owner_id, public_id, kind, args_json, n_items, priority, "
        "state, created_at, started_at, finished_at, collection_id) VALUES "
        "(1, 'job_followchk1', 'follow_check', '{}', 1, 100, 'done', "
        "unixepoch() - 7200, unixepoch() - 7200, unixepoch() - 7180, ?)",
        (karpathy,),
    )
    conn.execute(
        "INSERT INTO jobs (owner_id, public_id, kind, args_json, n_items, n_done, "
        "priority, state, created_at, collection_id) VALUES "
        "(1, 'job_followidx1', 'index', '{}', 1, 1, 100, 'done', unixepoch() - 7100, ?)",
        (karpathy,),
    )


def _settings(tmp_path: Path, **kwargs) -> Settings:
    values = dict(
        data_dir=_corpus(tmp_path),
        public_url="http://localhost:8080",
        worker_url="http://worker:8081",
        secret="test-secret",
        vec_max_distance=0.72,
        frame_max_distance=0.96,
    )
    values.update(kwargs)
    return Settings(**values)  # type: ignore[arg-type]


def _worker_down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("worker unavailable", request=request)


def make_client(
    tmp_path: Path,
    *,
    auth_mode: str = "none",
    token: str | None = None,
    password: str | None = None,
    readonly: bool = False,
    worker_handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> TestClient:
    settings = _settings(
        tmp_path, auth_mode=auth_mode, static_token=token, password=password
    )
    worker_http = httpx.AsyncClient(
        transport=httpx.MockTransport(worker_handler or _worker_down)
    )
    app = build_app(
        settings,
        embeddings=FakeEmbeddings(),
        run_pipeline=False,
        public=PublicSettings(enabled=readonly),
        dashboard=DashboardSettings(),
        worker_status_http=worker_http,
    )
    return TestClient(app, base_url="http://localhost:8080")


def owner_client(tmp_path: Path, **kwargs) -> TestClient:
    """`token` mode with a password — the deployment this surface is for."""
    return make_client(
        tmp_path, auth_mode="token", token=TOKEN, password=PASSWORD, **kwargs
    )


def sign_in(client: TestClient) -> None:
    response = client.post(
        f"{ROOT}/login",
        data={"password": PASSWORD},
        headers=SAME_ORIGIN,
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def _empty_corpus(tmp_path: Path) -> TestClient:
    """The same deployment with nothing followed — the empty state's own case."""
    data = tmp_path / "bare"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    settings = Settings(  # type: ignore[arg-type]
        data_dir=data,
        public_url="http://localhost:8080",
        worker_url="http://worker:8081",
        secret="test-secret",
        auth_mode="token",
        static_token=TOKEN,
        password=PASSWORD,
        vec_max_distance=0.72,
        frame_max_distance=0.96,
    )
    app = build_app(
        settings,
        embeddings=FakeEmbeddings(),
        run_pipeline=False,
        public=PublicSettings(enabled=False),
        dashboard=DashboardSettings(),
        worker_status_http=httpx.AsyncClient(
            transport=httpx.MockTransport(_worker_down)
        ),
    )
    return TestClient(app, base_url="http://localhost:8080")


# --------------------------------------------- 1. absent, not merely refused


def test_the_following_surface_is_absent_in_readonly_mode(tmp_path: Path) -> None:
    """§2.3, with a credential configured — so the *flag* is doing the work.

    This is the deployment Tom ships publicly. Every route of this surface must
    be missing rather than refusing, **including its two reads**: a surface
    whose every affordance POSTs has nothing to show a deployment that
    registers no write side, and a route that exists and refuses is a route
    somebody probes.
    """
    with owner_client(tmp_path, readonly=True) as demo:
        assert demo.get(f"{ROOT}/api/library", headers=BEARER).status_code == 200
        for path in (f"{ROOT}/api/following", f"{ROOT}/api/following/karpathy"):
            assert demo.get(path, headers=BEARER).status_code == 404, path
        for path in FOLLOW_POSTS:
            refused = demo.post(path, headers={**BEARER, **SAME_ORIGIN})
            assert refused.status_code == 404, path


def test_the_following_surface_is_absent_in_auth_none(tmp_path: Path) -> None:
    """§3.2 rule 3, as a status code: **404, not 403**.

    `none` is the mode with no credential to check. An unauthenticated instance
    behind a tunnel with a live "follow this channel" button is a standing
    remote-yt-dlp subscription pointed at the operator's residential IP — worse
    than the one-shot version of the same button, because nobody has to come
    back and press it again.
    """
    with make_client(tmp_path) as client:  # auth=none
        for path in (f"{ROOT}/api/following", f"{ROOT}/api/following/karpathy"):
            assert client.get(path).status_code == 404, path
        for path in FOLLOW_POSTS:
            assert client.post(path, headers=SAME_ORIGIN).status_code == 404, path
        # Every other read is still open, which is the other half of the rule.
        assert client.get(f"{ROOT}/api/overview").status_code == 200
        registered = {str(getattr(r, "path", "")) for r in client.app.routes}
        assert not (registered & set(WRITE_ROUTES))


def test_the_follow_write_routes_are_declared(tmp_path: Path) -> None:
    """§2.5.4: one list, declared once, and it is the whole non-GET surface."""
    with owner_client(tmp_path) as client:
        routes = [
            r for r in client.app.routes if str(getattr(r, "path", "")).startswith(ROOT)
        ]
        writing = {str(r.path) for r in routes if set(r.methods or ()) - {"GET", "HEAD"}}
        assert writing == set(WRITE_ROUTES)
        assert f"{ROOT}/following" in writing
        assert f"{ROOT}/following/{{slug}}/queue" in writing
        # And the detail *read* is a read: no state-changing GET, ever (§3.3).
        detail = [r for r in routes if str(r.path) == f"{ROOT}/api/following/{{slug}}"]
        assert detail and set(detail[0].methods or ()) - {"HEAD"} == {"GET"}


# ------------------------------------------------------------ 2. the two pages


# -------------------------------------------------------------- 3. the writes


def test_a_write_from_another_origin_is_refused(tmp_path: Path) -> None:
    """§3.3: the session cookie is ambient, so the Origin check carries it."""
    with owner_client(tmp_path) as client:
        sign_in(client)
        for path in FOLLOW_POSTS:
            refused = client.post(path, headers=CROSS_ORIGIN, data={"action": "pause"})
            assert refused.status_code == 403, path
            assert "E_BAD_ORIGIN" in refused.text
        # A cookie with no fetch metadata at all is refused too: that is
        # exactly the shape a cross-site form POST would have.
        assert client.post(f"{ROOT}/following/karpathy/check").status_code == 403


def test_a_write_without_a_credential_is_refused(tmp_path: Path) -> None:
    with owner_client(tmp_path) as client:
        for path in FOLLOW_POSTS:
            refused = client.post(path, headers=SAME_ORIGIN)
            assert refused.status_code == 401, path


def test_create_pause_resume_and_unfollow_round_trip(tmp_path: Path) -> None:
    """The four state writes, each through `follow_channel`, and the row after.

    POST → 303 throughout on this branch: a write is never the thing a reload
    repeats, and the target is a path the front end serves. What each write
    *answered* is `test_dashboard_writes_json.py`'s; what this pins is that the
    store moved, read back through the surface's own read.
    """
    with _empty_corpus(tmp_path) as client:
        sign_in(client)

        created = client.post(
            f"{ROOT}/following",
            data={
                "url": "https://www.youtube.com/@karpathy",
                "title": "Andrej Karpathy",
                "tab_videos": "1",
                "min_duration": "8:00",
                "max_per_check": "5",
                "mode": "auto",
                "channel_transcript": "1",
                "channel_ocr": "1",
                "channel_frames": "1",
            },
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert created.status_code == 303, created.text
        target = created.headers["location"]
        assert target.startswith(f"{ROOT}/following/")
        slug = target.rsplit("/", 1)[-1]

        def follow() -> dict:
            response = client.get(f"{ROOT}/api/following/{slug}", headers=BEARER)
            assert response.status_code == 200, response.text
            return response.json()["follow"]

        assert follow()["min_duration_s"] == 480
        assert follow()["state"] == "active"

        paused = client.post(
            f"{ROOT}/following/{slug}/state",
            data={"action": "pause"},
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert paused.status_code == 303
        assert follow()["state"] == "paused"

        resumed = client.post(
            f"{ROOT}/following/{slug}/state",
            data={"action": "resume"},
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert resumed.status_code == 303
        assert follow()["state"] == "active"

        # Check now moves the clock and nothing else; a check is a job the
        # queue claims on its next tick, and this suite never runs one.
        checked = client.post(
            f"{ROOT}/following/{slug}/check", headers=SAME_ORIGIN, follow_redirects=False
        )
        assert checked.status_code == 303

        gone = client.post(
            f"{ROOT}/following/{slug}/delete",
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert gone.status_code == 303
        assert gone.headers["location"] == f"{ROOT}/following"
        assert client.get(f"{ROOT}/api/following/{slug}", headers=BEARER).status_code == 404


def test_the_rules_form_edits_through_the_shared_validator(tmp_path: Path) -> None:
    """§5.5: the form adds no policy — `build_rules` is the policy.

    A refused value is refused in the validator's own words, and an accepted
    one comes back as the sentence the check will obey.
    """
    with owner_client(tmp_path) as client:
        sign_in(client)
        edited = client.post(
            f"{ROOT}/following/andrej-karpathy/rules",
            data={
                "tab_videos": "1",
                "tab_streams": "1",
                "min_duration": "20:00",
                "max_per_check": "3",
                "mode": "review",
                "channel_transcript": "1",
            },
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert edited.status_code == 303
        follow = client.get(
            f"{ROOT}/api/following/andrej-karpathy", headers=BEARER
        ).json()["follow"]
        assert follow["min_duration_s"] == 1200
        assert follow["max_per_check"] == 3
        assert follow["channels"] == "transcript"
        assert follow["mode"] == "review"

        # The floor the validator owns, refused in the validator's own words —
        # not clamped silently by the form.
        refused = client.post(
            f"{ROOT}/following/andrej-karpathy/rules",
            data={"tab_videos": "1", "check_interval_s": "60"},
            headers=SAME_ORIGIN,
        )
        assert refused.status_code == 400
        assert refused.json()["error"] == "E_BAD_PARAM"
        assert "at least 900 seconds" in refused.json()["message"]


def test_following_a_single_video_is_refused_by_the_tool(tmp_path: Path) -> None:
    """The dashboard reimplements none of the tool's policy, including this."""
    with _empty_corpus(tmp_path) as client:
        sign_in(client)
        refused = client.post(
            f"{ROOT}/following",
            data={"url": "https://youtu.be/kCc8FmEb1nY", "tab_videos": "1"},
            headers=SAME_ORIGIN,
        )
        assert refused.status_code == 400
        assert "a follow watches a channel or a playlist" in refused.text


def test_index_anyway_queues_exactly_one_video(tmp_path: Path) -> None:
    """The button that overrules a rule, once, without editing it.

    `expand=none`, so a row is one video and never a surprise expansion; the
    follow's own channels and tags, so a video rescued from the ledger is built
    the way the follow would have built it.
    """
    with owner_client(tmp_path) as client:
        sign_in(client)
        before = len(client.get(f"{ROOT}/api/jobs", headers=BEARER).json()["jobs"])
        queued = client.post(
            f"{ROOT}/following/andrej-karpathy/queue",
            data={"url": "https://youtu.be/nearmiss001"},
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert queued.status_code == 303
        job_id = queued.headers["location"].rsplit("/", 1)[-1]
        assert job_id.startswith("job_")

        job = client.get(f"{ROOT}/api/jobs/{job_id}", headers=BEARER).json()["job"]
        assert job["n_items"] == 1
        after = client.get(f"{ROOT}/api/jobs", headers=BEARER).json()["jobs"]
        assert len(after) == before + 1

        # The rule is unchanged: overruling it once is not editing it.
        rows = client.get(f"{ROOT}/api/following", headers=BEARER).json()["follows"]
        assert next(r for r in rows if r["slug"] == "andrej-karpathy")[
            "min_duration_s"
        ] == 480


def test_no_follow_write_is_reachable_by_a_get(tmp_path: Path) -> None:
    """§3.3: `SameSite=Lax` sends the cookie on a top-level GET navigation."""
    with owner_client(tmp_path) as client:
        sign_in(client)
        for path in FOLLOW_POSTS:
            # 404 rather than 405: `Mount("/")` is a full match for the path, so
            # the router never falls back to the method-mismatch answer the
            # POST-only route would have given. Either way nothing fires — and
            # `/following` is one of them now, because its page is Next's and
            # only the form's POST is still Python's (frontend-migration.md §1d).
            assert client.get(path, follow_redirects=False).status_code in (404, 405), path
        # The list read is a read, and it lives under `/api`.
        assert client.get(f"{ROOT}/api/following", headers=BEARER).status_code == 200


def test_the_kind_filter_offers_the_follow_check_job(tmp_path: Path) -> None:
    """`follow_check` is a `jobs.kind` since 0006, so it is a filter (§5.4)."""
    from vidtheque_mcp.dashboard.read_models import JOB_KINDS

    with owner_client(tmp_path) as client:
        assert "follow_check" in JOB_KINDS
        filtered = client.get(
            f"{ROOT}/api/jobs?kind=follow_check", headers=BEARER
        ).json()
        assert filtered["filters"]["kind"] == "follow_check"
        assert [job["job_id"] for job in filtered["jobs"]] == ["job_followchk1"]


