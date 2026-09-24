"""The management dashboard's surface — `docs/design/dashboard.md`.

The gate, the clamp policies, the rate buckets, the write-side registration
matrix and the projection: what this route group *is*, rather than what any one
endpoint answers with. The payload contracts are `test_dashboard_api.py`'s and
the write outcomes are `test_dashboard_writes_json.py`'s, and both of them build
their clients out of this file.

**It read markup until 2026-09-06.** Python served eleven pages here and half of
this suite was written against their HTML; the pages are Next's now and the
assertions that were about the surface rather than about the rendering are the
ones that stayed. Six that only the markup witnessed moved to the two files
above before the pages went — the commit that ported them names them.

Worker readiness reaches only the injected HTTP status stub; nothing here loads
a model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

import httpx2 as httpx
import pytest
from starlette.testclient import TestClient

from vidtheque_mcp.app import build_app
from vidtheque_mcp.config import Settings
from vidtheque_mcp.dashboard import ROOT, WRITE_ROUTES
from vidtheque_mcp.dashboard.settings import DashboardSettings
from vidtheque_mcp.dashboard.read_models import WORKER_STATUS_TIMEOUT_S
from vidtheque_mcp.db.connection import open_write_connection
from vidtheque_mcp.public.api import OWNER_CLAMPS, PUBLIC_CLAMPS
from vidtheque_mcp.public.settings import PublicSettings

from .conftest import A_VISITOR, FakeEmbeddings, rpc, rpc_headers, seed


# What was on someone's screen, and what yt-dlp said about it. Both are corpus
# strings and both are attacker-controlled in exactly the same way.
HOSTILE = '<script>alert(document.cookie)</script> <img src=x onerror=alert(1)>'

# What a read of this surface is, now that none of them is a page. `OVERVIEW`
# is the one behind the gate that takes no parameter, so it stands in wherever
# a test used to ask for `/dashboard` itself.
OVERVIEW = f"{ROOT}/api/overview"
LIBRARY = f"{ROOT}/api/library"


# --------------------------------------------------------------------- setup


def _corpus(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    if data.exists():  # a test that builds two apps seeds the corpus once
        return data
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    conn = open_write_connection(data / "vidtheque.db")
    try:
        conn.execute("BEGIN IMMEDIATE")
        # A video that never finished, with hostile metadata: the state the
        # table exists to show, carrying the text the page must not execute.
        conn.execute(
            "INSERT INTO videos (owner_id, source_id, url, title, channel_name, "
            "published_at, duration_s, index_state) VALUES "
            "(1, 'aaaaaaaaaaa', 'https://youtu.be/aaaaaaaaaaa', ?, ?, "
            "1740000000, 5400, 'indexing')",
            (f"Half-indexed {HOSTILE}", f"Channel {HOSTILE}"),
        )
        vid = int(
            conn.execute("SELECT id FROM videos WHERE source_id='aaaaaaaaaaa'").fetchone()[0]
        )
        conn.execute(
            "INSERT INTO video_stages (video_id, stage, state, model_key, started_at, "
            "finished_at) VALUES (?, 'fetch', 'done', 'yt-dlp-2026.07.04', 100, 142)",
            (vid,),
        )
        # A failed stage: model_key NULL, and an error string with a tag in it.
        conn.execute(
            "INSERT INTO video_stages (video_id, stage, state, model_key, started_at, "
            "finished_at, error) VALUES (?, 'stt', 'failed', NULL, 142, 447, ?)",
            (vid, f"ERROR: [youtube] Sign in to confirm you are not a bot. {HOSTILE}"),
        )
        # A deduplicated keyframe on the first seeded video, and an OCR line
        # whose text is hostile.
        first = int(
            conn.execute("SELECT id FROM videos WHERE source_id='kCc8FmEb1nY'").fetchone()[0]
        )
        keep = conn.execute(
            "SELECT id, jpeg_path FROM keyframes WHERE video_id=? ORDER BY ord LIMIT 1",
            (first,),
        ).fetchone()
        conn.execute(
            "INSERT INTO keyframes (video_id, ord, t_s, shot_id, shot_start_s, shot_end_s, "
            "phash, sharpness, width, height, jpeg_path, jpeg_bytes, dup_of, ocr_state) "
            "VALUES (?, 7, 700.0, 7, 700.0, 745.0, 77, 9.5, 1280, 720, ?, 4096, ?, 'skipped')",
            (first, keep["jpeg_path"], keep["id"]),
        )
        conn.execute(
            "UPDATE ocr_lines SET text = ? WHERE keyframe_id = ?", (HOSTILE, keep["id"])
        )
        _seed_jobs(conn)
        conn.execute("COMMIT")
    finally:
        conn.close()
    return data


def _seed_jobs(conn) -> None:  # type: ignore[no-untyped-def]
    """The three shapes the jobs view exists for (dashboard.md §5.4).

    A queued job held off by a `not_before` in the future — the countdown that
    was true and invisible during the overnight batch; a running job mid-item,
    with a retry already spent; and a finished one carrying both kinds of loss,
    the item that failed loudly and the item that succeeded with a stage
    missing underneath it.
    """
    videos = conn.execute("SELECT id, public_id FROM videos ORDER BY id").fetchall()

    # 1. Deferred. `not_before` is relative to now so the countdown is always
    #    in the future, whatever clock the test runs on.
    conn.execute(
        "INSERT INTO jobs (owner_id, public_id, kind, args_json, n_items, priority, "
        "state, not_before, created_at, error_code, error_message) VALUES "
        "(1, 'job_deferred01', 'index', '{}', 1, 100, 'queued', unixepoch() + 240, "
        "unixepoch() - 900, 'E_RATE_LIMIT', ?)",
        ("the source rate-limited this box; cookiefile /home/dev/.cookies.txt",),
    )
    deferred = int(
        conn.execute("SELECT id FROM jobs WHERE public_id='job_deferred01'").fetchone()[0]
    )
    conn.execute(
        "INSERT INTO job_items (job_id, seq, source_url, state, attempts, started_at) "
        "VALUES (?, 0, 'https://youtu.be/deferredvid', 'queued', 2, unixepoch() - 880)",
        (deferred,),
    )
    conn.execute(
        "INSERT INTO job_events (job_id, at, level, message) VALUES "
        "(?, unixepoch() - 300, 'warn', ?)",
        (deferred, f"retrying in 300s after E_RATE_LIMIT: HTTP 429 {HOSTILE}"),
    )

    # 2. Running, mid-item, second attempt.
    conn.execute(
        "INSERT INTO jobs (owner_id, public_id, kind, args_json, n_items, priority, "
        "state, created_at, started_at, heartbeat_at) VALUES "
        "(1, 'job_running001', 'index', '{}', 2, 50, 'running', unixepoch() - 1200, "
        "unixepoch() - 1100, unixepoch() - 4)"
    )
    running = int(
        conn.execute("SELECT id FROM jobs WHERE public_id='job_running001'").fetchone()[0]
    )
    conn.execute(
        "INSERT INTO job_items (job_id, seq, source_url, video_id, state, stage, "
        "stage_pct, attempts, started_at) VALUES (?, 0, ?, ?, 'running', 'stt', 0.42, 2, "
        "unixepoch() - 690)",
        (running, f"https://youtu.be/{videos[0]['public_id']}", videos[0]["id"]),
    )
    conn.execute(
        "INSERT INTO job_items (job_id, seq, source_url, state) VALUES "
        "(?, 1, 'https://youtu.be/queuedvideo', 'queued')",
        (running,),
    )

    # 3. Finished: one loud failure, one silent one.
    conn.execute(
        "INSERT INTO jobs (owner_id, public_id, kind, args_json, n_items, priority, "
        "state, created_at, started_at, finished_at) VALUES "
        "(1, 'job_finished01', 'index', '{}', 2, 100, 'failed', unixepoch() - 8000, "
        "unixepoch() - 7900, unixepoch() - 6400)"
    )
    finished = int(
        conn.execute("SELECT id FROM jobs WHERE public_id='job_finished01'").fetchone()[0]
    )
    conn.execute(
        "INSERT INTO job_items (job_id, seq, source_url, video_id, state, attempts, "
        "started_at, finished_at) VALUES (?, 0, ?, ?, 'done', 1, unixepoch() - 7900, "
        "unixepoch() - 7000)",
        (finished, f"https://youtu.be/{videos[2]['public_id']}", videos[2]["id"]),
    )
    conn.execute(
        "INSERT INTO job_items (job_id, seq, source_url, state, attempts, error_code, "
        "error_message, started_at, finished_at) VALUES "
        "(?, 1, 'https://youtu.be/failedvideo', 'failed', 3, 'E_SOURCE', ?, "
        "unixepoch() - 7000, unixepoch() - 6400)",
        (finished, f"ERROR: [youtube] Sign in to confirm you are not a bot. {HOSTILE}"),
    )
    # `done` item, failed stage: n_failed is 0 and a search channel is missing.
    conn.execute(
        "INSERT OR REPLACE INTO video_stages (video_id, stage, state, model_key, "
        "started_at, finished_at, error) VALUES (?, 'ocr', 'failed', NULL, "
        "unixepoch() - 7200, unixepoch() - 7100, ?)",
        (videos[2]["id"], "worker returned 503 for 41 frames; on-screen text is missing"),
    )


def _settings(tmp_path: Path, **kwargs) -> Settings:
    values = dict(
        data_dir=_corpus(tmp_path),
        public_url="http://localhost:8080",
        worker_url="http://worker:8081",
        secret="test-secret",
        # See mcp/tests/conftest.py: the shipped relevance floors are
        # deliberately open pending recalibration, and the fixture's
        # stand-in vectors have no geometry to calibrate against.
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
    public: PublicSettings | None = None,
    dashboard: DashboardSettings | None = None,
    worker_handler: Callable[[httpx.Request], httpx.Response] | None = None,
    worker_url: str = "http://worker:8081",
) -> TestClient:
    settings = _settings(
        tmp_path, auth_mode=auth_mode, static_token=token, worker_url=worker_url
    )  # type: ignore[arg-type]
    worker_http = httpx.AsyncClient(
        transport=httpx.MockTransport(worker_handler or _worker_down)
    )
    app = build_app(
        settings,
        embeddings=FakeEmbeddings(),
        run_pipeline=False,
        public=public or PublicSettings(enabled=False),
        dashboard=dashboard or DashboardSettings(),
        worker_status_http=worker_http,
    )
    return TestClient(app, base_url="http://localhost:8080")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    with make_client(tmp_path) as c:
        yield c


# ----------------------------------------------------- 1. the clamp policies


def test_the_owner_policy_is_wider_than_the_public_one_and_still_bounded() -> None:
    """dashboard.md §2.5.1/§5.2. Wider, never unbounded."""
    assert OWNER_CLAMPS.videos_max_limit == 100
    assert OWNER_CLAMPS.videos_default_limit == 50
    assert OWNER_CLAMPS.search_max_limit == 50
    # The owner *is* the "owner's agent" the full-transcript hatch was written
    # for, so `max_text_chars` is the caller's, clamped by the tool.
    assert OWNER_CLAMPS.search_text_chars is None
    assert PUBLIC_CLAMPS.search_text_chars == 400
    for field in ("videos_max_limit", "search_max_limit", "offset_max"):
        assert getattr(OWNER_CLAMPS, field) > getattr(PUBLIC_CLAMPS, field)


def test_one_set_of_handlers_serves_both_prefixes(tmp_path: Path) -> None:
    """The dashboard's JSON is `/api`'s handlers at another path, not a copy.

    **Amended in phase 5.** This test used to read the two prefixes' *clamps*
    as the proof they were different route groups sharing handlers, and that
    reading is what the clamp bug looked like from inside the suite: the demo
    deployment is `AUTH=none`, so both of these requests are anonymous, and an
    anonymous caller now gets the demo's bounds whichever prefix it knocked on.
    What makes them one implementation is the payload *shape*; the bounds are
    section 10's subject.
    """
    with make_client(tmp_path, public=PublicSettings(enabled=True)) as client:
        public = client.get("/api/videos").json()
        owner = client.get("/dashboard/api/videos").json()
    assert set(public) == set(owner) == {"videos", "pagination"}
    assert public["videos"] == owner["videos"]
    assert public["pagination"]["limit"] == owner["pagination"]["limit"] == 24


def test_the_dashboard_json_is_clamped_server_side(client: TestClient) -> None:
    """`?limit=100000` is clamped, not honoured — whoever is asking.

    The `client` fixture is `AUTH=none`, i.e. anonymous, so the ceiling it
    meets is the public one. The owner's ceiling is asserted in section 10;
    what matters here is that *neither* is the number in the URL.
    """
    payload = client.get("/dashboard/api/videos?limit=100000").json()
    assert payload["pagination"]["limit"] == 50  # not 100000
    search = client.get("/dashboard/api/search?q=cache&limit=999").json()
    assert search["pagination"]["limit"] == 20
    assert client.get("/dashboard/api/meta").json()["clamps"]["policy"] == "public"


def test_the_dashboard_json_is_the_private_mode_facade(client: TestClient) -> None:
    """demo-site.md §7.4, delivered: JSON without turning the demo on."""
    assert client.get("/api/videos").status_code == 404
    assert client.get("/dashboard/api/videos").status_code == 200
    # Not the spend surface, though: `ask` stays a public-mode route.
    assert client.post("/dashboard/api/ask", json={"q": "x"}).status_code == 404


def test_the_demo_facade_is_unchanged_by_the_refactor(tmp_path: Path) -> None:
    with make_client(tmp_path, public=PublicSettings(enabled=True)) as client:
        assert client.get("/api/videos?limit=999").json()["pagination"]["limit"] == 50
        payload = client.get("/api/search?q=cache&limit=999&max_text_chars=0").json()
        assert payload["pagination"]["limit"] == 20
        assert all(len(hit["text"]) <= 400 + 80 for hit in payload["results"])


# ------------------------------------------------------------ 2. the auth gate


def test_none_mode_serves_the_read_only_subset(client: TestClient) -> None:
    """`none` is already open through /mcp and /frames; a gate here is theatre.

    What `none` does *not* get is a write side. Phase 1 registered none at all
    because there were none; phase 3 registers none *because the mode has no
    credential to check* — an unauthenticated instance behind a tunnel with a
    live "index this URL" button is remote-yt-dlp-as-a-service (§3.2 rule 3).
    """
    for path in (OVERVIEW, LIBRARY, f"{LIBRARY}/kCc8FmEb1nY"):
        assert client.get(path).status_code == 200
    registered = {str(getattr(r, "path", "")) for r in client.app.routes}
    assert not (registered & set(WRITE_ROUTES))


def test_token_mode_refuses_every_read_on_the_prefix(tmp_path: Path) -> None:
    """One refusal, one shape. The gate used to answer a browser with a
    rendered sign-in page and a script with the envelope; there is no page, so
    it is the envelope on both (§21, 2026-09-06)."""
    with make_client(tmp_path, auth_mode="token", token="s3cret") as client:
        for path in (OVERVIEW, f"{ROOT}/api/videos"):
            denied = client.get(path, headers={"Accept": "text/html"})
            assert denied.status_code == 401, path
            assert denied.json()["error"] == "E_AUTH_REQUIRED"
            assert "Authorization: Bearer" in denied.json()["next"]
            # No corpus leaks through the refusal.
            assert "kCc8FmEb1nY" not in denied.text


def test_token_mode_accepts_the_bearer(tmp_path: Path) -> None:
    with make_client(tmp_path, auth_mode="token", token="s3cret") as client:
        headers = {"Authorization": "Bearer s3cret"}
        assert client.get(OVERVIEW, headers=headers).status_code == 200
        assert client.get(f"{ROOT}/api/videos", headers=headers).status_code == 200
        wrong = {"Authorization": "Bearer wrong"}
        assert client.get(OVERVIEW, headers=wrong).status_code == 401


def test_token_mode_accepts_the_existing_session_cookie(tmp_path: Path) -> None:
    """The same cookie, the same table — and, from phase 3, a place to write it.

    `token` mode had no `AuthStore` at all in phase 1, so a cookie could not be
    a credential there however valid it looked. It has one now (§3.2 rule 2),
    because the login page needs somewhere to put a `login_sessions` row — and
    a value that is not in that table is still not a credential.
    """
    import time

    from vidtheque_mcp.auth.login import SESSION_COOKIE

    with make_client(tmp_path, auth_mode="token", token="s3cret") as client:
        store = client.app.state.assembled.auth.store
        assert store is not None  # phase 3: `token` mode carries the session store
        client.cookies.set(SESSION_COOKIE, "anything")
        assert client.get(OVERVIEW).status_code == 401

        store.save_session("sid-1", "owner", int(time.time()) + 600)
        client.cookies.set(SESSION_COOKIE, "sid-1")
        assert client.get(OVERVIEW).status_code == 200
        client.cookies.set(SESSION_COOKIE, "sid-nope")
        assert client.get(OVERVIEW).status_code == 401


def test_an_expired_session_is_not_a_credential(tmp_path: Path) -> None:
    """§9's `VIDTHEQUE_DASHBOARD_SESSION_TTL_S`, from the far side of it."""
    import time

    from vidtheque_mcp.auth.login import SESSION_COOKIE

    with make_client(tmp_path, auth_mode="token", token="s3cret") as client:
        store = client.app.state.assembled.auth.store
        assert store is not None
        store.save_session("fresh", "owner", int(time.time()) + 600)
        store.save_session("stale", "owner", int(time.time()) - 1)
        client.cookies.set(SESSION_COOKIE, "fresh")
        assert client.get(OVERVIEW).status_code == 200
        client.cookies.set(SESSION_COOKIE, "stale")
        assert client.get(OVERVIEW).status_code == 401


def test_no_dashboard_route_is_state_changing(client: TestClient) -> None:
    """§3.3: SameSite=Lax sends the cookie on a top-level GET navigation.

    Every route in the group is GET-only *and* read-only, so there is nothing
    for an `<img src=…>` in some other page to fire.
    """
    routes = [r for r in client.app.routes if str(getattr(r, "path", "")).startswith(ROOT)]
    assert routes, "the dashboard registered no routes"
    for route in routes:
        assert set(route.methods or set()) <= {"GET", "HEAD"}, route.path


async def test_the_write_guard_refuses_before_it_is_ever_wired(tmp_path: Path) -> None:
    """Phase 3's guard, tested at phase 1 — it is not dead, it is early.

    The two rules it enforces are the ones that get retrofitted badly: `none`
    mode has no write side at all, and every write checks its Origin.
    """
    import json as json_

    from starlette.requests import Request

    from vidtheque_mcp.dashboard.access import origin_ok, require_write

    def request(app, headers: dict[str, str], method: str = "POST") -> Request:
        return Request(
            {
                "type": "http",
                "method": method,
                "path": f"{ROOT}/index",
                "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
                "app": app,
            }
        )

    with make_client(tmp_path) as client:  # auth=none
        refusal = await require_write(request(client.app, {}))
        assert refusal is not None and refusal.status_code == 403
        assert b"VIDTHEQUE_AUTH=token" in refusal.body

    with make_client(tmp_path, auth_mode="token", token="s3cret") as client:
        app = client.app
        # No credential.
        assert (await require_write(request(app, {}))).status_code == 401  # type: ignore[union-attr]
        good = {"Authorization": "Bearer s3cret"}
        # Credential, right origin — both ways of saying it.
        assert await require_write(request(app, {**good, "Origin": "http://localhost:8080"})) is None
        assert await require_write(request(app, {**good, "Sec-Fetch-Site": "same-origin"})) is None
        # Credential, wrong origin.
        bad = await require_write(request(app, {**good, "Origin": "https://evil.example"}))
        assert bad is not None and bad.status_code == 403
        assert json_.loads(bad.body)["error"] == "E_BAD_ORIGIN"
        # `Sec-Fetch-Site` is the browser's own answer and outranks a header the
        # page could have chosen for itself.
        cross = await require_write(
            request(app, {**good, "Sec-Fetch-Site": "cross-site",
                          "Origin": "http://localhost:8080"})
        )
        assert cross is not None and cross.status_code == 403
        # A caller with no ambient credential is not a CSRF victim.
        assert origin_ok(request(app, {}))


def test_the_route_group_can_be_turned_off(tmp_path: Path) -> None:
    with make_client(tmp_path, dashboard=DashboardSettings(enabled=False)) as client:
        for path in (OVERVIEW, LIBRARY, f"{ROOT}/api/videos", f"{ROOT}/"):
            assert client.get(path).status_code == 404


# ------------------------------------------------------------- 3. the pages


def test_pipeline_readiness_reads_worker_status_over_bounded_http(
    tmp_path: Path,
) -> None:
    """One probe, bounded, and only the three fields this surface publishes."""
    requests: list[httpx.Request] = []

    def worker(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "backends": [
                    {
                        "task": "embed",
                        "model": "served/text-model",
                        "loaded": True,
                    },
                    {
                        "task": "image_embed",
                        "model": "served/frame-model",
                        "loaded": False,
                    },
                    {"task": "ocr", "model": "served/ocr-model", "loaded": True},
                ],
                # Operator-only fields returned by /status but not needed here.
                "vram": {"used_mb": 9999},
                "queue": {"depth": 4},
            },
        )

    with owner_client(tmp_path, worker_handler=worker) as client:
        body = client.get(OVERVIEW, headers=BEARER).json()

    # One request, to one path, inside a wall-clock budget the read cannot
    # exceed however slow the worker is.
    assert [request.url.path for request in requests] == ["/status"]
    assert WORKER_STATUS_TIMEOUT_S <= 1.0

    readiness = body["readiness"]
    assert readiness["mcp"] == "ready" and readiness["database"] == "ready"
    assert readiness["worker"]["state"] == "ready"
    served = {model["model"]: model["loaded"] for model in readiness["worker"]["models"]}
    assert served["served/text-model"] is True
    assert served["served/frame-model"] is False
    assert isinstance(readiness["checked_at"], int)
    # The operator-only half of `/status` is not read and cannot be sent.
    raw = json.dumps(body)
    assert "9999" not in raw and "vram" not in raw


def test_pipeline_readiness_degrades_without_delaying_or_breaking_the_read(
    tmp_path: Path,
) -> None:
    def timed_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("late", request=request)

    with owner_client(tmp_path, worker_handler=timed_out) as client:
        worker = client.get(OVERVIEW, headers=BEARER).json()["readiness"]["worker"]
        # The rest of the surface is unaffected by a worker that never answers.
        assert client.get(LIBRARY, headers=BEARER).status_code == 200
    assert worker["state"] == "unavailable"
    # The sentence is policy text and stays Python's: the state is a word a
    # client colours from, and the why is a sentence it prints.
    assert worker["detail"] == "The worker did not answer its status check."

    with owner_client(tmp_path, worker_url="") as client:
        worker = client.get(OVERVIEW, headers=BEARER).json()["readiness"]["worker"]
    assert worker["state"] == "unconfigured"
    assert "No worker URL is configured" in worker["detail"]


# -------------------------------------------------------- 3b. §5.4 the jobs


def test_an_unknown_job_is_a_typed_404(client: TestClient) -> None:
    missing = client.get(f"{ROOT}/api/jobs/job_nope")
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-store"
    assert missing.json()["error"] == "E_UNKNOWN_JOB"
    assert missing.json()["next"]


def test_the_poll_target_says_when_to_stop(client: TestClient) -> None:
    """§5.4: 2 s while anything is live, stopped when nothing is."""
    payload = client.get(f"{ROOT}/api/jobs").json()
    assert payload["poll_ms"] == 2000
    assert payload["live"] is True  # two of the three jobs are queued/running
    assert payload["pagination"]["limit"] == 25
    deferred = next(j for j in payload["jobs"] if j["job_id"] == "job_deferred01")
    assert 200 < deferred["defer_s"] <= 240
    # Nothing live in this filter, so a tab watching it stops.
    done = client.get(f"{ROOT}/api/jobs?state=done").json()
    assert done["live"] is False

    single = client.get(f"{ROOT}/api/jobs/job_running001").json()
    assert single["live"] is True
    assert single["job"]["state"] == "running"
    assert [(i["attempts"], i["max_attempts"]) for i in single["items"]] == [
        (2, 3),
        (0, 3),
    ]
    assert single["items"][0]["stage"] == "stt"
    assert single["items"][0]["stage_pct"] == 42
    assert client.get(f"{ROOT}/api/jobs?limit=100000").json()["pagination"]["limit"] == 100


def test_the_same_secrets_do_not_come_back_through_the_mcp_tools(
    tmp_path: Path,
) -> None:
    """The redaction the test below asserts, defeated by quoting a job id.

    The jobs view publishes its ids, deliberately — and `job-status` is
    annotated read-only, so the public mask keeps it registered. A visitor read
    an id off `/dashboard/jobs`, called the tool through `/mcp`, and got back
    exactly the two fields the surface had just withheld. `corpus-summary` did
    the same with `video_stages.error`.

    Nothing tested this pairing, which is why it survived: §2.5's greps only
    ever read dashboard HTML. This is the missing half. (2026-08-10 audit, F-4.)
    """
    secrets = (
        "youtu.be/failedvideo",
        "Sign in to confirm you are not a bot",
        "cookiefile",
        "worker returned 503",
        "retrying in 300s",
    )

    def tool(client: TestClient, name: str, arguments: dict) -> str:
        body = rpc("tools/call", {"name": name, "arguments": arguments})
        response = client.post(
            "/mcp", json=body, headers=rpc_headers("tools/call", name=name)
        )
        assert response.status_code == 200, response.text
        return json.dumps(response.json())

    with make_client(tmp_path, public=PublicSettings(enabled=True)) as demo:
        printed = tool(demo, "job-status", {"job_id": "job_finished01"})
        for secret in secrets:
            assert secret not in printed, f"{secret} leaked through job-status"
        # The code is what a reader can act on, and it stays.
        assert "E_SOURCE" in printed
        gaps = tool(demo, "corpus-summary", {"include_gaps": True})
        for secret in secrets:
            assert secret not in gaps, f"{secret} leaked through corpus-summary"

    # The owner's instance is the contrast: the tool exists to say this.
    with make_client(tmp_path) as owner:
        printed = tool(owner, "job-status", {"job_id": "job_finished01"})
        assert "Sign in to confirm you are not a bot" in printed


def test_the_demo_projection_keeps_the_clocks_and_drops_the_rest(
    tmp_path: Path,
) -> None:
    """§2.4 and §10.4, which pull in opposite directions and both hold.

    The demo keeps the jobs view because its stated purpose is showing a
    visitor what indexing a video costs in time. It drops exactly two things:
    source URLs, which are `args_json` by another name, and error text, which
    is yt-dlp talking about the operator's own box.
    """
    secrets = (
        "youtu.be/failedvideo",
        "Sign in to confirm you are not a bot",
        "cookiefile",
        "worker returned 503",
        "retrying in 300s",
    )
    with make_client(tmp_path, public=PublicSettings(enabled=True)) as demo:
        for path in (
            f"{ROOT}/api/jobs",
            f"{ROOT}/api/jobs/job_finished01",
            f"{ROOT}/api/jobs/job_deferred01",
        ):
            raw = json.dumps(demo.get(path).json())
            for secret in secrets:
                assert secret not in raw, f"{secret} leaked on {path}"
        single = demo.get(f"{ROOT}/api/jobs/job_finished01").json()
        assert single["job"]["error_message"] is None
        assert all(item["source_url"] is None for item in single["items"])
        assert all(event["message"] is None for event in single["events"])
        # …and keeps the codes, the counts and every clock (§10.4).
        assert single["items"][1]["error_code"] == "E_SOURCE"
        assert single["items"][1]["took_s"] == 600
        assert single["job"]["wall_s"] == 1600 and single["job"]["ran_s"] == 1500
        assert single["counts"] == {"done": 1, "failed": 1}
        assert single["degraded"][0]["stage"] == "ocr"
        held = demo.get(f"{ROOT}/api/jobs/job_deferred01").json()
        assert 200 < held["job"]["defer_s"] <= 240

    # The owner's instance is the contrast: same read, both fields present.
    with make_client(tmp_path) as owner:
        seen = owner.get(f"{ROOT}/api/jobs/job_finished01").json()
        assert seen["items"][1]["source_url"] == "https://youtu.be/failedvideo"
        assert "Sign in to confirm you are not a bot" in seen["items"][1]["error_message"]
        assert "worker returned 503" in seen["degraded"][0]["error"]


# ------------------------------------------ 3c. relative frames, and the slash


async def test_the_mcp_surface_still_hands_out_absolute_frame_urls(
    assembled, fake_embeddings
) -> None:
    """The other half of the same assertion, at the tool layer."""
    from vidtheque_mcp.tools import frames

    result = await frames.run(assembled.deps, video_id="kCc8FmEb1nY", limit=2)
    payload = result.structured_content or {}
    urls = [f["url"] for f in payload.get("frames", [])]
    assert urls and all(u.startswith("http://localhost:8080/frames/") for u in urls)


def test_the_trailing_slash_redirects_rather_than_404ing(client: TestClient) -> None:
    """`Mount("/")` matches everything, so Starlette's own redirect never fires."""
    response = client.get(f"{ROOT}/", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == ROOT
    # The query survives, so a bookmarked filter with a stray slash still works.
    with_query = client.get(f"{ROOT}/?index_state=failed", follow_redirects=False)
    assert with_query.headers["location"] == f"{ROOT}?index_state=failed"
    # Where it lands is the front end's; this server only declines to 404 the
    # slash, and `Mount("/")` answers the target itself.
    assert client.get(f"{ROOT}/").status_code == 404


def test_nothing_on_this_surface_is_cacheable(client: TestClient) -> None:
    """Every response describes state that changes under the reader."""
    for path in (OVERVIEW, LIBRARY, f"{LIBRARY}/kCc8FmEb1nY", f"{ROOT}/api/jobs"):
        assert client.get(path).headers["cache-control"] == "no-store", path


def test_there_is_no_asset_route_and_the_faces_are_still_the_record(
    client: TestClient,
) -> None:
    """`/dashboard/static/*` went with the pages it existed for (2026-09-06).

    It served one stylesheet, two scripts and a `fonts/` alias onto
    `public/static/fonts` — the document of record for the two faces (DESIGN.md,
    *Fonts — one canonical location*). The web app self-hosts its fonts and
    `test_web_assets.py` diffs its copies against that directory, so the
    directory stays and the route that aliased it does not.
    """
    for path in (
        f"{ROOT}/static/dashboard.css",
        f"{ROOT}/static/dashboard.js",
        f"{ROOT}/static/jobs.js",
        f"{ROOT}/static/fonts/archivo-latin-wght-normal.woff2",
        f"{ROOT}/static/../../config.py",
    ):
        assert client.get(path).status_code == 404, path

    package = Path(__file__).resolve().parents[1] / "src/vidtheque_mcp/dashboard"
    assert not (package / "static").exists()
    assert not (package / "templates").exists()

    import vidtheque_mcp.public as _public

    fonts = Path(_public.__file__).parent / "static" / "fonts"
    assert (fonts / "archivo-latin-wght-normal.woff2").is_file()
    assert (fonts / "jetbrains-mono-latin-wght-normal.woff2").is_file()


# --------------------------------------------------- 4. no HTML, and no Jinja


def test_the_dashboard_package_renders_no_html_at_all() -> None:
    """The XSS section, reduced to the one assertion the deletion makes true.

    This file used to sweep every rendered page for a corpus string that had
    become markup, grep the templates for `| safe`, and scan the two scripts
    for `innerHTML`. All three were about a rendering that no longer happens
    here: every response is JSON, a JSON encoder escapes by construction, and
    what is left to assert is that nobody reintroduces the other thing.
    """
    package = Path(__file__).resolve().parents[1] / "src/vidtheque_mcp/dashboard"
    for module in sorted(package.rglob("*.py")):
        text = module.read_text()
        for forbidden in ("jinja2", "HTMLResponse", "<html", "<div", "innerHTML"):
            assert forbidden not in text, f"{module.name} reaches for {forbidden}"
    assert not list(package.rglob("*.html"))
    assert not list(package.rglob("*.js"))
    assert not list(package.rglob("*.css"))


# ------------------------------------------------------- 5. the rate limiter


def test_the_dashboard_bucket_is_installed_in_every_mode(tmp_path: Path) -> None:
    """dashboard.md §2.5.3: the limiter loses its mode conditional."""
    with make_client(tmp_path, dashboard=DashboardSettings(rate_per_min=2)) as client:
        assert client.get(OVERVIEW).status_code == 200
        assert client.get(LIBRARY).status_code == 200
        refused = client.get(f"{ROOT}/api/videos")
        assert refused.status_code == 429
        assert refused.json()["bucket"] == "dashboard"
        assert "retry-after" in refused.headers


def test_a_private_deployment_still_serves_frames_unbucketed(tmp_path: Path) -> None:
    """One detail read asks for ~48 frames; a 120/min bucket would refuse the
    second page load, and the owner is not that bucket's threat model."""
    with make_client(tmp_path, dashboard=DashboardSettings(rate_per_min=1)) as client:
        assert client.get(OVERVIEW).status_code == 200
        for _ in range(4):
            assert client.get("/frames/kCc8FmEb1nY-00000.jpg?w=192&q=70").status_code == 200


def test_the_public_buckets_are_untouched(tmp_path: Path) -> None:
    with make_client(
        tmp_path,
        public=PublicSettings(enabled=True, search_per_min=2),
        dashboard=DashboardSettings(enabled=False),
    ) as client:
        client.headers.update(A_VISITOR)
        assert client.get("/api/videos").status_code == 200
        assert client.get("/api/videos").status_code == 200
        refused = client.get("/api/videos")
        assert refused.status_code == 429
        assert refused.json()["bucket"] == "search"


def test_no_limiter_at_all_when_neither_surface_is_on(tmp_path: Path) -> None:
    from vidtheque_mcp.public import public_middleware

    assert public_middleware(PublicSettings(enabled=False), None) == []
    assert len(public_middleware(PublicSettings(enabled=False), 60)) == 1
    assert len(public_middleware(PublicSettings(enabled=True), None)) == 1


# ------------------------------------------------- 7. one writer, one layer


def test_the_dashboard_imports_nothing_from_the_worker() -> None:
    """CLAUDE.md's boundary rule, asserted rather than remembered."""
    package = Path(__file__).resolve().parents[1] / "src/vidtheque_mcp/dashboard"
    for module in package.rglob("*.py"):
        text = module.read_text()
        assert "vidtheque_worker" not in text, module
        # It calls the tools directly; it never speaks MCP to itself.
        assert "streamable_http" not in text, module
        assert "MCPServer" not in text, module


# ------------------------------------------------- 8. the write side (phase 3)

# The private deployment the write side is *for*: a credential to check, and
# the demo flag off. Everything in this section is measured against it, or
# against the two deployments that must not have a write side at all.
PASSWORD = "correct-horse"
TOKEN = "s3cret"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
SAME_ORIGIN = {"Origin": "http://localhost:8080"}


def owner_client(
    tmp_path: Path,
    *,
    readonly: bool = False,
    password: str | None = PASSWORD,
    worker_handler: Callable[[httpx.Request], httpx.Response] | None = None,
    worker_url: str = "http://worker:8081",
    **kwargs,
) -> TestClient:
    """`token` mode with a password — the deployment phase 3 is written for."""
    settings = _settings(
        tmp_path,
        auth_mode="token",
        static_token=TOKEN,
        password=password,
        worker_url=worker_url,
    )
    worker_http = httpx.AsyncClient(
        transport=httpx.MockTransport(worker_handler or _worker_down)
    )
    app = build_app(
        settings,
        embeddings=FakeEmbeddings(),
        run_pipeline=False,
        public=PublicSettings(enabled=readonly),
        dashboard=kwargs.pop("dashboard", None) or DashboardSettings(),
        worker_status_http=worker_http,
        **kwargs,
    )
    return TestClient(app, base_url="http://localhost:8080", **kwargs.pop("client", {}))


def sign_in(client: TestClient, secret: str = PASSWORD) -> None:
    response = client.post(
        f"{ROOT}/login",
        data={"password": secret},
        headers=SAME_ORIGIN,
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


WRITE_POSTS = (
    f"{ROOT}/index",
    f"{ROOT}/jobs/job_running001/cancel",
    f"{ROOT}/jobs/job_finished01/retry",
    f"{ROOT}/logout",
    f"{ROOT}/videos/kCc8FmEb1nY/reindex",
    f"{ROOT}/videos/kCc8FmEb1nY/tags",
)


# --- 8.1 what is registered, and where it is not


def test_the_write_side_is_absent_in_none_mode_not_merely_refused(
    tmp_path: Path,
) -> None:
    """§3.2 rule 3, as a status code: **404, not 403**.

    An unauthenticated instance behind a tunnel with a live "index this URL"
    button is remote-yt-dlp-as-a-service pointed at the operator's residential
    IP. `none` is the mode with no credential to check, so it gets no write
    routes — and no login page either, because a sign-in that grants nothing is
    a probe magnet with a password field on it.
    """
    with make_client(tmp_path) as client:  # auth=none
        for path in WRITE_POSTS:
            assert client.post(path).status_code == 404, path
        # And every read is still open, which is the other half of the rule.
        assert client.get(OVERVIEW).status_code == 200


def test_a_refusal_never_points_at_a_page_that_is_not_registered(
    tmp_path: Path,
) -> None:
    """The read gate's 401 offers the sign-in page only where there is one.

    Read-only plus `token` is a real deployment — a credentialed public mirror
    — and it gates its reads while having no write side at all. A refusal that
    sent that reader to `/dashboard/login` would be telling them a second
    untruth on the way out. The page is Next's now and the `POST` behind it is
    still not registered here, so the hint is still the thing that has to know.
    """
    with owner_client(tmp_path) as private:
        for path in (OVERVIEW, f"{ROOT}/api/videos"):
            refused = private.get(path)
            assert refused.status_code == 401, path
            assert f"{ROOT}/login" in refused.json()["next"], path

    with owner_client(tmp_path, readonly=True) as demo:
        for path in (OVERVIEW, f"{ROOT}/api/videos"):
            refused = demo.get(path)
            assert refused.status_code == 401, path
            assert "login" not in refused.json()["next"], path
            assert "Bearer" in refused.json()["next"], path


def test_the_write_side_is_absent_in_readonly_mode_not_merely_refused(
    tmp_path: Path,
) -> None:
    """§2.3, with a credential configured — so the *flag* is doing the work.

    This is the deployment Tom ships publicly: welcome page plus the read-only
    projection through a tunnel. The write side must be missing, not refusing.
    """
    with owner_client(tmp_path, readonly=True) as demo:
        assert demo.get(LIBRARY, headers=BEARER).status_code == 200
        for path in WRITE_POSTS:
            assert demo.post(path, headers={**BEARER, **SAME_ORIGIN}).status_code == 404
        assert demo.post(f"{ROOT}/login", headers=BEARER).status_code == 404
        # The following reads go with the writes, because their surface does
        # (§18.6): a JSON route that answered here would be the way back in.
        assert demo.get(f"{ROOT}/api/following", headers=BEARER).status_code == 404


def test_the_write_routes_are_declared_and_post_only(tmp_path: Path) -> None:
    """§2.5.4: one list, declared once, and it is the whole non-GET surface.

    A tenth write route that forgets to declare itself fails here rather than
    shipping unguarded — the equivalent of `public/readonly.py` deriving the
    masked tool set instead of listing it by hand.
    """
    with owner_client(tmp_path) as client:
        routes = [
            r for r in client.app.routes if str(getattr(r, "path", "")).startswith(ROOT)
        ]
        writing = {str(r.path) for r in routes if set(r.methods or ()) - {"GET", "HEAD"}}
        assert writing == set(WRITE_ROUTES)
        for route in routes:
            extra = set(route.methods or ()) - {"GET", "HEAD", "POST"}
            assert not extra, f"{route.path} answers {extra}"


def test_no_write_is_reachable_by_a_get(tmp_path: Path) -> None:
    """§3.3: `SameSite=Lax` sends the cookie on a top-level GET navigation, so
    an `<img src="…/reindex">` in any page the owner opens would fire."""
    with owner_client(tmp_path) as client:
        sign_in(client)
        for path in (
            f"{ROOT}/logout",
            f"{ROOT}/login",
            f"{ROOT}/jobs/job_running001/cancel",
            f"{ROOT}/jobs/job_finished01/retry",
            f"{ROOT}/videos/kCc8FmEb1nY/reindex",
            f"{ROOT}/videos/kCc8FmEb1nY/tags",
        ):
            # 404 rather than 405: `Mount("/")` is a full match for the path,
            # so the router never falls back to the method-mismatch answer the
            # POST-only route would have given. Either way nothing fires.
            assert client.get(path).status_code in (404, 405), path
        # `/index` and `/login` had a GET each and both were pages; the pages
        # are Next's, so every path in this group is a POST or a read (§21).
        for path in (f"{ROOT}/index", f"{ROOT}/login"):
            assert client.get(path).status_code in (404, 405), path


def test_cancel_keeps_its_303_and_lands_where_the_reader_can_see_it(
    tmp_path: Path,
) -> None:
    """The redirect branch, which is what a form navigation still takes.

    What each cancel *did* is `test_dashboard_writes_json.py`'s — this is the
    other half: the target is the job's own page, which Next serves.
    """
    with owner_client(tmp_path) as client:
        sign_in(client)
        running = client.post(
            f"{ROOT}/jobs/job_running001/cancel",
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert running.status_code == 303
        assert running.headers["location"] == f"{ROOT}/jobs/job_running001"

        deferred = client.post(
            f"{ROOT}/jobs/job_deferred01/cancel",
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert deferred.status_code == 303
        assert deferred.headers["location"] == f"{ROOT}/jobs/job_deferred01"

        # A settled job cannot be cancelled, and the refusal is the envelope on
        # this branch too (§21).
        finished = client.post(
            f"{ROOT}/jobs/job_finished01/cancel", headers=SAME_ORIGIN
        )
        assert finished.status_code == 400
        assert "already failed" in finished.json()["message"]


# --- 8.2 the auth matrix


def test_the_write_gate_takes_the_bearer_the_cookie_and_nothing_else(
    tmp_path: Path,
) -> None:
    path = f"{ROOT}/videos/kCc8FmEb1nY/tags"
    with owner_client(tmp_path) as client:
        # No credential at all: the typed 401.
        anonymous = client.post(path, data={"add": "topic:gate"}, headers=SAME_ORIGIN)
        assert anonymous.status_code == 401
        assert anonymous.json()["error"] == "E_AUTH_REQUIRED"

        # The script path.
        assert client.post(
            path, data={"add": "topic:gate"}, headers=BEARER, follow_redirects=False
        ).status_code == 303

        # A wrong bearer is not a credential.
        assert client.post(
            path,
            data={"add": "topic:gate"},
            headers={"Authorization": "Bearer nope", **SAME_ORIGIN},
        ).status_code == 401

        # The browser path.
        sign_in(client)
        assert client.post(
            path,
            data={"remove": "topic:gate"},
            headers=SAME_ORIGIN,
            follow_redirects=False,
        ).status_code == 303


def test_a_cookie_write_needs_positive_same_origin_evidence(tmp_path: Path) -> None:
    """The CSRF posture, in one test (§3.3 as phase 3 amends it).

    The write side is HTML forms, so "a cross-site form POST cannot reach the
    handler" is no longer true on its own. The rule that replaces it is
    asymmetric on purpose: the **ambient** credential needs a browser to vouch
    for the request, and a bearer — which no browser attaches by itself — does
    not.
    """
    path = f"{ROOT}/videos/kCc8FmEb1nY/tags"
    body = {"add": "topic:csrf"}
    with owner_client(tmp_path) as client:
        sign_in(client)
        for headers in (SAME_ORIGIN, {"Sec-Fetch-Site": "same-origin"}):
            assert client.post(
                path, data=body, headers=headers, follow_redirects=False
            ).status_code == 303

        for headers in (
            {"Origin": "https://evil.example"},
            # The browser's own answer outranks a header the page chose itself.
            {"Sec-Fetch-Site": "cross-site", **SAME_ORIGIN},
        ):
            refused = client.post(path, data=body, headers=headers)
            assert refused.status_code == 403
            assert refused.json()["error"] == "E_BAD_ORIGIN"

        # Neither header, with the cookie: refused. That is the shape a
        # cross-site form POST would have if SameSite ever failed to hold it.
        assert client.post(path, data=body).status_code == 403

    with owner_client(tmp_path) as script:
        # Neither header, with a bearer: allowed. curl is not a CSRF victim.
        assert script.post(
            path, data=body, headers=BEARER, follow_redirects=False
        ).status_code == 303


def test_trusted_cidrs_are_empty_by_default_and_are_the_socket_peer(
    tmp_path: Path,
) -> None:
    """§3.2's escape hatch, and §3.4's reason for shipping it switched off."""
    import ipaddress

    from vidtheque_mcp.dashboard.settings import DashboardSettings as DS

    assert DS().trusted_cidrs == ()
    assert DS.from_env().trusted_cidrs == ()

    lan = DS(trusted_cidrs=(ipaddress.ip_network("10.0.0.0/8"),))
    assert lan.trusts("10.4.4.4")
    assert not lan.trusts("192.168.1.1")
    assert not lan.trusts(None)
    assert not lan.trusts("not-an-address")

    def lan_app() -> object:
        # A fresh app per client: an MCP session manager's lifespan runs once.
        return build_app(
            _settings(tmp_path, auth_mode="token", static_token=TOKEN),
            embeddings=FakeEmbeddings(),
            run_pipeline=False,
            public=PublicSettings(enabled=False),
            dashboard=DashboardSettings(trusted_cidrs=lan.trusted_cidrs),
        )

    path = f"{ROOT}/videos/kCc8FmEb1nY/tags"
    inside = TestClient(
        lan_app(), base_url="http://localhost:8080", client=("10.9.9.9", 4444)
    )
    with inside:
        assert inside.post(
            path, data={"add": "topic:lan"}, headers=SAME_ORIGIN, follow_redirects=False
        ).status_code == 303

    outside = TestClient(
        lan_app(), base_url="http://localhost:8080", client=("203.0.113.7", 4444)
    )
    with outside:
        forged = outside.post(
            path,
            data={"add": "topic:lan"},
            headers={
                **SAME_ORIGIN,
                # The rate limiter trusts this header. Authorization must not:
                # any client can send it (demo-site.md §4.3).
                "CF-Connecting-IP": "10.9.9.9",
                "X-Forwarded-For": "10.9.9.9",
            },
        )
        assert forged.status_code == 401, "a header is not an address"


def test_a_trusted_peer_reads_the_surface_it_may_write_to(tmp_path: Path) -> None:
    """The gates are symmetric (2026-08-13, Tom's call on the field finding).

    §3.4 already granted a trusted peer the whole write side, and §4's policy
    table calls that peer an owner — but `guarded()` checked only the bearer
    and the session, so a LAN peer could submit an index job and be refused the
    read it posted from. A network trusted to change the corpus but not to read
    it is the "boundary with no shape" §3.4 names, now in both directions.
    Socket peer only, as everywhere else: the forged-header client outside the
    CIDR stays refused on every read here.
    """
    import ipaddress

    lan = DashboardSettings(trusted_cidrs=(ipaddress.ip_network("10.0.0.0/8"),))

    def lan_app() -> object:  # a fresh app per client: one lifespan each
        return build_app(
            _settings(tmp_path, auth_mode="token", static_token=TOKEN),
            embeddings=FakeEmbeddings(),
            run_pipeline=False,
            public=PublicSettings(enabled=False),
            dashboard=lan,
        )

    inside = TestClient(
        lan_app(), base_url="http://localhost:8080", client=("10.9.9.9", 4444)
    )
    with inside:
        # No credential presented: the peer is the credential.
        assert inside.get(OVERVIEW).status_code == 200
        assert inside.get(f"{ROOT}/api/videos").status_code == 200

    outside = TestClient(
        lan_app(), base_url="http://localhost:8080", client=("203.0.113.7", 4444)
    )
    with outside:
        forged = {"CF-Connecting-IP": "10.9.9.9", "X-Forwarded-For": "10.9.9.9"}
        assert outside.get(OVERVIEW, headers=forged).status_code == 401
        assert outside.get(f"{ROOT}/api/videos", headers=forged).status_code == 401


def test_a_cidr_that_covers_the_proxy_refuses_the_boot() -> None:
    """The 2026-08-09 review's MEDIUM, refused rather than logged (gate G2).

    `trusts()` reads the socket peer, which is right for a LAN and wrong behind
    a tunnel: cloudflared connects over loopback or a docker bridge, so a CIDR
    covering *that* makes every anonymous visitor an owner. A trusted-IP header
    is the tell that a proxy is in front, because it exists for exactly the
    reason the socket peer is not the client.

    Three conditions, and the third earns its own assertions below: the header
    *defaults* to `CF-Connecting-IP` and `.env.example` ships that value, so
    "a header is set" is also true of a LAN box that has never seen a proxy.
    Refusing on the first two alone took owner access away from the deployment
    §3.2 designed the allowlist for — caught by two existing tests in this file
    when the first version of this guard landed. A non-loopback public hostname
    is what distinguishes the exposed case, per B-2's own test.
    """
    import ipaddress

    import pytest

    from vidtheque_mcp.config import ConfigError
    from vidtheque_mcp.dashboard.settings import (
        DashboardSettings as DS,
        proxy_origin_cidrs,
        refuse_proxy_origin_cidrs,
    )

    loopback = DS(trusted_cidrs=(ipaddress.ip_network("127.0.0.1/32"),))
    docker = DS(trusted_cidrs=(ipaddress.ip_network("172.17.0.0/16"),))
    lan = DS(trusted_cidrs=(ipaddress.ip_network("10.0.0.0/8"),))
    routable = DS(trusted_cidrs=(ipaddress.ip_network("203.0.113.0/24"),))

    assert proxy_origin_cidrs(loopback) == ("127.0.0.1/32",)
    assert proxy_origin_cidrs(docker) == ("172.17.0.0/16",), "docker's own bridge"
    assert proxy_origin_cidrs(routable) == ()
    assert proxy_origin_cidrs(DS()) == ()

    public = ("vidtheque.example.com",)
    for settings in (loopback, docker, lan):
        with pytest.raises(ConfigError) as caught:
            refuse_proxy_origin_cidrs(settings, "CF-Connecting-IP", public)
        said = str(caught.value)
        assert "treated as the owner" in said, said
        assert str(settings.trusted_cidrs[0]) in said, "it must name the CIDR"
        assert "vidtheque.example.com" in said, "and the hostname it is exposed on"
        # The remedy is in the message: this is the one a reader can act on
        # without opening the source.
        assert "narrow the allowlist" in said, said

    # Boots: no proxy in front (the header is the documented way to say "trust
    # the socket only"), or an allowlist a proxy cannot be speaking from.
    for settings, header in ((loopback, ""), (routable, "CF-Connecting-IP")):
        refuse_proxy_origin_cidrs(settings, header, public)

    # And the case that made this a three-condition rule: a LAN deployment, on
    # the *default* header, with the allowlist that is its only credential.
    # No public hostname, so nothing here is exposed and nothing is refused.
    for hostnames in ((), ("localhost",), ("127.0.0.1",)):
        refuse_proxy_origin_cidrs(lan, "CF-Connecting-IP", hostnames)


def test_the_env_vars_this_phase_adds_are_documented() -> None:
    """CLAUDE.md: an env var without an entry in `deploy/.env.example` is a bug."""
    import os

    example = (Path(__file__).resolve().parents[2] / "deploy/.env.example").read_text()
    for var in ("VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS", "VIDTHEQUE_DASHBOARD_SESSION_TTL_S"):
        assert var in example, var

    # And the TTL is read, which is the half `config.py` was missing — the
    # session lifetime was hard-coded while every other tunable had a reader.
    os.environ["VIDTHEQUE_DASHBOARD_SESSION_TTL_S"] = "600"
    try:
        assert Settings.from_env().login_session_ttl_s == 600
    finally:
        del os.environ["VIDTHEQUE_DASHBOARD_SESSION_TTL_S"]


# --- 8.3 the login page


def test_signing_out_drops_the_row_not_just_the_cookie(tmp_path: Path) -> None:
    from vidtheque_mcp.auth.login import SESSION_COOKIE

    with owner_client(tmp_path) as client:
        sign_in(client)
        sid = client.cookies.get(SESSION_COOKIE)
        store = client.app.state.assembled.auth.store
        assert store is not None and store.load_session(sid) == "owner"

        out = client.post(f"{ROOT}/logout", headers=SAME_ORIGIN, follow_redirects=False)
        assert out.status_code == 303
        assert store.load_session(sid) is None, "a replayed cookie must be dead"

        # Replaying it by hand does not get back in.
        client.cookies.set(SESSION_COOKIE, sid)
        assert client.get(OVERVIEW).status_code == 401


def test_an_expired_browser_session_is_refused_in_one_shape(tmp_path: Path) -> None:
    """A form POST from a page whose cookie died gets the envelope.

    It used to get a 303 to the Jinja sign-in page. There is no Jinja sign-in
    page, so the 401 is the whole refusal in both media and it is the shell
    that decides to navigate (§21, 2026-09-05).
    """
    with owner_client(tmp_path) as client:
        refused = client.post(
            f"{ROOT}/videos/kCc8FmEb1nY/reindex",
            headers={**SAME_ORIGIN, "Accept": "text/html"},
            follow_redirects=False,
        )
        assert refused.status_code == 401
        assert refused.json()["error"] == "E_AUTH_REQUIRED"


# --- 8.4 the index form


def test_a_single_url_goes_straight_to_its_job(tmp_path: Path) -> None:
    with owner_client(tmp_path) as client:
        sign_in(client)
        response = client.post(
            f"{ROOT}/index",
            data={"urls": "https://youtu.be/solo0000001"},
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"].startswith(f"{ROOT}/jobs/job_")


# --- 8.5 the row actions


def test_re_index_queues_a_forced_job_for_this_video_only(tmp_path: Path) -> None:
    with owner_client(tmp_path) as client:
        sign_in(client)
        queued = client.post(
            f"{ROOT}/videos/zduSFxRajkE/reindex",
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert queued.status_code == 303
        job = queued.headers["location"].rsplit("/", 1)[-1]
        payload = client.get(f"{ROOT}/api/jobs/{job}").json()
        # It is one video, not the playlist the URL happened to be in.
        assert payload["job"]["kind"] == "reindex"
        assert payload["job"]["n_items"] == 1


def test_re_indexing_a_video_a_live_job_holds_is_the_tools_refusal(
    tmp_path: Path,
) -> None:
    """`kCc8FmEb1nY` is mid-`stt` in the fixture's running job.

    The button does not get to override that, and the surface does not invent
    its own wording for it: this is `index-video`'s `E_INDEXING`, verbatim.
    """
    with owner_client(tmp_path) as client:
        sign_in(client)
        held = client.post(f"{ROOT}/videos/kCc8FmEb1nY/reindex", headers=SAME_ORIGIN)
        assert held.status_code == 409
        assert held.json()["error"] == "E_INDEXING"


def test_re_indexing_an_unknown_video_is_a_typed_404(tmp_path: Path) -> None:
    with owner_client(tmp_path) as client:
        sign_in(client)
        missing = client.post(f"{ROOT}/videos/nope/reindex", headers=SAME_ORIGIN)
        assert missing.status_code == 404
        assert missing.json()["error"] == "E_UNKNOWN_VIDEO"


def test_tagging_calls_the_tool_and_keeps_its_rules(tmp_path: Path) -> None:
    """The 303 branch, and what the corpus holds afterwards.

    The outcome payload is `test_dashboard_writes_json.py`'s; what this pins is
    that a form navigation still lands on the video it tagged, and that the
    write reached the corpus rather than only the response.
    """
    with owner_client(tmp_path) as client:
        sign_in(client)
        detail = f"{ROOT}/videos/kCc8FmEb1nY"

        added = client.post(
            f"{detail}/tags",
            data={"add": "topic:attention, series:zero-to-hero"},
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert added.status_code == 303
        assert added.headers["location"] == f"{detail}#manage"
        row = client.get(f"{LIBRARY}/kCc8FmEb1nY").json()["video"]
        assert {"topic:attention", "series:zero-to-hero"} <= set(row["tags"])

        removed = client.post(
            f"{detail}/tags",
            data={"remove": "topic:attention"},
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert removed.status_code == 303
        after = client.get(f"{LIBRARY}/kCc8FmEb1nY").json()["video"]["tags"]
        assert "topic:attention" not in after and "series:zero-to-hero" in after

        # The tool's namespace rule, verbatim, and nothing applied.
        bad = client.post(
            f"{detail}/tags", data={"add": "Shouty:Tag"}, headers=SAME_ORIGIN
        )
        assert bad.status_code == 400
        assert bad.json()["error"] == "E_BAD_PARAM"
        assert "namespace" in bad.json()["next"]

        # An empty submission is not an error; it is a no-op that goes back.
        assert client.post(
            f"{detail}/tags", data={}, headers=SAME_ORIGIN, follow_redirects=False
        ).status_code == 303


def test_tagging_an_unknown_video_refuses_before_it_writes(tmp_path: Path) -> None:
    with owner_client(tmp_path) as client:
        sign_in(client)
        missing = client.post(
            f"{ROOT}/videos/nope/tags", data={"add": "topic:x"}, headers=SAME_ORIGIN
        )
        assert missing.status_code == 404
        assert missing.json()["error"] == "E_UNKNOWN_VIDEO"


# --- 8.6 the surface stays itself


def test_the_sign_in_write_has_its_own_much_tighter_bucket(tmp_path: Path) -> None:
    """The one path on this surface where a request is a guess at a secret.

    The loose `dashboard` bucket is written for a human clicking plus a polling
    tab; charging a password form against it would leave 120 guesses a minute
    on a box that is reachable through a tunnel.
    """
    from vidtheque_mcp.public import LOGIN_PER_MIN

    assert LOGIN_PER_MIN < 60
    with owner_client(tmp_path) as client:
        codes = [
            client.post(
                f"{ROOT}/login", data={"password": "guess"}, headers=SAME_ORIGIN
            ).status_code
            for _ in range(LOGIN_PER_MIN + 2)
        ]
        assert codes[0] == 401
        assert codes[-1] == 429
        # The other reads are on the loose bucket and are not collateral.
        assert client.get(LIBRARY).status_code in (200, 401)


# ------------------------------------------- 9. the projection (phase 4)

# `VIDTHEQUE_PUBLIC_READONLY=1` — the deployment Tom ships through the tunnel:
# the welcome page at `/`, the read-only projection at `/dashboard`, and no
# write side by two independent mechanisms (dashboard.md §2.4,
# docs/deploy-public.md §2.2). Everything in this section is measured against
# it *and* against the owner's instance, in the same test wherever the point is
# a difference — a redaction with no contrast beside it is a redaction that can
# quietly grow to cover the whole page and still pass.
DEMO = PublicSettings(enabled=True)

# The operator's own console, in the strings it would leak in. The model ids are
# `config` rows; the byte panel is a measurement of somebody's disk; `auth=` is
# an environment variable and its value.
OPERATOR_STRINGS = (
    "Qwen/Qwen3-VL-Embedding-2B",
    # The declared-models table. It had a panel heading of its own until the
    # 2026-08-13 readiness merge; it is pinned by its own column head and its
    # caption now, which is what the projection actually has to drop.
    "model declared",
    "The models this corpus was built with",
    "keyframe JPEGs",
    # The storage panel itself. It used to be pinned by a figure-note that read
    # "one SQLite file, one writer"; that note was self-narration and went in
    # the 2026-08-10 cull, so the panel is pinned by its own heading now.
    'id="storage">Storage',
    "auth=",
    "VIDTHEQUE_AUTH",
)

READ_ROUTES = (
    f"{ROOT}/api/overview",
    f"{ROOT}/api/ledger",
    f"{ROOT}/api/search?q=cache",
    f"{ROOT}/api/library",
    f"{ROOT}/api/library/kCc8FmEb1nY",
    f"{ROOT}/api/jobs",
    f"{ROOT}/api/jobs/job_finished01",
    f"{ROOT}/api/videos/kCc8FmEb1nY/cues",
)


def test_the_demo_serves_every_read_and_none_of_the_writes(tmp_path: Path) -> None:
    """§2.4's table, top to bottom, as one composed assertion.

    Phase 4's headline: the flag that used to gate only the demo page and
    `/api` now also decides which *dashboard* you get. Nothing here is new
    machinery — the write side has been absent under this flag since phase 3
    and the jobs redaction since phase 2 — so the thing worth asserting is the
    composition, which no single earlier test covered.
    """
    with make_client(tmp_path, public=DEMO) as demo:
        for path in READ_ROUTES:
            assert demo.get(path).status_code == 200, path
        # The corpus is still whole in the two reads §2.4 gives the demo
        # unredacted: every state, every video, the seven stages.
        table = demo.get(LIBRARY).json()
        assert {row["video_id"] for row in table["videos"]} >= {
            "aaaaaaaaaaa",
            "kCc8FmEb1nY",
        }
        assert all(len(row["coverage"]) == 3 for row in table["videos"])
        detail = demo.get(f"{LIBRARY}/kCc8FmEb1nY").json()
        assert [stage["stage"] for stage in detail["stages"]] == [
            "fetch", "stt", "chunk", "text_embed", "keyframe", "ocr", "frame_embed",
        ]
        # And the write side is absent, not refusing (§2.3).
        registered = {str(getattr(r, "path", "")) for r in demo.app.routes}
        assert not (registered & set(WRITE_ROUTES))
        assert demo.post(f"{ROOT}/login").status_code == 404
        assert demo.get(f"{ROOT}/api/following").status_code == 404


def test_the_demo_dashboard_is_charged_to_the_limiter_like_the_private_one(
    tmp_path: Path,
) -> None:
    """§6.8 and §2.5.3 — and now with the public buckets beside it.

    A read-only dashboard on a public hostname is the *more* exposed of the two
    deployments the `dashboard` bucket exists for, so the composed mode must
    not lose it to the public bucket map.
    """
    with make_client(
        tmp_path, public=DEMO, dashboard=DashboardSettings(rate_per_min=2)
    ) as demo:
        assert demo.get(OVERVIEW).status_code == 200
        assert demo.get(LIBRARY).status_code == 200
        refused = demo.get(f"{ROOT}/api/jobs")
        assert refused.status_code == 429
        assert refused.json()["bucket"] == "dashboard"
        # The demo's own buckets are untouched by it.
        assert demo.get("/api/videos").status_code == 200


def test_the_projection_still_goes_through_the_byte_capped_frame_cache(
    tmp_path: Path,
) -> None:
    """§6.4: three fixed widths, never base64, on the public surface too."""
    with make_client(tmp_path, public=DEMO) as demo:
        for path in (OVERVIEW, LIBRARY, f"{LIBRARY}/kCc8FmEb1nY"):
            raw = json.dumps(demo.get(path).json())
            assert "base64" not in raw and "data:image" not in raw
            for width in set(re.findall(r"/frames/[\w.-]+\.jpg\?w=(\d+)", raw)):
                assert int(width) in (192, 512, 1280), f"{width} on {path}"


# --- 9.1 codex gap 1: the queue, on the first screen (§5.1)


def test_the_queue_read_is_one_query_and_the_projection_keeps_it(
    tmp_path: Path,
) -> None:
    """It is a corpus-shaped fact, so it survives the demo (§2.4) — and it is
    one grouped statement, because the overview is the one read that answers
    with flat aggregates and four counts must not be four round trips."""
    from vidtheque_mcp.db.connection import open_read_connection
    from vidtheque_mcp.jobs import store as jobs_store

    conn = open_read_connection(_corpus(tmp_path) / "vidtheque.db")
    try:
        assert jobs_store.job_health(conn, 0) == {
            "active": 2,
            "running": 1,
            "deferred": 1,
            "failed_recent": 1,
        }
        # A window that excludes the failure reports zero rather than forever.
        assert jobs_store.job_health(conn, 2**31)["failed_recent"] == 0
    finally:
        conn.close()

    with make_client(tmp_path, public=DEMO) as demo:
        assert demo.get(OVERVIEW).json()["jobs"] == {
            "active": 2,
            "running": 1,
            "deferred": 1,
            "failed_recent": 1,
            "failed_window_s": 86_400,
        }


# --- 9.2 codex gap 2: the date filters (§5.2)


# --- 9.3 the welcome page's one link


def test_the_welcome_page_gains_its_link_into_the_browsable_corpus(
    tmp_path: Path,
) -> None:
    """§2.4: the demo page gains one link, and `/api/meta` is what decides.

    The link is not in the page's own gift. It is `browse` in `/api/meta`, so a
    deployment that turned the route group off — or the edge rule in
    `deploy/cloudflared.example.yml` that 404s `^/dashboard` — does not leave
    an invitation to a dead page in the masthead.

    Until 2026-09-05 this also read the hidden markup out of the served demo
    page. The page is the Next.js app's now and its half of this moved with it;
    the field it renders from is still Python's and is still asserted both ways.
    """
    with make_client(tmp_path, public=DEMO) as demo:
        assert demo.get("/api/meta").json()["browse"] == ROOT

    with make_client(
        tmp_path, public=DEMO, dashboard=DashboardSettings(enabled=False)
    ) as off:
        assert off.get("/api/meta").json()["browse"] is None
        assert off.get(OVERVIEW).status_code == 404


def test_the_browse_target_is_the_route_groups_own_root(tmp_path: Path) -> None:
    """One source of truth for the path, asserted rather than assumed."""
    from vidtheque_mcp.dashboard.settings import ROOT as DASHBOARD_ROOT

    with make_client(tmp_path, public=DEMO) as demo:
        browse = demo.get("/api/meta").json()["browse"]
    assert browse == DASHBOARD_ROOT
    assert browse.startswith("/") and not browse.startswith("//")


# ------------------------------- 10. credential-keyed clamps (phase 5, §2.4)


"""The clamp policy follows the credential, not the route group.

`docs/deploy-public.md` opened this as a policy question and phase 4 declined
to answer it: `/dashboard/api/*` was registered with `OWNER_CLAMPS` in every
mode, and the intended public deployment (`VIDTHEQUE_PUBLIC_READONLY=1` +
`VIDTHEQUE_AUTH=none`) puts nothing in front of it — so an anonymous visitor
was handed the owner's bounds, `max_text_chars=0` included. That is the
full-transcript hatch demo-site.md §2 reserves for an owner's agent, and at
120 req/min the corpus is a short crawl.

The matrix below is the fix, asserted as a matrix rather than as three
examples: {anonymous, session, bearer, trusted peer} × {none, token} ×
{readonly on, off}, on **both** prefixes.
"""


def _policy(client: TestClient, path: str, **kwargs) -> str:
    response = client.get(f"{path}/api/meta", **kwargs)
    assert response.status_code == 200, response.text
    return response.json()["clamps"]["policy"]


def test_anonymous_gets_the_public_policy_on_both_prefixes_in_every_mode(
    tmp_path: Path,
) -> None:
    """The bug, closed. `AUTH=none` has no credential, so nobody is the owner.

    Both halves of the intended public deployment are here — the demo flag on
    and off — because the flag was never what made the JSON reachable: the
    route group is registered in every mode, and `guarded()` is open in `none`
    by design (the corpus is already open through `/mcp` there).
    """
    for readonly in (True, False):
        with make_client(tmp_path, public=PublicSettings(enabled=readonly)) as anon:
            assert _policy(anon, ROOT) == "public"
            if readonly:  # `/api/*` is public-mode-only
                assert _policy(anon, "") == "public"


def test_a_bearer_or_a_session_gets_the_owner_policy_on_both_prefixes(
    tmp_path: Path,
) -> None:
    """The other half: a credential widens the bounds wherever it is presented.

    Including on `/api/*`. The demo's numbers are what an anonymous browser
    gets; a caller who can prove they are the owner is the caller the hatch was
    written for, and which path they knocked on says nothing about who they are.

    The session leg is minted the way `token` mode's own gate test mints one
    rather than through `/dashboard/login`, because in a *read-only* deployment
    that page is not registered (§2.3) and the cookie still has to be honoured:
    a session outlives the flag that was flipped after it was issued.
    """
    import time

    from vidtheque_mcp.auth.login import SESSION_COOKIE

    for readonly in (True, False):
        with owner_client(tmp_path, readonly=readonly) as client:
            assert _policy(client, ROOT, headers=BEARER) == "owner"

            store = client.app.state.assembled.auth.store
            assert store is not None
            store.save_session("clamp-sid", "owner", int(time.time()) + 600)
            client.cookies.set(SESSION_COOKIE, "clamp-sid")
            assert _policy(client, ROOT) == "owner"
            if readonly:  # `/api/*` is public-mode-only
                assert _policy(client, "") == "owner"
                client.cookies.clear()
                assert _policy(client, "", headers=BEARER) == "owner"

    # And the live login page, in the deployment that registers one.
    with owner_client(tmp_path, readonly=False) as private:
        sign_in(private)
        assert _policy(private, ROOT) == "owner"


def test_a_readonly_deployment_with_a_credential_does_not_clamp_its_own_owner(
    tmp_path: Path,
) -> None:
    """Why the policy is not keyed off `VIDTHEQUE_PUBLIC_READONLY`.

    `api_routes(PUBLIC_CLAMPS if readonly else OWNER_CLAMPS, …)` was the
    one-line version deploy-public.md sketched and rejected: it would clamp the
    owner of a demo instance that *does* have a token configured, which is Tom's
    own deployment. Keying off the credential separates the two callers the
    flag cannot tell apart.
    """
    with owner_client(tmp_path, readonly=True) as client:
        anon = client.get(f"{ROOT}/api/videos?limit=100000")
        assert anon.status_code == 401  # `token` mode gates the read side too
        owner = client.get(f"{ROOT}/api/videos?limit=100000", headers=BEARER).json()
        assert owner["pagination"]["limit"] == 100
        # And the demo's own front door, same instance, same request, no token.
        assert client.get("/api/videos?limit=100000").json()["pagination"]["limit"] == 50


def test_a_trusted_peer_counts_as_a_credential_for_the_clamps(tmp_path: Path) -> None:
    """The CIDR decision, deliberate: yes, it grants the owner policy.

    `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS` already grants that network the *write*
    side with no credential at all (§3.4) — indexing, re-indexing, tagging. A
    network trusted to change the corpus but not to read a transcript of it
    would be a boundary with no shape. It is also the lever that gives an
    `AUTH=none` LAN deployment its owner back, so this asserts it in `none`
    mode, where there is no other credential to hold.

    Socket peer only, as everywhere else: the forged-header client is outside.
    """
    import ipaddress

    lan = DashboardSettings(trusted_cidrs=(ipaddress.ip_network("10.0.0.0/8"),))

    def app() -> object:  # a fresh app per client: one lifespan each
        return build_app(
            _settings(tmp_path, auth_mode="none"),
            embeddings=FakeEmbeddings(),
            run_pipeline=False,
            public=PublicSettings(enabled=True),
            dashboard=lan,
        )

    inside = TestClient(app(), base_url="http://localhost:8080", client=("10.9.9.9", 1))
    with inside:
        assert _policy(inside, ROOT) == "owner"
        assert _policy(inside, "") == "owner"

    outside = TestClient(
        app(), base_url="http://localhost:8080", client=("203.0.113.7", 1)
    )
    with outside:
        forged = {"CF-Connecting-IP": "10.9.9.9", "X-Forwarded-For": "10.9.9.9"}
        assert _policy(outside, ROOT, headers=forged) == "public"
        assert _policy(outside, "", headers=forged) == "public"


def test_the_full_transcript_hatch_is_refused_to_anonymous_traffic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`max_text_chars=0` — the one difference that is not "a bigger page".

    The seeded corpus has no segment long enough for a length assertion to mean
    anything, so this asserts the number the facade *hands the service layer*,
    which is the contract: `0` is the documented opt-out and the public policy
    does not have one.

    The anonymous half is measured on `AUTH=none` + `READONLY=1`, because that
    is the deployment the hatch was reachable on — in `token` mode the
    dashboard's read gate refuses an anonymous caller long before a clamp
    matters. Both prefixes, because the bug was that one of them differed.
    """
    from vidtheque_mcp.public import api

    asked: list[int] = []
    real = api.search.run

    async def spy(deps, **kwargs):
        asked.append(kwargs["max_text_chars"])
        return await real(deps, **kwargs)

    monkeypatch.setattr(api.search, "run", spy)

    with make_client(tmp_path, public=PublicSettings(enabled=True)) as demo:
        for path in ("", ROOT):
            assert demo.get(f"{path}/api/search?q=cache&max_text_chars=0").status_code == 200
    assert asked == [400, 400], "anonymous keeps the demo's forced width"

    asked.clear()
    with owner_client(tmp_path, readonly=True) as client:
        for path in ("", ROOT):
            got = client.get(f"{path}/api/search?q=cache&max_text_chars=0", headers=BEARER)
            assert got.status_code == 200
    assert asked == [0, 0], "the owner is the agent the hatch was written for"


def test_the_demo_pages_own_clamp_numbers_are_untouched(tmp_path: Path) -> None:
    """No regression to what demo-site.md §2.1/§2.2 promises a visitor.

    The policy objects are pinned in `test_public.py`; this pins the numbers a
    request actually meets on the deployment the demo ships as.
    """
    with make_client(tmp_path, public=PublicSettings(enabled=True)) as demo:
        meta = demo.get("/api/meta").json()["clamps"]
        assert meta == {
            "policy": "public",
            "search_max_limit": PUBLIC_CLAMPS.search_max_limit,
            "videos_max_limit": PUBLIC_CLAMPS.videos_max_limit,
        }
        videos = demo.get("/api/videos").json()["pagination"]
        assert videos["limit"] == PUBLIC_CLAMPS.videos_default_limit
        search = demo.get("/api/search?q=cache").json()["pagination"]
        assert search["limit"] == PUBLIC_CLAMPS.search_default_limit


def test_the_management_reads_keep_the_owner_page_size(tmp_path: Path) -> None:
    """§2.4: what phase 5 keyed off the credential is the facade, not this.

    The full-transcript hatch is an `/api/*` parameter and `/dashboard/api/
    library` takes no `max_text_chars` at all. What is left between the two
    policies here is rows-per-page, on a listing the demo publishes in full
    anyway, so keying it off the credential would paginate the browsable corpus
    at 24 rows to protect nothing.
    """
    with make_client(tmp_path, public=PublicSettings(enabled=True)) as demo:
        anonymous = demo.get(f"{LIBRARY}?limit=100000").json()
        assert anonymous["pagination"]["limit"] == OWNER_CLAMPS.videos_max_limit
        assert "max_text_chars" not in json.dumps(anonymous)
    # And the bound is still the server's, not the URL's.
    assert OWNER_CLAMPS.videos_max_limit == 100


# ------------------------------- 9. the digest, and the wrap sweep behind it
#
# Tom, 2026-08-10: "there are some weird wrapping issues notably and make sure
# nothing is out of place", and the OCR matches block "eats a huge column of
# vertical space" on dense slides. The design that answered both is DESIGN.md,
# **The digest**; what a CPU test can hold is the half a screenshot cannot check
# twice — that the bounds are real numbers, that the expander counts the list it
# is actually holding, and that the full text never left the page.


# ------------------------------------- 10. the second identity pass (2026-08-10)
#
# Fifteen items from Tom's review of the projection-room rebuild. The ones with
# a shape a test can hold are here; the rest are geometry and live in the
# screenshots and in dashboard.md §12.5.


def test_the_version_is_one_string_everywhere(tmp_path: Path) -> None:
    """One version string, the same in every place that publishes it.

    `mcp/pyproject.toml` said 0.1.0 while the workspace root and the worker both
    said 0.0.1, so `/healthz`, `vidtheque://context` and the dashboard footer
    published a version the project does not ship (Tom, 2026-08-10). One
    constant, asserted against the packaging metadata rather than against a
    literal, so the next bump cannot leave a surface behind. The footer is the
    React chassis's now and it reads `/dashboard/api/session`, which is where
    this asserts it.
    """
    import tomllib

    from vidtheque_mcp import __version__

    root = Path(__file__).resolve().parents[2]
    declared = tomllib.loads((root / "mcp/pyproject.toml").read_text())["project"]["version"]
    worker = tomllib.loads((root / "worker/pyproject.toml").read_text())["project"]["version"]
    assert __version__ == declared == worker == "0.0.17"

    with make_client(tmp_path) as client:
        assert client.get("/healthz").json()["version"] == __version__
        assert client.get(f"{ROOT}/api/session").json()["version"] == __version__


def test_the_cue_pager_clamps_and_never_counts(client: TestClient) -> None:
    """The transcript pane's source: the server's bounds, not the URL's."""
    payload = client.get(
        f"{ROOT}/api/videos/kCc8FmEb1nY/cues?offset=0&limit=100000"
    ).json()
    assert payload["limit"] == 200  # CUE_PAGE_MAX, not the URL's number
    assert payload["has_more"] is False
    assert "total" not in payload
    assert client.get(f"{ROOT}/api/videos/nosuchvideo1/cues").status_code == 404


# ------------------------------------ 8. the fourth review pass (2026-08-10)


