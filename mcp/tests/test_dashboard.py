"""The management dashboard — `docs/design/dashboard.md`, phases 1 and 2.

Six read-only pages, `/dashboard/api/*` under caller-keyed clamps, and the auth
gate in front of both. Worker readiness reaches only the injected HTTP status
stub; nothing here loads a model. The pages render against the seeded fixture
corpus through the same ASGI app the server runs.

Phase 2's half is the jobs view — the `not_before` countdown, the retry
counter, the degraded list and the event tail — plus its demo projection, which
keeps every clock and drops exactly two fields.

The shipped half of phase 5 is the search document, entering through the same
handler as the JSON facade; the overview's readiness observation is the other
current-state read covered here.

The two things this file is most interested in are the ones a screenshot cannot
check: that a corpus string never becomes markup, and that every list is
bounded server-side however the URL asks.
"""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

import httpx2 as httpx
import pytest
from starlette.testclient import TestClient

from vidtheque_mcp.app import build_app
from vidtheque_mcp.config import Settings
from vidtheque_mcp.dashboard import ROOT, WRITE_ROUTES
from vidtheque_mcp.dashboard.settings import DashboardSettings
from vidtheque_mcp.dashboard.views import EVENT_PREVIEW, WORKER_STATUS_TIMEOUT_S
from vidtheque_mcp.db.connection import open_write_connection
from vidtheque_mcp.public.api import OWNER_CLAMPS, PUBLIC_CLAMPS
from vidtheque_mcp.public.settings import PublicSettings

from .conftest import FakeEmbeddings, rpc, rpc_headers, seed

STATIC = Path(__file__).resolve().parents[1] / "src/vidtheque_mcp/dashboard/static"
TEMPLATES = Path(__file__).resolve().parents[1] / "src/vidtheque_mcp/dashboard/templates"
DEMO_STATIC = Path(__file__).resolve().parents[1] / "src/vidtheque_mcp/public/static/demo"

# What was on someone's screen, and what yt-dlp said about it. Both are corpus
# strings and both are attacker-controlled in exactly the same way.
HOSTILE = '<script>alert(document.cookie)</script> <img src=x onerror=alert(1)>'


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


def page(client: TestClient, path: str, status: int = 200) -> str:
    response = client.get(path)
    assert response.status_code == status, f"{path} -> {response.status_code}"
    return response.text


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
    for path in (ROOT, f"{ROOT}/videos", f"{ROOT}/videos/kCc8FmEb1nY"):
        assert client.get(path).status_code == 200
    registered = {str(getattr(r, "path", "")) for r in client.app.routes}
    assert not (registered & set(WRITE_ROUTES))


def test_token_mode_refuses_the_pages_and_the_json(tmp_path: Path) -> None:
    with make_client(tmp_path, auth_mode="token", token="s3cret") as client:
        denied = client.get(ROOT)
        assert denied.status_code == 401
        assert "Authorization: Bearer" in denied.text
        assert "vidtheque" in denied.text  # a page, not a JSON blob
        api = client.get(f"{ROOT}/api/videos")
        assert api.status_code == 401
        assert api.json()["error"] == "E_AUTH_REQUIRED"
        # No corpus leaks through the refusal.
        assert "kCc8FmEb1nY" not in denied.text


def test_token_mode_accepts_the_bearer(tmp_path: Path) -> None:
    with make_client(tmp_path, auth_mode="token", token="s3cret") as client:
        headers = {"Authorization": "Bearer s3cret"}
        assert client.get(ROOT, headers=headers).status_code == 200
        assert client.get(f"{ROOT}/api/videos", headers=headers).status_code == 200
        assert client.get(ROOT, headers={"Authorization": "Bearer wrong"}).status_code == 401


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
        assert client.get(ROOT).status_code == 401

        store.save_session("sid-1", "owner", int(time.time()) + 600)
        client.cookies.set(SESSION_COOKIE, "sid-1")
        assert client.get(ROOT).status_code == 200
        client.cookies.set(SESSION_COOKIE, "sid-nope")
        assert client.get(ROOT).status_code == 401


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
        assert client.get(ROOT).status_code == 200
        client.cookies.set(SESSION_COOKIE, "stale")
        assert client.get(ROOT).status_code == 401


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
        for path in (ROOT, f"{ROOT}/videos", f"{ROOT}/api/videos"):
            assert client.get(path).status_code == 404


# ------------------------------------------------------------- 3. the pages


def test_the_overview_answers_the_first_screen_questions(client: TestClient) -> None:
    body = page(client, ROOT)
    # The counts, the declared models, and the live vector state beside them.
    assert "transcript cues" in body and "keyframes" in body
    assert "Qwen/Qwen3-VL-Embedding-2B" in body
    # The "vector legs on" pill is gone (Tom, 2026-08-10): it was a green badge
    # for the ordinary case, beside a table that already names both embedding
    # models. What survives is the one fact that changes what a *write* can do —
    # a cell in the readiness strip since the 2026-08-13 merge, beside the four
    # other states of the same pipeline rather than two panels below them.
    assert "vector legs" not in body
    assert "<dt>Indexing</dt>" in body
    assert re.search(r'class="pill tone-ok">allowed<', body)
    # `data_status` verbatim from corpus-summary, not re-derived.
    assert re.search(r'class="pill tone-\w+">(ok|partial|degraded|indexing|empty)<', body)
    # Storage from the column, and no filesystem path anywhere.
    assert "keyframe JPEGs" in body
    assert "/keyframes/" not in body and "jpeg_path" not in body


def test_readiness_is_one_panel_holding_declared_beside_served(
    tmp_path: Path,
) -> None:
    """Tom, 2026-08-13: readiness is one story, told compactly.

    It was two panels — "Pipeline readiness" near the top and "Declared models,
    and what the worker is serving" at the foot of the page — with the fact that
    links them, whether the two agree, spread across both. One panel now: five
    states in one strip, then declared and served side by side, and the whole
    panel wears the drift rule when they stop agreeing.
    """

    def worker(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"backends": [{"task": "stt", "model": "large-v3", "loaded": True}]}
        )

    with make_client(tmp_path, worker_handler=worker) as client:
        body = page(client, ROOT)
        # One heading for the whole story, and the panel that carried the
        # second one is gone rather than moved.
        assert "Declared models, and what the worker is serving" not in body
        assert body.count('id="readiness"') == 1
        panel = body[body.index('id="readiness"') : body.index("Recently indexed")]
        # Declared and served, in that order, inside the one panel.
        assert panel.index("model declared") < panel.index("model served")
        assert "Qwen/Qwen3-VL-Embedding-2B" in panel and "large-v3" in panel
        assert '<div class="split readiness-models">' in panel
        # Five states in the strip, including the one the models panel carried.
        assert panel.count("<dt>") == 5

        # The diff goes red on the panel that *is* the diff.
        assembled = client.app.state.assembled
        assembled.db.vectors.disable("the worker is serving 'other'.")
        try:
            drifted = page(client, ROOT)
            assert 'class="panel readiness is-drift"' in drifted
        finally:
            assembled.db.vectors.enabled = True
            assembled.db.vectors.reason = None


def test_the_overview_no_longer_repeats_the_video_count(client: TestClient) -> None:
    """Tom, 2026-08-13: "across N videos" says what the figure two cells to the
    left of it already says, in the same band."""
    assert "across" not in page(client, ROOT).split("</dl>")[0]


def test_the_videos_table_counts_the_filtered_set_exactly(client: TestClient) -> None:
    """Tom, 2026-08-13: "just show the actual number of videos".

    The `~` came from `list-videos`' bounded count probe, which stays exactly as
    it is — a total an agent pays tokens for and will not page through is the
    thing `COUNT_PROBE_FLOOR` exists to refuse. The page counts the same
    filtered CTE itself, so the number moves with the filters and the pager
    still pages on `has_more`.
    """
    body = page(client, f"{ROOT}/videos")
    assert "of ~" not in body
    assert re.search(r'shown of <span class="shown">4</span>', body)

    # It is the *filtered* count, not the corpus size.
    only = page(client, f"{ROOT}/videos?index_state=indexing")
    assert re.search(r'shown of <span class="shown">1</span>', only)

    # A page of one still says how many there are, and still says there is more.
    paged = page(client, f"{ROOT}/videos?limit=1")
    assert re.search(r'<span class="shown">1</span> shown of <span class="shown">4</span>', paged)
    assert "more available" in paged

    # And the tool's own payload is untouched: the probe is the contract of the
    # surface an agent reads. Three, not four, and that is the other half of
    # §5.2 — the JSON keeps the query surface's "in the corpus" (ready|stale)
    # while the page asks for `all`, because a management table that cannot see
    # the half-indexed video is the one view nobody needs.
    pagination = client.get(f"{ROOT}/api/videos").json()["pagination"]
    assert pagination["approx_total"] == 3


def test_the_overview_shows_the_drift_banner_when_vectors_are_off(
    client: TestClient,
) -> None:
    assembled = client.app.state.assembled
    assembled.db.vectors.disable("the worker is serving 'other' but the corpus used 'qwen'.")
    try:
        body = page(client, ROOT)
        assert "The corpus and the worker disagree" in body
        assert "the worker is serving" in body
        # The *effect* is the banner's own sentence, and it is what survived the
        # pill's removal: the reason a visitor should stop trusting vector
        # recall is a paragraph, not a badge.
        assert "the vector legs are off" in body
    finally:
        assembled.db.vectors.enabled = True
        assembled.db.vectors.reason = None


def test_pipeline_readiness_reads_worker_status_over_bounded_http(
    tmp_path: Path,
) -> None:
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
        body = client.get(ROOT, headers=BEARER).text

    assert [request.url.path for request in requests] == ["/status"]
    assert WORKER_STATUS_TIMEOUT_S <= 1.0
    assert 'data-readiness' in body
    assert ">MCP<" in body and ">Database<" in body
    assert "Reachable over HTTP" in body
    assert "served/text-model" in body and "served/frame-model" in body
    assert ">loaded<" in body and ">cold<" in body
    assert re.search(r"last health check\s*<time[^>]+>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", body)
    assert "9999" not in body and ">vram<" not in body.lower()


def test_pipeline_readiness_degrades_without_delaying_or_breaking_the_page(
    tmp_path: Path,
) -> None:
    def timed_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("late", request=request)

    with owner_client(tmp_path, worker_handler=timed_out) as client:
        body = client.get(ROOT, headers=BEARER).text
        assert client.get(f"{ROOT}/videos", headers=BEARER).status_code == 200
    assert ">unavailable<" in body
    assert "did not answer its status check" in body

    with owner_client(tmp_path, worker_url="") as client:
        body = client.get(ROOT, headers=BEARER).text
    assert ">unconfigured<" in body
    assert "No worker URL is configured" in body


def test_readonly_readiness_omits_operator_infrastructure(tmp_path: Path) -> None:
    called = False

    def worker(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(
            200,
            json={"backends": [{"task": "embed", "model": "private/model-id"}]},
        )

    with owner_client(tmp_path, readonly=True, worker_handler=worker) as client:
        assembled = client.app.state.assembled
        assembled.db.vectors.disable("private drift reason")
        body = client.get(ROOT, headers=BEARER).text

    assert not called  # the projection does not make an operator-only probe
    assert ">MCP<" in body and ">Database<" in body
    assert "full-text only" in body
    assert "Worker" not in body
    assert "private/model-id" not in body and "private drift reason" not in body


def test_search_is_a_rail_destination_and_uses_the_shared_result_contract(
    tmp_path: Path,
) -> None:
    """§14, amended 2026-08-13: the rail's search *form* became a search *page*.

    What has to stay true is everything the form was there for — one entry from
    every page, the shared handler behind it, the tool's own receipts — and what
    changes is that the entry is a link to a document rather than a text field
    in the chrome.
    """
    with owner_client(tmp_path) as client:
        for path in (ROOT, f"{ROOT}/videos", f"{ROOT}/jobs"):
            body = client.get(path, headers=BEARER).text
            assert 'class="rail-search"' not in body, "the rail widget is gone"
            assert f'<a href="{ROOT}/search"' in body, "and it is a nav item"

        body = client.get(
            f"{ROOT}/search?q=cache&limit=999", headers=BEARER
        ).text
        assert 'data-search-results' in body and 'data-limit="50"' in body
        # The page is where you are, and the form is the page's own.
        assert f'<a href="{ROOT}/search" aria-current="page"' in body
        assert f'action="{ROOT}/search"' in body
        # Structured leg names and corpus channel names are not translated away
        # by the page: the human label is added, the machine key stays beside
        # it. Exact-second receipts are the tool's own deeplinks.
        assert "transcript_fts" in body and "frame_knn" in body
        assert "Transcript — keyword match (FTS)" in body
        assert "Frames — visual candidates considered" in body
        assert "Andrej Karpathy" in body and "GPU MODE" in body
        assert re.search(r'href="https://youtu\.be/[\w-]+\?t=\d+"', body)
        assert re.search(r"youtu\.be/[\w-]+\?t=\d+ ↗", body)

        notes = client.get(
            f"{ROOT}/search?q=words-that-do-not-exist-anywhere", headers=BEARER
        ).text
        assert "note: no word of this query occurs anywhere in the corpus" in notes

    # In an open dashboard the same credential-keyed policy as the JSON facade
    # applies; the page does not turn its route prefix into owner authority.
    with make_client(tmp_path) as client:
        body = page(client, f"{ROOT}/search?q=cache&limit=999")
        assert 'data-limit="20"' in body


def test_a_search_hit_carries_its_frame_its_kind_and_a_way_into_the_index(
    client: TestClient,
) -> None:
    """The rebuilt result row (Tom, 2026-08-13) — §14, amended.

    A hit used to be a title, a raw `source` string and a YouTube link. Three
    things it now carries, and each one is a fact the row could not previously
    show: the *frame*, which for an OCR or visual match is the evidence itself;
    the *kind* of evidence in a word; and a link into what this deployment
    stored, next to the receipt that leaves for YouTube.
    """
    body = page(client, f"{ROOT}/search?q=cache")

    # The frame, through the derived cache at the strip's own width, opened by
    # the same button class and the same overlay the keyframe grid uses.
    assert 'class="framebtn search-hit-shot"' in body
    assert re.search(r'src="/frames/[\w.-]+\.jpg\?w=192', body)
    assert re.search(r'data-large="/frames/[\w.-]+\.jpg\?w=1280', body)
    assert body.count('<dialog id="shot"') == 1
    # …and never against PUBLIC_URL, which the JSON facade's own decoration
    # would have left on the hit.
    assert "http://localhost:8080/frames/" not in body

    # The kind, as a word — with the tool's own `source` string kept beside it.
    assert '<span class="badge badge-screen">on-screen</span>' in body
    assert '<span class="badge badge-spoken">spoken</span>' in body
    assert 'title="source=ocr"' in body and 'title="source=transcript"' in body

    # A frame hit lands on that frame: `ord` is dense, so which strip page holds
    # it is arithmetic, and `select` marks it whether or not the script runs.
    assert f'href="{ROOT}/videos/kCc8FmEb1nY?frame_offset=0&amp;select=0#frame-0"' in body
    # A spoken hit names its cues by id and the transcript panel pages by
    # offset, so it links to the video plainly rather than inventing a page.
    assert f'href="{ROOT}/videos/zduSFxRajkE"' in body

    # The receipt is still the argument, and it is still the tool's own link.
    assert re.search(r'class="search-receipt" href="https://youtu\.be/[\w-]+\?t=\d+"', body)

    # The reader's own words are marked in the snippet — as text nodes the
    # template wraps, never as markup built from corpus data.
    assert "<mark>cache</mark>" in body


def test_the_page_translates_a_leg_and_a_source_without_ever_losing_one() -> None:
    """The shapes the fixture corpus cannot produce, at the function.

    Two of the four sources and both `frame_*` legs never appear over the
    seeded videos, and they are exactly the cases where a translation table
    fails quietly: the rule is that an unfamiliar key arrives as an unfamiliar
    *word*, never as a hit with no provenance or a leg that vanished.
    """
    from vidtheque_mcp.dashboard.views import (
        FRAME_PAGE,
        HIGHLIGHT_MARKS,
        _highlighted,
        _search_evidence,
        _search_inside,
        _search_legs,
    )

    both = _search_evidence("transcript+ocr")
    assert [pill["label"] for pill in both["pills"]] == ["spoken", "on-screen"]
    assert both["kind"] == "mixed" and both["key"] == "transcript+ocr"
    # A fourth leg one day: its own name, its own badge, nothing dropped.
    fourth = _search_evidence("hypertext")
    assert fourth["pills"] == [{"label": "hypertext", "kind": "other"}]
    assert _search_evidence(None)["pills"] == []

    legs = _search_legs({"ocr": 1, "transcript_fts": 9, "transcript": 3, "novel": 2})
    assert [leg["key"] for leg in legs] == ["transcript", "transcript_fts", "ocr", "novel"]
    assert [leg["sub"] for leg in legs] == [False, True, False, True]
    assert legs[-1]["label"] == "novel", "an unknown leg keeps its own name"

    # A frame hit lands on the strip page that holds its ordinal — the shot
    # bars' arithmetic, because `ord` is dense per video.
    inside = _search_inside({"video_id": "vid", "frame_id": f"vid-{FRAME_PAGE + 5:05d}"})
    assert inside == (
        f"{ROOT}/videos/vid?frame_offset={FRAME_PAGE}&select={FRAME_PAGE + 5}"
        f"#frame-{FRAME_PAGE + 5}"
    )
    # A frame id that is not this video's, and a hit with no frame at all: the
    # page link, never a guessed ordinal.
    assert _search_inside({"video_id": "vid", "frame_id": "other-00001"}) == (
        f"{ROOT}/videos/vid"
    )
    assert _search_inside({"video_id": "vid"}) == f"{ROOT}/videos/vid"
    assert _search_inside({}) is None

    # The marks are bounded like every other list on this surface.
    marks = _highlighted("ab " * 500, "ab")
    assert sum(1 for part in marks if part["hit"]) == HIGHLIGHT_MARKS


def test_search_page_escapes_corpus_and_query_text(client: TestClient) -> None:
    body = page(client, f"{ROOT}/search?q=%3Cscript%3Ealert(1)%3C%2Fscript%3E")
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    # And the highlighter is not a second way in: a query term that matches
    # hostile corpus text is marked as escaped text, never as a live tag.
    assert "<mark><script>" not in body


def test_the_videos_table_shows_every_state_and_no_per_row_counts(
    client: TestClient,
) -> None:
    body = page(client, f"{ROOT}/videos")
    # `all` means all: the half-indexed video is in the corpus and on the page.
    assert "aaaaaaaaaaa" in body
    assert ">indexing<" in body and ">ready<" in body
    # Coverage booleans, not counts (§4.2) — three pills per row.
    assert body.count('class="cov cov-') == 4 * 3
    assert "transcript: present" in body and "on-screen text: missing" in body


def test_the_videos_table_clamps_and_filters_from_the_url(client: TestClient) -> None:
    assert 'value="100"' in page(client, f"{ROOT}/videos?limit=100000")
    assert 'value="1"' in page(client, f"{ROOT}/videos?limit=0")
    # An unknown state is not honoured and not an error: it falls back to all.
    assert "aaaaaaaaaaa" in page(client, f"{ROOT}/videos?index_state=nonsense")
    only = page(client, f"{ROOT}/videos?index_state=indexing")
    assert "aaaaaaaaaaa" in only and "kCc8FmEb1nY" not in only
    empty = page(client, f"{ROOT}/videos?index_state=failed")
    assert "Nothing matches those filters" in empty


def test_the_videos_table_pages_with_has_more_not_a_total(client: TestClient) -> None:
    body = page(client, f"{ROOT}/videos?limit=2")
    assert "more available" in body
    assert "offset=2" in body
    assert body.count('<tr>') == 3  # the header row plus two videos
    last = page(client, f"{ROOT}/videos?limit=2&offset=2")
    assert "offset=0" in last  # a previous link


def test_the_detail_page_carries_all_seven_stages(client: TestClient) -> None:
    body = page(client, f"{ROOT}/videos/kCc8FmEb1nY")
    for stage in ("fetch", "stt", "chunk", "text_embed", "keyframe", "ocr", "frame_embed"):
        assert f"<code>{stage}</code>" in body
    # A stage with no row is `absent`, not silently missing: this corpus was
    # seeded without a fetch row.
    assert ">absent<" in body
    assert '<h2 class="panel-title" id="provenance">Provenance</h2>' in body


def test_the_detail_page_is_honest_about_a_failed_stage(client: TestClient) -> None:
    body = page(client, f"{ROOT}/videos/aaaaaaaaaaa")
    # video-summary refuses a mid-pipeline video; the refusal is rendered
    # verbatim and the panels below still say what exists.
    assert "E_INDEXING" in body
    assert ">failed<" in body
    assert "yt-dlp-2026.07.04" in body  # the stage that did succeed
    # …and the one that did not records no model at all. The dash is the whole
    # statement — the paragraph that used to gloss it as "not recorded" was
    # culled 2026-08-10, so the cell itself has to carry the fact.
    assert "Sign in to confirm you are not a bot" in body
    assert '<td class="col-model"><span class="muted">—</span></td>' in body


def test_the_detail_page_counts_are_bounded_and_per_video(client: TestClient) -> None:
    body = page(client, f"{ROOT}/videos/kCc8FmEb1nY")
    assert "What was stored" in body
    # Every ledger note is a value. "the embedding unit" — a vocabulary lesson
    # in the slot beside a figure — went in the 2026-08-10 cull; what a chunk
    # is made of is a number the page already has.
    assert re.search(r"<dd class=\"figure-note\">from \d+ cues</dd>", body)
    assert "kept of" in body  # keyframes kept vs captured — the dedup story
    assert "whisperx" in body  # where the transcript came from


def test_the_scene_timeline_is_positions_not_a_query_per_shot(
    client: TestClient,
) -> None:
    body = page(client, f"{ROOT}/videos/kCc8FmEb1nY")
    bars = re.findall(r'class="shotbar[^"]*"[^>]*data-shot="(\d+)"', body)
    assert bars == ["0", "1", "7"]
    # Every bar is a real link into the frame page that holds its first frame.
    assert body.count("#frame-") >= len(bars)
    # The shot whose only frame was deduplicated is dimmed, not hidden.
    assert "is-dedup" in body
    # The band's own caption is a count and nothing else. The paragraph that
    # used to explain that shots are a `GROUP BY shot_id` rather than a table
    # was culled 2026-08-10 (Tom); the fact lives in dashboard.md §4.3.
    assert "3 shot(s)." in body


def test_a_shot_bar_off_the_current_strip_page_still_lands_on_evidence(
    client: TestClient,
) -> None:
    """The regression, found live on real data 2026-08-10 (review round 4).

    A shot bar is supposed to *select its keyframe into evidence*: the card and
    its OCR figure marked in gold, the reader put in front of the moment they
    pointed at. That only ever worked when the frame happened to be in the
    document already, because the interception is a click handler and the
    fragment it reads — `#frame-N` — never reaches a server. The fixture hid it
    completely: a seeded video has three keyframes and a strip page holds
    twenty-four, so every bar's frame was always on the page. A real talk has
    one shot per keyframe and a hundred and sixty of them, so five bars in six
    navigated and the page they landed on marked nothing at all.

    `?frames=1` is the fixture's way of being that talk: one frame per strip
    page, so two of the three bars point off it.
    """
    body = page(client, f"{ROOT}/videos/kCc8FmEb1nY?frames=1")
    links = re.findall(r'<li class="shotbar.*?<a href="([^"]+)"', body, re.S)
    assert len(links) == 3
    # Every bar names the ordinal it points at, in the query string, beside the
    # fragment that carries the same number for the browser's own scroll.
    for link in links:
        ordinal = re.search(r"#frame-(\d+)$", link).group(1)
        assert f"&amp;select={ordinal}" in link, link
        assert f"frame_offset={ordinal}" in link, "…on the page that holds it"

    # And the page that link lands on puts the frame into evidence itself —
    # both halves of it, exactly as the intercepted click does.
    landing = page(
        client, f"{ROOT}/videos/kCc8FmEb1nY?frames=1&frame_offset=1&select=1"
    )
    card = re.search(r'<li class="framecard([^"]*)" id="frame-1"', landing)
    assert card and "is-selected" in card.group(1)
    # The card is the frame's still, its detection boxes and its lines, so
    # marking it marks the evidence whole (round 4, item 2 merged the two).
    assert 'data-ocrframe="kCc8FmEb1nY-00001"' in _figure(landing, "kCc8FmEb1nY-00001")

    # A page nobody selected on marks nothing: `select` has no default, because
    # `0` is a real ordinal and a page that arrives with a keyframe already
    # marked is a page reporting a click nobody made.
    quiet = page(client, f"{ROOT}/videos/kCc8FmEb1nY?frames=1")
    assert "is-selected" not in quiet
    assert "is-selected" not in page(
        client, f"{ROOT}/videos/kCc8FmEb1nY?frames=1&select=not-a-number"
    )


def test_the_intercepted_click_and_the_navigation_end_in_the_same_url(
    client: TestClient,
) -> None:
    """The script's half of the same fix.

    When the frame is already on the page there is nothing to navigate to, so
    the handler marks it in place — and writes the same `select=` the link
    would have carried, so a reload, a copied link and the back button all
    agree with the path that had to reload.
    """
    script = (STATIC / "dashboard.js").read_text()
    assert 'here.searchParams.set("select", String(ord))' in script
    assert "history.replaceState" in script


def test_the_scrub_preview_is_markup_the_band_can_lose(client: TestClient) -> None:
    """The hover preview, and the no-JavaScript page it has to leave alone.

    The band is a row of real links and stays one: everything the preview needs
    rides on the bars as data, and the box the script fills is served empty.
    """
    body = page(client, f"{ROOT}/videos/kCc8FmEb1nY")
    # Served empty and hidden. Nothing in it is a fact the page would lose.
    assert '<div class="scrubpreview" id="scrub" aria-hidden="true" hidden>' in body
    assert '<span class="scrubspan"></span>' in body
    assert '<span class="scrubmeta"></span>' in body
    # …and the bars are still what they were, `title` included, because that is
    # the only hover a reader with the script blocked gets.
    bars = re.findall(r'<li class="shotbar[^"]*"(.*?)</li>', body, re.S)
    assert len(bars) == 3
    for bar in bars:
        assert re.search(r'data-span="\d+:\d\d–\d+:\d\d"', bar)
        assert re.search(r'data-kept="\d+/\d+ kept"', bar)
        assert "<a href=" in bar and "title=" in bar and "#frame-" in bar


def test_the_scrub_preview_costs_no_new_cache_variant(client: TestClient) -> None:
    """§6.4: three widths per keyframe in `derived/`, and the preview is one of
    the three. A fourth width is a fourth JPEG per frame in a byte-capped
    cache, so the preview reuses the strip's."""
    body = page(client, f"{ROOT}/videos/kCc8FmEb1nY")
    previews = re.findall(r'data-preview="([^"]+)"', body)
    assert len(previews) == 3, "one per shot, from `first_ord`, with no extra query"
    for url in previews:
        assert url.startswith("/frames/"), "relative, like every asset here"
        assert re.search(r"\?w=192&(?:amp;)?q=70", url), "the strip's own width"
    # The shot that opens the video previews the frame the strip opens with.
    assert "/frames/kCc8FmEb1nY-00000.jpg?w=192" in previews[0]
    # Still exactly three variants across the whole page.
    assert set(re.findall(r"/frames/[\w:-]+\.jpg\?w=(\d+)&(?:amp;)?q=70", body)) == {
        "192", "512", "1280",
    }


def test_the_scrub_preview_reads_the_bars_and_writes_only_text(client: TestClient) -> None:
    """The script half: no markup from data, no unfiltered URL, and the
    keyboard gets the same box the pointer does."""
    script = (STATIC / "dashboard.js").read_text()
    assert "safeUrl(bar.dataset.preview)" in script, "the frame URL is filtered"
    # `test_no_dashboard_module_builds_html_from_data` owns the sink list; this
    # only asserts the two lines the preview writes are text nodes.
    assert "shotSpan.textContent =" in script and "shotMeta.textContent =" in script
    # Loaded once, then cached in the page; a sweep is not a request per shot.
    assert "fetched.has(url)" in script and "setTimeout" in script
    # Arrow keys move focus between the anchors that were already there.
    for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
        assert key in script
    assert 'link.removeAttribute("title")' in script, "no tooltip under a preview"


def test_the_keyframe_strip_uses_the_derived_cache_and_never_base64(
    client: TestClient,
) -> None:
    body = page(client, f"{ROOT}/videos/kCc8FmEb1nY")
    widths = set(re.findall(r"/frames/[\w:-]+\.jpg\?w=(\d+)&(?:amp;)?q=70", body))
    assert widths == {"192", "512", "1280"}, "the fixed width set (§6.4)"
    assert "data:image/jpeg" not in body
    assert 'loading="lazy"' in body
    assert 'width="192" height="108"' in body  # explicit box, CLS 0
    # A deduplicated frame says whose duplicate it is, rather than vanishing.
    assert "duplicate of #0" in body


def test_the_ocr_browser_draws_the_boxes_it_stored(client: TestClient) -> None:
    body = page(client, f"{ROOT}/videos/zduSFxRajkE")
    boxes = re.findall(r'class="ocrline" data-line="\d+"\s+data-box="([^"]+)"', body)
    assert len(boxes) == 3
    for box in boxes:
        values = [float(v) for v in box.split(",")]
        assert len(values) == 4 and all(0.0 <= v <= 1.0 for v in values)
    assert "paged kv cache" in body
    # The boxes are drawn over the frame from those same coordinates, as
    # percentages. That the page says so in prose is no longer the assertion:
    # the paragraph went in the 2026-08-10 cull, the geometry is the claim.
    assert re.search(r'class="ocrbox" aria-hidden="true" data-line="\d+"\s+style="left:[\d.]+%', body)


def test_the_transcript_browser_shows_the_chunk_boundaries(client: TestClient) -> None:
    body = page(client, f"{ROOT}/videos/kCc8FmEb1nY")
    assert "chunk 0 ·" in body
    assert "in-chunk" in body
    assert "we cache the keys and the values" in body
    assert "https://youtu.be/kCc8FmEb1nY?t=0" in body
    # words_json is described, never dumped (§5.3).
    assert "words_json" not in body


def test_the_detail_pagers_are_clamped_and_keep_each_other(client: TestClient) -> None:
    body = page(client, f"{ROOT}/videos/kCc8FmEb1nY?frames=1&cues=1")
    assert "frame_offset=1" in body and "cue_offset=1" in body
    # One pager's link carries the other's offset, so paging frames does not
    # reset the transcript.
    assert "frames=1&amp;frame_offset=1&amp;cues=1&amp;cue_offset=0" in body
    # Server-side clamps: the URL is an input, not an instruction.
    huge = page(client, f"{ROOT}/videos/kCc8FmEb1nY?frames=100000&cues=100000")
    assert huge.count('class="framecard') <= 96
    assert huge.count('class="cue ') <= 200


def test_an_unknown_video_is_a_typed_404(client: TestClient) -> None:
    body = page(client, f"{ROOT}/videos/nosuchvideo", status=404)
    assert "E_UNKNOWN_VIDEO" in body


# -------------------------------------------------------- 3b. §5.4 the jobs


def test_the_jobs_table_shows_the_countdown_that_was_invisible(
    client: TestClient,
) -> None:
    """dashboard.md §4.4: the single highest-value line on the page.

    `defer_job` has written `not_before` since the backoff landed and nothing
    ever read it, so "this job is deferred for another 240 seconds" was true
    and unobservable from every surface vidtheque had.
    """
    body = page(client, f"{ROOT}/jobs")
    for job in ("job_deferred01", "job_running001", "job_finished01"):
        assert job in body
    countdowns = re.findall(r'data-field="job-defer" data-defer="(\d+)"', body)
    assert len(countdowns) == 3, "every row carries the field, whatever its value"
    # Only the queued job is actually being held off. A `not_before` left behind
    # on a running row is a stamp, not a wait, and must not become a countdown.
    assert sorted(countdowns, key=int)[-1] != "0"
    live = [n for n in countdowns if n != "0"]
    assert len(live) == 1 and 200 < int(live[0]) <= 240
    assert "held <span" in body


def test_the_jobs_table_prints_when_a_job_finished(client: TestClient) -> None:
    """Tom, 2026-08-13: created is not enough.

    The column the table was missing is the one that says the batch is over.
    Three rows, three states: the finished job carries a clock formatted by
    `iso_minute` like every other stamp on this surface, and the two live ones
    carry the em dash rather than an empty cell — a blank in an instrument reads
    as a value that failed to arrive.

    The cell is patched by the tick because it is the one clock a row *acquires*
    while the page is open, and the string comes from the server for the same
    reason every other one does (§5.4).
    """
    body = page(client, f"{ROOT}/jobs")
    assert "<th scope=\"col\">finished</th>" in body
    cells = re.findall(r'<time data-field="job-finished">([^<]*)</time>', body)
    assert len(cells) == 3, "every row carries the field, whatever its value"
    assert cells.count("—") == 2  # queued and running have not finished
    stamped = [c for c in cells if c != "—"]
    assert len(stamped) == 1 and re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", stamped[0])
    # The tick reads the same string off the same projection.
    payload = client.get(f"{ROOT}/api/jobs").json()
    finished = next(j for j in payload["jobs"] if j["job_id"] == "job_finished01")
    assert finished["text"]["finished"] == stamped[0]
    running = next(j for j in payload["jobs"] if j["job_id"] == "job_running001")
    assert running["text"]["finished"] == "—"
    assert 'setText(scope, "job-finished"' in (STATIC / "jobs.js").read_text()


def test_the_jobs_table_is_clamped_and_pages_with_has_more(client: TestClient) -> None:
    one = page(client, f"{ROOT}/jobs?limit=1")
    assert "more available" in one
    assert "offset=1" in one
    assert one.count("<tr data-job=") == 1
    assert 'value="100"' in page(client, f"{ROOT}/jobs?limit=100000")
    # An unknown state is not honoured and not an error.
    assert "job_finished01" in page(client, f"{ROOT}/jobs?state=nonsense")
    active = page(client, f"{ROOT}/jobs?state=active")
    assert "job_running001" in active and "job_finished01" not in active


def test_job_triage_filters_order_and_polling_share_one_query(client: TestClient) -> None:
    assert "job_deferred01" in page(client, f"{ROOT}/jobs?error_code=E_RATE_LIMIT")
    assert "job_running001" not in page(
        client, f"{ROOT}/jobs?error_code=E_RATE_LIMIT"
    )
    degraded = page(client, f"{ROOT}/jobs?degraded=1")
    assert "job_finished01" in degraded
    assert "job_running001" not in degraded and "job_deferred01" not in degraded

    priority = page(client, f"{ROOT}/jobs?order=priority")
    assert priority.index("job_running001") < priority.index("job_deferred01")
    wall = page(client, f"{ROOT}/jobs?order=wall_clock")
    assert wall.index("job_finished01") < wall.index("job_running001")

    filtered = page(
        client,
        f"{ROOT}/jobs?state=all&kind=index&error_code=E_RATE_LIMIT"
        "&degraded=0&order=priority&limit=1",
    )
    poll = re.search(r'data-poll="([^"]+)"', filtered).group(1)
    assert "kind=index" in poll and "error_code=E_RATE_LIMIT" in poll
    assert "degraded=0" in poll and "order=priority" in poll
    older = re.findall(r'href="([^"]+)">Older', filtered)
    if older:
        assert all(part in older[0] for part in (
            "kind=index", "error_code=E_RATE_LIMIT", "degraded=0", "order=priority"
        ))
    payload = client.get(poll.replace("&amp;", "&")).json()
    assert [job["job_id"] for job in payload["jobs"]] == ["job_deferred01"]


def test_job_kind_filter_is_server_side(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        conn = open_write_connection(client.app.state.assembled.db.path)
        try:
            conn.execute("UPDATE jobs SET kind='reindex' WHERE public_id='job_finished01'")
        finally:
            conn.close()
        body = page(client, f"{ROOT}/jobs?kind=reindex")
        assert "job_finished01" in body
        assert "job_running001" not in body and "job_deferred01" not in body


def test_the_job_page_says_what_the_video_cost(client: TestClient) -> None:
    """§10.4: the clocks are the point, and the two of them mean different things."""
    body = page(client, f"{ROOT}/jobs/job_finished01")
    assert "wall clock" in body and "created → finished" in body
    assert "on the runner" in body and "first claim → finished" in body
    # 1600s of wall clock, 1500s on the runner: the difference is the wait.
    assert "26m 40s" in body and "25m 00s" in body
    # And the per-stage durations, for the item the job ended on.
    assert "Stage by stage" in body
    for stage in ("fetch", "stt", "chunk", "text_embed", "keyframe", "ocr", "frame_embed"):
        assert f"<code>{stage}</code>" in body


def test_the_job_page_carries_attempts_the_degraded_list_and_the_tail(
    client: TestClient,
) -> None:
    body = page(client, f"{ROOT}/jobs/job_finished01")
    assert "3/3" in body  # attempts against max_attempts — the retry counter
    assert "E_SOURCE" in body
    assert "Sign in to confirm you are not a bot" in body
    # `done` + `n_failed=0` + a failed stage is the silent loss this page names.
    assert "Finished with something missing" in body
    assert "<code>ocr</code> failed" in body
    assert "worker returned 503 for 41 frames" in body


def test_video_detail_has_one_bounded_recent_indexing_history_query(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        db = client.app.state.assembled.db.path
        conn = open_write_connection(db)
        try:
            conn.execute("BEGIN IMMEDIATE")
            video = int(
                conn.execute(
                    "SELECT id FROM videos WHERE public_id='kCc8FmEb1nY'"
                ).fetchone()[0]
            )
            for n in range(12):
                cursor = conn.execute(
                    "INSERT INTO jobs (owner_id, public_id, kind, args_json, n_items, "
                    "state, created_at, started_at, finished_at) VALUES "
                    "(1, ?, 'reindex', '{}', 1, 'done', unixepoch() + ?, "
                    "unixepoch() + ?, unixepoch() + ?)",
                    (f"job_history{n:02d}", n, n, n + 1),
                )
                conn.execute(
                    "INSERT INTO job_items (job_id, seq, source_url, video_id, state, "
                    "finished_at) VALUES (?, 0, 'https://youtu.be/kCc8FmEb1nY', ?, "
                    "'done', unixepoch() + ?)",
                    (int(cursor.lastrowid), video, n + 1),
                )
            conn.execute("COMMIT")
        finally:
            conn.close()

        body = page(client, f"{ROOT}/videos/kCc8FmEb1nY")
        panel = body[body.index('id="index-history"') :]
        assert "job_history11" in panel and "job_history02" in panel
        assert "job_history01" not in panel
        assert panel.count("<code>job_history") == 10
        assert "Latest 10 at most; no total is computed" in panel

        # The videos table is unchanged: history is a detail-only query, never
        # a per-row addition to the listing.
        assert _count_reads(client, f"{ROOT}/videos?limit=1") == _count_reads(
            client, f"{ROOT}/videos?limit=100"
        )


def test_video_history_shows_error_and_degraded_stage(client: TestClient) -> None:
    body = page(client, f"{ROOT}/videos/eMlx5fFNoYc")
    panel = body[body.index('id="index-history"') :]
    assert "job_finished01" in panel
    assert "failed" in panel and "index" in panel
    assert "<code>ocr</code>" in panel


def test_the_deferred_job_explains_itself(client: TestClient) -> None:
    body = page(client, f"{ROOT}/jobs/job_deferred01")
    assert "Waiting, not stuck" in body
    assert "E_RATE_LIMIT" in body
    # The event tail is the only place a non-rate-limit deferral exists at all,
    # which is why the log is printed rather than described (the sentence that
    # said so went in the 2026-08-10 cull; dashboard.md §4.4 keeps the fact).
    assert "retrying in 300s after E_RATE_LIMIT" in body
    assert 'class="events" data-events' in body


def test_an_unknown_job_is_a_typed_404(client: TestClient) -> None:
    assert "E_UNKNOWN_JOB" in page(client, f"{ROOT}/jobs/job_nope", status=404)
    missing = client.get(f"{ROOT}/api/jobs/job_nope")
    assert missing.status_code == 404
    assert missing.json()["error"] == "E_UNKNOWN_JOB"


def test_the_poll_target_is_the_page_projection_and_says_when_to_stop(
    client: TestClient,
) -> None:
    """§5.4: 2 s while anything is live, stopped when nothing is."""
    payload = client.get(f"{ROOT}/api/jobs").json()
    assert payload["poll_ms"] == 2000
    assert payload["live"] is True  # two of the three jobs are queued/running
    assert payload["pagination"]["limit"] == 25
    deferred = next(j for j in payload["jobs"] if j["job_id"] == "job_deferred01")
    assert 200 < deferred["defer_s"] <= 240
    # The strings the page rendered, so the tick cannot format them differently.
    assert deferred["text"]["defer"].endswith("s")
    assert deferred["text"]["counts"] == "0/1 done"

    # Nothing live in this filter, so a tab watching it stops.
    done = client.get(f"{ROOT}/api/jobs?state=done").json()
    assert done["live"] is False

    single = client.get(f"{ROOT}/api/jobs/job_running001").json()
    assert single["live"] is True
    assert single["job"]["state"] == "running"
    assert [i["text"]["attempts"] for i in single["items"]] == ["2/3", "0/3"]
    assert single["items"][0]["text"]["stage"] == "stt 42%"
    assert client.get(f"{ROOT}/api/jobs?limit=100000").json()["pagination"]["limit"] == 100


def test_the_jobs_pages_do_not_fan_out_per_row(client: TestClient) -> None:
    """§6.3 again, for the two pages phase 2 adds.

    One row and a hundred must cost the same number of reads — the degraded
    badge in particular is one grouped query for the page, not a probe per job.
    """
    one = _count_reads(client, f"{ROOT}/jobs?limit=1")
    many = _count_reads(client, f"{ROOT}/jobs?limit=100")
    # Four, not three, since round 4: what each job *contains* is one more
    # grouped read for the page — and still one for a hundred rows, which is
    # the only thing this test is about.
    assert one == many <= 4, f"{one} reads for 1 job, {many} for 100"
    detail = _count_reads(client, f"{ROOT}/jobs/job_finished01")
    assert detail <= 8, f"{detail} reads for one job page"


def test_the_same_secrets_do_not_come_back_through_the_mcp_tools(
    tmp_path: Path,
) -> None:
    """The redaction the test below asserts, defeated by quoting a job id.

    The jobs view renders its ids as links, deliberately — and `job-status` is
    annotated read-only, so the public mask keeps it registered. A visitor read
    an id off `/dashboard/jobs`, called the tool through `/mcp`, and got back
    exactly the two fields the page had just withheld. `corpus-summary` did the
    same with `video_stages.error`.

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
        for path in (f"{ROOT}/jobs", f"{ROOT}/jobs/job_finished01",
                     f"{ROOT}/jobs/job_deferred01"):
            body = page(demo, path)
            for secret in secrets:
                assert secret not in body, f"{secret} leaked on {path}"
        body = page(demo, f"{ROOT}/jobs/job_finished01")
        # …and keeps the codes, the counts and every clock.
        assert "E_SOURCE" in body and "1/2 done · 1 failed" in body
        assert "26m 40s" in body and "25m 00s" in body
        assert "Stage by stage" in body
        assert "Finished with something missing" in body and "<code>ocr</code> failed" in body
        assert "not published on this instance" in body
        held = page(demo, f"{ROOT}/jobs/job_deferred01")
        assert "Waiting, not stuck" in held and "held <span" in held
        # The JSON projection is the same one, not a second rule that can drift.
        single = demo.get(f"{ROOT}/api/jobs/job_finished01").json()
        assert single["job"]["error_message"] is None
        assert all(item["source_url"] is None for item in single["items"])
        assert all(event["message"] is None for event in single["events"])
        assert single["items"][1]["error_code"] == "E_SOURCE"
        assert single["items"][1]["took_s"] == 600

    # The owner's instance is the contrast: same page, both fields present.
    with make_client(tmp_path) as owner:
        body = page(owner, f"{ROOT}/jobs/job_finished01")
        assert "Sign in to confirm you are not a bot" in body
        assert "worker returned 503" in body
        assert owner.get(f"{ROOT}/api/jobs/job_finished01").json()["items"][1][
            "source_url"
        ] == "https://youtu.be/failedvideo"


# ------------------------------------------ 3c. relative frames, and the slash


def test_dashboard_html_frames_are_relative_and_the_api_stays_absolute(
    client: TestClient,
) -> None:
    """Phase 2's second half: a page knows its own host better than PUBLIC_URL.

    A preview on a tunnelled port rendered every thumbnail against a dead
    origin. The split lives in `thumb_url`, not in the signer — and the MCP
    side keeps absolute URLs, because an agent has no page to resolve against.
    """
    base = "http://localhost:8080"
    for path in (ROOT, f"{ROOT}/videos", f"{ROOT}/videos/kCc8FmEb1nY"):
        body = page(client, path)
        assert "/frames/" in body
        assert f"{base}/frames/" not in body, f"{path} embeds PUBLIC_URL"
        assert re.search(r'src="/frames/[\w:-]+\.jpg\?w=\d+', body)

    # The JSON facade is unchanged, on both prefixes and for the tools.
    thumbs = [
        v["thumb"]
        for v in client.get(f"{ROOT}/api/videos").json()["videos"]
        if v["thumb"]
    ]
    assert thumbs and all(t.startswith(f"{base}/frames/") for t in thumbs)


async def test_the_mcp_surface_still_hands_out_absolute_frame_urls(
    assembled, fake_embeddings
) -> None:
    """The other half of the same assertion, at the tool layer."""
    from vidtheque_mcp.tools import frames

    result = await frames.run(assembled.deps, video_id="kCc8FmEb1nY", limit=2)
    payload = result.structured_content or {}
    urls = [f["url"] for f in payload.get("frames", [])]
    assert urls and all(u.startswith("http://localhost:8080/frames/") for u in urls)


def test_a_relative_frame_url_still_carries_a_valid_signature(tmp_path: Path) -> None:
    """The signer covers the frame, the width, the quality and the expiry —
    never the origin — so dropping the origin cannot invalidate anything."""
    with make_client(tmp_path, auth_mode="token", token="s3cret") as client:
        headers = {"Authorization": "Bearer s3cret"}
        body = client.get(f"{ROOT}/videos/kCc8FmEb1nY", headers=headers).text
        match = re.search(r'src="(/frames/[^"]+)"', body)
        assert match, "no frame on the detail page"
        url = match.group(1).replace("&amp;", "&")
        assert "sig=" in url and "exp=" in url
        # No bearer, no cookie: the signature on the relative URL is the whole
        # credential, exactly as it is on an absolute one.
        assert client.get(url).status_code == 200
        assert client.get(url.split("&sig=")[0] + "&sig=forged").status_code == 401


def test_the_trailing_slash_redirects_rather_than_404ing(client: TestClient) -> None:
    """`Mount("/")` matches everything, so Starlette's own redirect never fires."""
    response = client.get(f"{ROOT}/", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == ROOT
    # The query survives, so a bookmarked filter with a stray slash still works.
    with_query = client.get(f"{ROOT}/?index_state=failed", follow_redirects=False)
    assert with_query.headers["location"] == f"{ROOT}?index_state=failed"
    assert client.get(f"{ROOT}/").status_code == 200


def test_the_pages_are_never_cached(client: TestClient) -> None:
    for path in (ROOT, f"{ROOT}/videos", f"{ROOT}/videos/kCc8FmEb1nY"):
        assert client.get(path).headers["cache-control"] == "no-store"
    # The stylesheet and the module are, though — they are the same bytes for
    # every reader.
    assert "max-age=" in client.get(f"{ROOT}/static/dashboard.css").headers["cache-control"]


def test_the_asset_route_serves_nothing_outside_its_directory(
    client: TestClient,
) -> None:
    assert client.get(f"{ROOT}/static/dashboard.js").status_code == 200
    assert client.get(f"{ROOT}/static/../../config.py").status_code == 404
    assert client.get(f"{ROOT}/static/nope.css").status_code == 404


def test_the_fonts_come_from_the_one_canonical_directory(
    client: TestClient,
) -> None:
    """`fonts/` is an alias onto public/static/fonts — the document of record.

    The dashboard used to carry byte-identical copies; now the route serves
    the canonical files, so equality is structural, not a discipline. The
    alias must stay as confined as the directory it replaced: no walking out
    of it, and the licence texts are still not assets.
    """
    face = client.get(f"{ROOT}/static/fonts/archivo-latin-wght-normal.woff2")
    assert face.status_code == 200
    assert face.headers["content-type"] == "font/woff2"
    assert "immutable" in face.headers["cache-control"]
    # The bytes are the canonical file's bytes, read straight from the package.
    import vidtheque_mcp.public as _public

    canonical = (
        Path(_public.__file__).parent
        / "static"
        / "fonts"
        / "archivo-latin-wght-normal.woff2"
    )
    assert face.content == canonical.read_bytes()
    # A face that is not in the canonical directory is a 404, not a fallback
    # to the dashboard's own directory.
    assert client.get(f"{ROOT}/static/fonts/nope.woff2").status_code == 404
    # The OFL texts travel with the fonts without being served — on this
    # route exactly as on the public one.
    assert client.get(f"{ROOT}/static/fonts/Archivo-OFL.txt").status_code == 404


# ------------------------------------------------------------------- 4. XSS


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/videos",
        "/videos/kCc8FmEb1nY",
        "/videos/aaaaaaaaaaa",
        "/videos/zduSFxRajkE",
        "/jobs",
        "/jobs/job_deferred01",
        "/jobs/job_finished01",
    ],
)
def test_no_corpus_string_ever_becomes_markup(client: TestClient, path: str) -> None:
    """demo-site.md §6.2, for a surface that renders more hostile text than the
    demo does: OCR lines, titles, channel names, yt-dlp's own error strings and
    — since phase 2 — the job event log, which is the runner quoting them.

    Jinja2's autoescape is the mechanism; this is the assertion that it is on
    everywhere and that nothing reached for `| safe`.
    """
    body = page(client, ROOT + path)
    # No corpus string ever opened a tag. The strings themselves are all over
    # these pages — as text.
    assert "<script>alert" not in body
    assert "<img src=x" not in body
    assert "<svg onload" not in body
    if "aaaaaaaaaaa" in path or path in ("/videos", "/jobs/job_deferred01",
                                         "/jobs/job_finished01"):
        assert "&lt;script&gt;alert" in body, "the hostile string is there, escaped"
        assert "&lt;img src=x onerror=alert(1)&gt;" in body


def test_the_templates_never_reach_for_safe() -> None:
    templates = Path(__file__).resolve().parents[1] / "src/vidtheque_mcp/dashboard/templates"
    for template in templates.glob("*.html"):
        text = template.read_text()
        assert "| safe" not in text, f"{template.name} disables autoescape"
        assert "|safe" not in text, f"{template.name} disables autoescape"
        assert "autoescape" not in text, f"{template.name} toggles autoescape inline"


def test_no_dashboard_module_builds_html_from_data() -> None:
    """The JS half of the same rule, asserted the way `app.js` already is.

    Every module in the bundle, not a named one: the poller appends live event
    rows, which is exactly the code path that reaches for `innerHTML` if nobody
    is watching, and its text is the runner quoting yt-dlp.
    """
    modules = sorted(STATIC.glob("*.js"))
    assert {m.name for m in modules} == {"dashboard.js", "jobs.js"}
    for module in modules:
        script = "\n".join(
            line
            for line in module.read_text().splitlines()
            if not line.strip().startswith(("//", "*", "/*"))
        )
        for sink in (
            "innerHTML",
            "outerHTML",
            "insertAdjacentHTML",
            "document.write",
            "eval(",
            "new Function",
            "srcdoc",
        ):
            assert sink not in script, f"{module.name} reaches for {sink}"
        assert "safeUrl(" in script, f"{module.name} filters URLs"
        assert "textContent" in script


# --- the copy cull (Tom, 2026-08-10)
#
# The dashboard "shouldn't try to sell you anything, or have leftover
# comment-like content". These two tests are the fence around that: the first
# pins the phrases that were removed so they cannot walk back in, the second
# bounds how much prose a slot may hold at all, because the failure mode is not
# any one sentence — it is a paragraph growing back one clause at a time.

# Exact strings the 2026-08-10 cull deleted. Every one of them was the page
# narrating or justifying itself; where the sentence carried a real fact, the
# fact moved to `docs/design/dashboard.md` and is cited in the commit.
CULLED_NARRATION = (
    "There is no scenes table",
    "Provenance records what",
    "not a report from the worker",
    "the unit a search hit points at",
    "the embedding unit",
    "one SQLite file, one writer",
    "summed from the column",
    "This is the management surface",
    "a self-hosted video-corpus MCP server",
    "Every row is one video",
    "normalised 0–1 at write time",
    "which is why a phrase that wraps",
    "nothing a person reads",
    "here and nowhere else",
    "is worse than no button",
    "that is the healthy state",
    "which the schema should not allow",
    "the whole of",
    "A script does not need this page",
    "so it is the one worth quoting",
    "an agent could not",
    "rather than refuses",
)

# The prose slots, and the ceiling on each. The tight group is page copy; the
# loose group is control documentation, which DESIGN.md sanctions as a
# `--prose`-capped sentence under a control and which genuinely needs to spell
# out a format. Neither ceiling is a style preference — both are the point at
# which a note becomes an essay.
PROSE_SLOTS = {
    "panel-note": (2, 170),
    "notice-detail": (2, 170),
    "notice-next": (2, 170),
    "empty-note": (2, 170),
    "empty-lead": (2, 170),
    "stage-alarm": (2, 170),
    "deployment-why": (2, 170),
    "field-help": (2, 240),
    "check-note": (2, 170),
    # A ledger column's third element. Six words, by construction.
    "figure-note": (1, 56),
}


def _prose_blocks(body: str) -> list[tuple[str, str]]:
    """Every rendered prose slot on a page, as (slot, plain text).

    `<dd>` as well as `<p>`, because a ledger column's note is the third
    element of a `<div class="figure">` and is exactly the slot most likely to
    grow a sentence.
    """
    out = []
    for _tag, classes, inner in re.findall(
        r'<(p|dd) class="([^"]*)">(.*?)</\1>', body, re.S
    ):
        for slot in classes.split():
            if slot not in PROSE_SLOTS:
                continue
            text = re.sub(r"<[^>]+>", "", inner)
            out.append((slot, " ".join(text.split())))
    return out


def _every_dashboard_page(tmp_path: Path) -> list[tuple[str, str]]:
    """One rendering of every template this surface has, as (label, html)."""
    pages = []
    with make_client(tmp_path) as anon:
        for path in (
            "", "/videos", "/videos?published_after=2099-01-01",
            "/videos/kCc8FmEb1nY", "/videos/aaaaaaaaaaa", "/videos/zduSFxRajkE",
            "/jobs", "/jobs?state=failed", "/jobs/job_finished01",
            "/jobs/job_deferred01", "/jobs/job_running001",
        ):
            pages.append((path or "/", page(anon, ROOT + path)))
        pages.append(("404", page(anon, f"{ROOT}/videos/nosuchvideo", status=404)))
    with owner_client(tmp_path) as owner:
        pages.append(("/login", page(owner, f"{ROOT}/login")))
        sign_in(owner)
        pages.append(("/index", page(owner, f"{ROOT}/index")))
    with make_client(tmp_path, public=PublicSettings(enabled=True)) as demo:
        for path in ("/jobs", "/jobs/job_finished01"):
            pages.append((f"demo {path}", page(demo, ROOT + path)))
    return pages


def test_no_dashboard_page_narrates_itself(tmp_path: Path) -> None:
    """The sentences Tom ordered removed stay removed."""
    for label, body in _every_dashboard_page(tmp_path):
        for phrase in CULLED_NARRATION:
            assert phrase not in body, f"{label} narrates itself: {phrase!r}"


def test_every_prose_slot_stays_inside_its_ceiling(tmp_path: Path) -> None:
    """A note may state a fact. It may not argue for the page it is on."""
    for label, body in _every_dashboard_page(tmp_path):
        for slot, text in _prose_blocks(body):
            sentences, chars = PROSE_SLOTS[slot]
            found = len(re.findall(r"[.!?](?:\s|$)", text))
            assert found <= sentences, (
                f"{label} .{slot} runs to {found} sentences: {text!r}"
            )
            assert len(text) <= chars, (
                f"{label} .{slot} is {len(text)} chars: {text!r}"
            )


def test_the_pages_carry_no_inline_script(client: TestClient) -> None:
    tags = (
        f'<script type="module" src="{ROOT}/static/dashboard.js"></script>',
        f'<script type="module" src="{ROOT}/static/jobs.js"></script>',
    )
    for path in ("", "/videos", "/videos/kCc8FmEb1nY", "/jobs", "/jobs/job_running001"):
        body = page(client, ROOT + path)
        for tag in tags:
            body = body.replace(tag, "")
        assert "<script" not in body, "the page stays CSP-ready"


# -------------------------------------------------------------- 5. the world


def test_the_dashboard_palette_matches_the_demos() -> None:
    """The demo page is not registered in private mode, so the dashboard ships
    its own copy of the six role properties. This is the thing that stops the
    copy drifting into a second visual world.

    Retargeted 2026-08-10 with the projection-room system (DESIGN.md, migration
    notes). Two things changed. The system is **single-scheme** — dark only, no
    `prefers-color-scheme` block and no toggle — so the expected count is six
    rather than twelve. And the six are **aliases**: DESIGN.md's frontmatter
    declares `bg: {colors.pitch}`, `accent: {colors.gold}` and so on, and an
    alias never carries its own value. So each one is resolved through its
    `var(--…)` chain and the two files are compared as *mappings*: what has to
    agree is the colour each role resolves to, not the spelling and not the
    order the six happen to be declared in. The old positional read was written
    when both files were six literal hexes at the top; with a ground scale under
    them the two surfaces group their tokens differently (the demo declares
    `--fg` with the ink, the dashboard with the roles) and neither is wrong.
    """

    ROLES = ("bg", "fg", "muted", "line", "accent", "raised")

    def palette(css: str) -> dict[str, str]:
        declared: dict[str, str] = {}
        for block in re.findall(r":root \{(.*?)\}", css, re.S):
            for name, value in re.findall(r"--([\w-]+):\s*([^;]+);", block):
                declared[name] = value.strip()

        def resolve(value: str, depth: int = 0) -> str:
            alias = re.fullmatch(r"var\(--([\w-]+)\)", value)
            if alias and depth < 8:
                return resolve(declared.get(alias.group(1), value), depth + 1)
            return value

        return {name: resolve(declared[name]) for name in ROLES if name in declared}

    demo = palette((DEMO_STATIC / "style.css").read_text())
    dash = palette((STATIC / "dashboard.css").read_text())
    assert len(demo) == 6, "six roles, one scheme"  # not twelve: dark only now
    assert dash == demo


def test_both_schemes_and_a_mobile_viewport_are_declared(client: TestClient) -> None:
    """The name is the one DESIGN.md's migration notes cite; there is one scheme
    now. A projection room does not have a day mode, so the page declares dark
    and stops: one `theme-color`, `color-scheme: dark`, and no light palette to
    switch to.
    """
    body = page(client, ROOT)
    assert body.count('name="theme-color"') == 1
    assert 'content="#040405"' in body
    assert 'content="dark"' in body
    assert "width=device-width" in body
    css = (STATIC / "dashboard.css").read_text()
    assert "color-scheme: dark" in css
    assert "@media (prefers-color-scheme" not in css, "dark only: no second scheme"
    assert "prefers-reduced-motion: reduce" in css
    # The table stops being a table rather than scrolling the page sideways.
    assert "max-width: 52rem" in css


@pytest.mark.parametrize(
    "path", ["/videos/kCc8FmEb1nY", "/jobs", "/jobs/job_finished01"]
)
def test_the_pages_are_landmarked_and_keyboard_reachable(
    client: TestClient, path: str
) -> None:
    body = page(client, ROOT + path)
    for landmark in ("<header", "<main", "<footer", "<h1", '<nav class="nav"'):
        assert landmark in body
    assert body.count("<h1") == 1
    assert 'class="skip" href="#main"' in body
    assert 'aria-current="page"' in body
    if path.count("/") > 1:  # a detail page: every panel heading is announced
        assert body.count("aria-labelledby=") >= 3
    else:  # a table page: the grid says what it is
        assert '<caption class="sr-only">' in body


def test_the_dashboard_is_not_indexable(client: TestClient) -> None:
    assert 'name="robots" content="noindex, nofollow"' in page(client, ROOT)


# ------------------------------------------------------- 6. the rate limiter


def test_the_dashboard_bucket_is_installed_in_every_mode(tmp_path: Path) -> None:
    """dashboard.md §2.5.3: the limiter loses its mode conditional."""
    with make_client(tmp_path, dashboard=DashboardSettings(rate_per_min=2)) as client:
        assert client.get(ROOT).status_code == 200
        assert client.get(f"{ROOT}/videos").status_code == 200
        refused = client.get(f"{ROOT}/api/videos")
        assert refused.status_code == 429
        assert refused.json()["bucket"] == "dashboard"
        assert "retry-after" in refused.headers


def test_a_private_deployment_still_serves_frames_unbucketed(tmp_path: Path) -> None:
    """One detail page asks for ~48 frames; a 120/min bucket would refuse the
    second page load, and the owner is not that bucket's threat model."""
    with make_client(tmp_path, dashboard=DashboardSettings(rate_per_min=1)) as client:
        assert client.get(ROOT).status_code == 200
        for _ in range(4):
            assert client.get("/frames/kCc8FmEb1nY-00000.jpg?w=192&q=70").status_code == 200


def test_the_public_buckets_are_untouched(tmp_path: Path) -> None:
    with make_client(
        tmp_path,
        public=PublicSettings(enabled=True, search_per_min=2),
        dashboard=DashboardSettings(enabled=False),
    ) as client:
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


def _count_reads(client: TestClient, path: str) -> int:
    db = client.app.state.assembled.db
    original = db.read
    calls = 0

    async def spy(fn, budget_s=None):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return await original(fn, budget_s)

    db.read = spy  # type: ignore[method-assign]
    try:
        page(client, path)
    finally:
        db.read = original  # type: ignore[method-assign]
    return calls


def test_no_page_issues_a_query_per_row(client: TestClient) -> None:
    """§6.3: no page issues a query per row, ever.

    Measured rather than argued, and measured as a *shape* rather than a
    threshold: rendering one frame and rendering twenty-four must cost the same
    number of reads. A per-row fan-out is precisely the thing that would make
    those two numbers differ.
    """
    detail = f"{ROOT}/videos/kCc8FmEb1nY"
    small = _count_reads(client, f"{detail}?frames=1&cues=1")
    large = _count_reads(client, f"{detail}?frames=96&cues=200")
    assert small == large, f"{small} reads for 1 frame, {large} for 96"
    assert large < 20, f"{large} reads for one detail page"

    # And the table: fifty rows must not become two hundred coverage probes.
    one = _count_reads(client, f"{ROOT}/videos?limit=1")
    many = _count_reads(client, f"{ROOT}/videos?limit=100")
    assert one == many < 10, f"{one} reads for 1 row, {many} for 100"


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
        assert client.get(f"{ROOT}/index").status_code == 404
        assert client.get(f"{ROOT}/login").status_code == 404
        # And every read page is still open, which is the other half of the rule.
        assert client.get(ROOT).status_code == 200


def test_a_refusal_never_points_at_a_page_that_is_not_registered(
    tmp_path: Path,
) -> None:
    """The read gate's 401 offers the sign-in page only where there is one.

    Read-only plus `token` is a real deployment — a credentialed public mirror
    — and it gates its reads while having no write side at all. A refusal that
    sent that reader to `/dashboard/login` would be telling them a second
    untruth on the way out.
    """
    with owner_client(tmp_path) as private:
        refused = private.get(ROOT)
        assert refused.status_code == 401
        assert f"{ROOT}/login" in refused.text
        assert f"{ROOT}/login" in private.get(f"{ROOT}/api/videos").json()["next"]

    with owner_client(tmp_path, readonly=True) as demo:
        refused = demo.get(ROOT)
        assert refused.status_code == 401
        assert f"{ROOT}/login" not in refused.text
        assert "Bearer" in refused.text
        assert "login" not in demo.get(f"{ROOT}/api/videos").json()["next"]


def test_the_write_side_is_absent_in_readonly_mode_not_merely_refused(
    tmp_path: Path,
) -> None:
    """§2.3, with a credential configured — so the *flag* is doing the work.

    This is the deployment Tom ships publicly: welcome page plus the read-only
    projection through a tunnel. The write side must be missing, not refusing.
    """
    with owner_client(tmp_path, readonly=True) as demo:
        assert demo.get(f"{ROOT}/videos", headers=BEARER).status_code == 200
        for path in WRITE_POSTS:
            assert demo.post(path, headers={**BEARER, **SAME_ORIGIN}).status_code == 404
        assert demo.get(f"{ROOT}/index", headers=BEARER).status_code == 404
        assert demo.get(f"{ROOT}/login", headers=BEARER).status_code == 404
        # No affordance survives the projection either (§2.4's table).
        table = demo.get(f"{ROOT}/videos", headers=BEARER).text
        assert "Re-index" not in table
        assert "Add videos" not in table


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
            f"{ROOT}/jobs/job_running001/cancel",
            f"{ROOT}/jobs/job_finished01/retry",
            f"{ROOT}/videos/kCc8FmEb1nY/reindex",
            f"{ROOT}/videos/kCc8FmEb1nY/tags",
        ):
            # 404 rather than 405: `Mount("/")` is a full match for the path,
            # so the router never falls back to the method-mismatch answer the
            # POST-only route would have given. Either way nothing fires.
            assert client.get(path).status_code in (404, 405), path
        # `/index` has a GET and it is the form, which reads and never writes.
        assert client.get(f"{ROOT}/index").status_code == 200


def test_cancel_is_guarded_and_honest_for_running_and_deferred_jobs(
    tmp_path: Path,
) -> None:
    with owner_client(tmp_path) as client:
        sign_in(client)
        running = client.post(
            f"{ROOT}/jobs/job_running001/cancel",
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert running.status_code == 303
        assert running.headers["location"] == f"{ROOT}/jobs/job_running001"
        running_page = page(client, f"{ROOT}/jobs/job_running001")
        assert "cancel requested" in running_page
        assert 'data-field="job-state">running<' in running_page

        deferred = client.post(
            f"{ROOT}/jobs/job_deferred01/cancel",
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert deferred.status_code == 303
        deferred_page = page(client, f"{ROOT}/jobs/job_deferred01")
        assert 'data-field="job-state">cancelled<' in deferred_page
        assert 'data-field="job-defer" data-defer="0"' in deferred_page
        assert 'id="deferred"' not in deferred_page
        assert "cancel requested" not in deferred_page

        finished = client.post(
            f"{ROOT}/jobs/job_finished01/cancel", headers=SAME_ORIGIN
        )
        assert finished.status_code == 400
        assert "already failed" in finished.text


def test_cancel_actions_exist_only_for_live_jobs_on_the_write_side(
    tmp_path: Path,
) -> None:
    with owner_client(tmp_path) as client:
        sign_in(client)
        table = page(client, f"{ROOT}/jobs")
        assert f'{ROOT}/jobs/job_running001/cancel' in table
        assert f'{ROOT}/jobs/job_deferred01/cancel' in table
        assert f'{ROOT}/jobs/job_finished01/cancel' not in table
        assert "Cancel this job" in page(client, f"{ROOT}/jobs/job_running001")
        assert "Cancel this job" not in page(client, f"{ROOT}/jobs/job_finished01")

    with owner_client(tmp_path, readonly=True) as demo:
        table = demo.get(f"{ROOT}/jobs", headers=BEARER).text
        assert "/cancel" not in table and ">Cancel<" not in table


def test_retry_queues_only_failed_and_degraded_items_with_original_policy(
    tmp_path: Path,
) -> None:
    with owner_client(tmp_path) as client:
        sign_in(client)
        db = client.app.state.assembled.db.path
        conn = open_write_connection(db)
        try:
            conn.execute("BEGIN IMMEDIATE")
            job = int(
                conn.execute(
                    "SELECT id FROM jobs WHERE public_id='job_finished01'"
                ).fetchone()[0]
            )
            degraded_url = str(
                conn.execute(
                    "SELECT source_url FROM job_items WHERE job_id=? AND seq=0", (job,)
                ).fetchone()[0]
            )
            successful = conn.execute(
                "SELECT id, url FROM videos WHERE public_id='kCc8FmEb1nY'"
            ).fetchone()
            conn.execute(
                "UPDATE jobs SET n_items=3, priority=50, args_json=? WHERE id=?",
                (
                    json.dumps(
                        {
                            "expand": "none",
                            "max_items": 25,
                            "tags": ["topic:repair", "series:jobs"],
                            "channels": "transcript,ocr",
                        }
                    ),
                    job,
                ),
            )
            conn.execute(
                "INSERT INTO job_items (job_id, seq, source_url, video_id, state, "
                "attempts, finished_at) VALUES (?, 2, ?, ?, 'done', 1, unixepoch())",
                (job, successful["url"], successful["id"]),
            )
            conn.execute("COMMIT")
        finally:
            conn.close()

        # One new job and nothing to explain follows `index_submit`'s rule:
        # a 303 to the job now running, never a POST body a reload repeats —
        # a reloaded retry receipt would queue the repair twice.
        receipt = client.post(
            f"{ROOT}/jobs/job_finished01/retry",
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert receipt.status_code == 303
        location = receipt.headers["location"]
        assert location.startswith(f"{ROOT}/jobs/job_")
        new_id = location.rsplit("/", 1)[1]
        assert new_id != "job_finished01"
        detail = page(client, location)
        assert "built-in method" not in detail

        conn = open_write_connection(db)
        try:
            new = conn.execute(
                "SELECT id, args_json, priority, n_items FROM jobs WHERE public_id=?",
                (new_id,),
            ).fetchone()
            items = conn.execute(
                "SELECT source_url FROM job_items WHERE job_id=? ORDER BY seq",
                (new["id"],),
            ).fetchall()
        finally:
            conn.close()
        assert new["priority"] == 50 and new["n_items"] == 2
        assert json.loads(new["args_json"])["channels"] == "transcript,ocr"
        assert json.loads(new["args_json"])["tags"] == ["topic:repair", "series:jobs"]
        assert {row["source_url"] for row in items} == {
            "https://youtu.be/failedvideo",
            degraded_url,
        }
        assert successful["url"] not in {row["source_url"] for row in items}


def test_retry_is_absent_without_a_repair_subset_and_from_readonly(
    tmp_path: Path,
) -> None:
    with owner_client(tmp_path) as client:
        sign_in(client)
        assert "/retry" in page(client, f"{ROOT}/jobs/job_finished01")
        assert "/retry" not in page(client, f"{ROOT}/jobs/job_running001")
    with owner_client(tmp_path, readonly=True) as demo:
        body = demo.get(f"{ROOT}/jobs/job_finished01", headers=BEARER).text
        assert "/retry" not in body and "Retry" not in body


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


def test_a_trusted_peer_reads_the_pages_it_may_write_from(tmp_path: Path) -> None:
    """The gates are symmetric (2026-08-13, Tom's call on the field finding).

    §3.4 already granted a trusted peer the whole write side, and §4's policy
    table calls that peer an owner — but `guarded()` checked only the bearer
    and the session, so a LAN peer could submit an index job and get a sign-in
    page for the dashboard it posted from. A network trusted to change the
    corpus but not to read it is the "boundary with no shape" §3.4 names, now
    in both directions. Socket peer only, as everywhere else: the forged-header
    client outside the CIDR stays refused, page and JSON both.
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
        # No credential presented, page and JSON both: the peer is the credential.
        assert inside.get(ROOT).status_code == 200
        assert inside.get(f"{ROOT}/api/videos").status_code == 200

    outside = TestClient(
        lan_app(), base_url="http://localhost:8080", client=("203.0.113.7", 4444)
    )
    with outside:
        forged = {"CF-Connecting-IP": "10.9.9.9", "X-Forwarded-For": "10.9.9.9"}
        assert outside.get(ROOT, headers=forged).status_code == 401
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


def test_the_login_page_mints_the_existing_cookie_and_no_new_system(
    tmp_path: Path,
) -> None:
    """§3.2 rule 2: same cookie name, same `login_sessions` row, same flags."""
    from vidtheque_mcp.auth.login import SESSION_COOKIE

    with owner_client(tmp_path) as client:
        form = client.get(f"{ROOT}/login")
        assert form.status_code == 200
        assert "VIDTHEQUE_PASSWORD" in form.text
        assert "kCc8FmEb1nY" not in form.text, "the sign-in page leaks no corpus"

        wrong = client.post(
            f"{ROOT}/login", data={"password": "hunter2"}, headers=SAME_ORIGIN
        )
        assert wrong.status_code == 401
        # One message for both secrets: naming which one was wrong is a hint
        # about which one this deployment has.
        assert "does not match this instance" in wrong.text
        assert not client.cookies.get(SESSION_COOKIE)

        good = client.post(
            f"{ROOT}/login",
            data={"password": PASSWORD, "next": f"{ROOT}/jobs"},
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert good.status_code == 303
        assert good.headers["location"] == f"{ROOT}/jobs"
        cookie = good.headers["set-cookie"]
        assert cookie.startswith(f"{SESSION_COOKIE}=")
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
        assert "Secure" not in cookie  # PUBLIC_URL is http in this fixture

        # A row in the table the OAuth login already writes, not a parallel one.
        sid = client.cookies.get(SESSION_COOKIE)
        store = client.app.state.assembled.auth.store
        assert store is not None and store.load_session(sid) == "owner"

        # And it is now a credential for the read pages too, with no bearer.
        assert client.get(ROOT).status_code == 200


def test_the_login_refuses_an_open_redirect_and_a_cross_origin_post(
    tmp_path: Path,
) -> None:
    with owner_client(tmp_path) as client:
        for away in ("https://evil.example/steal", "//evil.example", "/etc/passwd"):
            response = client.post(
                f"{ROOT}/login",
                data={"password": PASSWORD, "next": away},
                headers=SAME_ORIGIN,
                follow_redirects=False,
            )
            assert response.headers["location"] == ROOT, away

    with owner_client(tmp_path) as other:
        cross = other.post(
            f"{ROOT}/login",
            data={"password": PASSWORD},
            headers={"Origin": "https://evil.example"},
        )
        assert cross.status_code == 403
        assert "another origin" in cross.text


def test_the_token_is_the_secret_when_no_password_is_set(tmp_path: Path) -> None:
    """The curl path, typed once into a browser (§3.2 rule 2)."""
    with owner_client(tmp_path, password=None) as client:
        assert "VIDTHEQUE_TOKEN" in client.get(f"{ROOT}/login").text
        assert client.post(
            f"{ROOT}/login",
            data={"password": TOKEN},
            headers=SAME_ORIGIN,
            follow_redirects=False,
        ).status_code == 303
        assert client.get(ROOT).status_code == 200


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
        assert client.get(ROOT).status_code == 401


def test_an_expired_browser_session_is_sent_back_to_the_login(tmp_path: Path) -> None:
    """A form POST from a page whose cookie died gets a page, not a JSON blob."""
    with owner_client(tmp_path) as client:
        refused = client.post(
            f"{ROOT}/videos/kCc8FmEb1nY/reindex",
            headers={**SAME_ORIGIN, "Accept": "text/html"},
            follow_redirects=False,
        )
        assert refused.status_code == 303
        assert refused.headers["location"].startswith(f"{ROOT}/login?next=")


# --- 8.4 the index form


def test_the_index_form_splits_a_paste_into_jobs_of_ten(tmp_path: Path) -> None:
    """§10.7, resolved: the ten-URL cap stays on the MCP surface and the form
    splits server-side — which is what the 2026-08-09 straggler run did by
    hand, 64 videos into 7 jobs."""
    from vidtheque_mcp.dashboard.writes import URLS_PER_JOB

    ids = [f"vid{n:08d}" for n in range(23)]  # 11 characters, as YouTube ids are
    with owner_client(tmp_path) as client:
        sign_in(client)
        response = client.post(
            f"{ROOT}/index", data={"urls": "\n".join(ids)}, headers=SAME_ORIGIN
        )
        assert response.status_code == 200
        job_ids = set(re.findall(rf"{ROOT}/jobs/(job_[A-Za-z0-9_-]+)", response.text))
        assert len(job_ids) == 3, response.text[-2000:]
        # And the page says so: a split the operator cannot see is a job count
        # they cannot explain.
        assert "split into" in response.text

        counts = sorted(
            client.get(f"{ROOT}/api/jobs/{job}").json()["job"]["n_items"]
            for job in job_ids
        )
        assert counts == [3, 10, 10]
        assert max(counts) == URLS_PER_JOB


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


def test_the_index_form_is_bounded_like_every_other_input(tmp_path: Path) -> None:
    """The paste box is an input, not an instruction (§6.1)."""
    from vidtheque_mcp.dashboard.writes import MAX_FORM_URLS

    with owner_client(tmp_path) as client:
        sign_in(client)
        empty = client.post(f"{ROOT}/index", data={"urls": "  \n "}, headers=SAME_ORIGIN)
        assert empty.status_code == 400
        assert "E_BAD_PARAM" in empty.text

        before = len(client.get(f"{ROOT}/api/jobs").json()["jobs"])
        flood = "\n".join(f"vid{n:08d}" for n in range(MAX_FORM_URLS + 1))
        refused = client.post(f"{ROOT}/index", data={"urls": flood}, headers=SAME_ORIGIN)
        assert refused.status_code == 413
        assert str(MAX_FORM_URLS) in refused.text
        # Nothing was queued: the cap is checked before the first job.
        assert len(client.get(f"{ROOT}/api/jobs").json()["jobs"]) == before


def test_the_index_form_passes_the_tools_refusal_through_verbatim(
    tmp_path: Path,
) -> None:
    """The form adds no policy (§5.5): a bad tag is `index-video`'s refusal, in
    its own words, on the page that submitted it."""
    with owner_client(tmp_path) as client:
        sign_in(client)
        response = client.post(
            f"{ROOT}/index",
            data={"urls": "vid00000042", "tags": "NotATag"},
            headers=SAME_ORIGIN,
        )
        assert response.status_code == 409
        assert "E_BAD_PARAM" in response.text
        assert "namespace" in response.text
        # The typed URLs come back with it: a refusal that empties the box is a
        # refusal that costs the operator the paste.
        assert "vid00000042" in response.text


def test_the_index_form_refuses_honestly_when_indexing_is_disabled(
    tmp_path: Path,
) -> None:
    """§5.5: disabled with the reason, not accepting a doomed submission."""
    with owner_client(tmp_path) as client:
        sign_in(client)
        client.app.state.assembled.db.writes_allowed = False
        try:
            form = client.get(f"{ROOT}/index")
            assert "Indexing is disabled" in form.text
            assert "disabled" in form.text  # the controls, not only the banner
            refused = client.post(
                f"{ROOT}/index", data={"urls": "vid00000043"}, headers=SAME_ORIGIN
            )
            assert refused.status_code == 409
            assert "E_FEATURE_DISABLED" in refused.text
        finally:
            client.app.state.assembled.db.writes_allowed = True


def test_get_index_prefills_a_bounded_draft_without_queueing(tmp_path: Path) -> None:
    """The deep-link contract is render-only: POST is still the write."""
    from vidtheque_mcp.dashboard.writes import (
        MAX_PREFILL_TAGS_CHARS,
        MAX_PREFILL_URLS_CHARS,
    )

    source = "https://www.youtube.com/watch?v=kCc8FmEb1nY&list=PL_test"
    with owner_client(tmp_path) as client:
        sign_in(client)
        before = [j["job_id"] for j in client.get(f"{ROOT}/api/jobs").json()["jobs"]]
        response = client.get(
            f"{ROOT}/index",
            params={"urls": source, "expand": "channel_recent", "tags": "topic:attention"},
        )
        assert response.status_code == 200

        urls = re.search(r'<textarea id="urls"[^>]*>(.*?)</textarea>', response.text, re.S)
        tags = re.search(r'<input id="tags"[^>]*value="([^"]*)"', response.text, re.S)
        assert urls and unescape(urls.group(1)) == source
        assert tags and unescape(tags.group(1)) == "topic:attention"
        assert '<option value="channel_recent" selected>' in response.text
        assert [j["job_id"] for j in client.get(f"{ROOT}/api/jobs").json()["jobs"]] == before

        # Unrecognised enum values fall back to the full form's default, and
        # free text cannot make an unbounded response body.
        bounded = client.get(
            f"{ROOT}/index",
            params={
                "urls": "u" * (MAX_PREFILL_URLS_CHARS + 200),
                "expand": "everything",
                "tags": "t" * (MAX_PREFILL_TAGS_CHARS + 200),
            },
        ).text
        bounded_urls = re.search(r'<textarea id="urls"[^>]*>(.*?)</textarea>', bounded, re.S)
        bounded_tags = re.search(r'<input id="tags"[^>]*value="([^"]*)"', bounded, re.S)
        assert bounded_urls and len(unescape(bounded_urls.group(1))) == MAX_PREFILL_URLS_CHARS
        assert bounded_tags and len(unescape(bounded_tags.group(1))) == MAX_PREFILL_TAGS_CHARS
        assert '<option value="playlist" selected>' in bounded

        hostile = client.get(
            f"{ROOT}/index", params={"urls": HOSTILE, "tags": HOSTILE}
        ).text
        assert "<script>alert" not in hostile and "<img src=x" not in hostile
        assert "&lt;script&gt;alert" in hostile


def test_adding_videos_lives_in_the_rail_and_nowhere_else(tmp_path: Path) -> None:
    """Tom, 2026-08-13: the overview's quick-add form is gone.

    §13 gave the surface two entry points to one POST. The rail item is on every
    page; the form on the overview was a second one that also *decided* for the
    operator what a playlist URL meant, through three hidden fields, and spent a
    panel of the page's scarcest dimension doing it. What the removal must not
    break is the handler behind it, so the POST those defaults produced is still
    exercised here — from the form that renders them.
    """
    with owner_client(tmp_path) as client:
        sign_in(client)
        overview = page(client, ROOT)
        assert "data-quick-add" not in overview
        assert 'action="/dashboard/index"' not in overview
        # …and the one entry point is still one click away, on this page too.
        assert f'data-add-videos href="{ROOT}/index"' in overview

        queued = client.post(
            f"{ROOT}/index",
            data={
                "urls": "quickadd001",
                "expand": "none",
                "max_items": "25",
                "priority": "normal",
            },
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert queued.status_code == 303
        assert queued.headers["location"].startswith(f"{ROOT}/jobs/job_")


def test_video_detail_deep_links_to_the_channel_prefill(tmp_path: Path) -> None:
    with owner_client(tmp_path) as client:
        sign_in(client)
        detail = page(client, f"{ROOT}/videos/kCc8FmEb1nY")
        match = re.search(r'data-queue-channel href="([^"]+)"', detail)
        assert match
        target = urlsplit(unescape(match.group(1)))
        assert target.path == f"{ROOT}/index"
        assert parse_qs(target.query) == {
            "urls": ["https://youtu.be/kCc8FmEb1nY"],
            "expand": ["channel_recent"],
        }


def test_nothing_on_the_write_side_offers_a_delete(tmp_path: Path) -> None:
    """§5.2: `jobs.kind='delete'` has no pipeline, so it gets no button."""
    with owner_client(tmp_path) as client:
        sign_in(client)
        for path in (f"{ROOT}/index", f"{ROOT}/videos", f"{ROOT}/videos/kCc8FmEb1nY"):
            body = page(client, path)
            assert "/delete" not in body
            assert ">Delete<" not in body
        assert client.post(
            f"{ROOT}/videos/kCc8FmEb1nY/delete", headers=SAME_ORIGIN
        ).status_code == 404


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

    The button does not get to override that, and the page does not invent its
    own wording for it: this is `index-video`'s `E_INDEXING`, verbatim, with a
    way back to the video it was pressed on.
    """
    with owner_client(tmp_path) as client:
        sign_in(client)
        held = client.post(f"{ROOT}/videos/kCc8FmEb1nY/reindex", headers=SAME_ORIGIN)
        assert held.status_code == 409
        assert "E_INDEXING" in held.text
        assert f"{ROOT}/videos/kCc8FmEb1nY" in held.text


def test_re_indexing_an_unknown_video_is_a_typed_404(tmp_path: Path) -> None:
    with owner_client(tmp_path) as client:
        sign_in(client)
        missing = client.post(f"{ROOT}/videos/nope/reindex", headers=SAME_ORIGIN)
        assert missing.status_code == 404
        assert "E_UNKNOWN_VIDEO" in missing.text


def test_tagging_calls_the_tool_and_keeps_its_rules(tmp_path: Path) -> None:
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
        body = page(client, detail)
        assert ">topic:attention<" in body and ">series:zero-to-hero<" in body

        removed = client.post(
            f"{detail}/tags",
            data={"remove": "topic:attention"},
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert removed.status_code == 303
        assert ">topic:attention<" not in page(client, detail)

        # The tool's namespace rule, verbatim, and nothing applied.
        bad = client.post(
            f"{detail}/tags", data={"add": "Shouty:Tag"}, headers=SAME_ORIGIN
        )
        assert bad.status_code == 400
        assert "E_BAD_PARAM" in bad.text
        assert "namespace" in bad.text

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
        assert "E_UNKNOWN_VIDEO" in missing.text


# --- 8.6 the surface stays itself


def test_the_sign_in_page_has_its_own_much_tighter_bucket(tmp_path: Path) -> None:
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
        # The read pages are on the loose bucket and are not collateral.
        assert client.get(f"{ROOT}/videos").status_code in (200, 401)


def test_the_write_pages_carry_no_inline_script_and_no_corpus_markup(
    tmp_path: Path,
) -> None:
    """The two rules the whole surface is asserted against, on the new pages."""
    module = f'<script type="module" src="{ROOT}/static/dashboard.js"></script>'
    with owner_client(tmp_path) as client:
        assert "<script" not in client.get(f"{ROOT}/login").text.replace(module, "")
        sign_in(client)
        for path in (f"{ROOT}/index", f"{ROOT}/videos/aaaaaaaaaaa"):
            body = page(client, path)
            assert "<script" not in body.replace(module, ""), path
            assert "<script>alert" not in body
            assert "<img src=x" not in body


def test_the_write_affordances_appear_only_with_the_write_side(
    tmp_path: Path,
) -> None:
    """The controls and the routes are the same decision, made once (§2.4)."""
    with owner_client(tmp_path) as client:
        # The shared chrome includes the route even before a session exists.
        assert f'data-add-videos href="{ROOT}/index"' in page(client, f"{ROOT}/login")
        sign_in(client)
        for path, status in (
            (ROOT, 200),
            (f"{ROOT}/videos", 200),
            (f"{ROOT}/videos/kCc8FmEb1nY", 200),
            (f"{ROOT}/jobs", 200),
            (f"{ROOT}/jobs/job_deferred01", 200),
            (f"{ROOT}/index", 200),
            (f"{ROOT}/videos/not-here", 404),
        ):
            assert f'data-add-videos href="{ROOT}/index"' in page(client, path, status)
        assert "Add videos" in page(client, ROOT)
        assert "data-queue-channel" in page(client, f"{ROOT}/videos/kCc8FmEb1nY")
        assert "Re-index" in page(client, f"{ROOT}/videos")
        assert 'id="manage"' in page(client, f"{ROOT}/videos/kCc8FmEb1nY")
        assert "Sign out" in page(client, ROOT)

        # The filtered empty state is still an empty jobs page, and it points
        # straight at the same index form.
        assert f'data-empty-add href="{ROOT}/index"' in page(
            client, f"{ROOT}/jobs?state=done"
        )

    with make_client(tmp_path) as none_mode:  # auth=none
        overview = page(none_mode, ROOT)
        assert "Add videos" not in overview
        assert "Sign out" not in overview
        # …and it says why, with the one-line fix (§3.2 rule 3).
        assert "VIDTHEQUE_AUTH=token" in overview
        assert "Re-index" not in page(none_mode, f"{ROOT}/videos")
        assert 'id="manage"' not in page(none_mode, f"{ROOT}/videos/kCc8FmEb1nY")
        assert "data-queue-channel" not in page(
            none_mode, f"{ROOT}/videos/kCc8FmEb1nY"
        )
        assert "data-empty-add" not in page(none_mode, f"{ROOT}/jobs?state=done")

    with owner_client(tmp_path, readonly=True) as demo:
        overview = demo.get(ROOT, headers=BEARER).text
        assert "data-add-videos" not in overview
        assert "data-queue-channel" not in demo.get(
            f"{ROOT}/videos/kCc8FmEb1nY", headers=BEARER
        ).text
        assert "data-empty-add" not in demo.get(
            f"{ROOT}/jobs?state=done", headers=BEARER
        ).text


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

READ_PAGES = (
    ROOT,
    f"{ROOT}/search",
    f"{ROOT}/videos",
    f"{ROOT}/videos/kCc8FmEb1nY",
    f"{ROOT}/jobs",
    f"{ROOT}/jobs/job_finished01",
)


def test_the_demo_serves_every_read_page_and_none_of_the_write_ones(
    tmp_path: Path,
) -> None:
    """§2.4's table, top to bottom, as one composed assertion.

    Phase 4's headline: the flag that used to gate only the demo page and
    `/api` now also decides which *dashboard* you get. Nothing here is new
    machinery — the write side has been absent under this flag since phase 3
    and the jobs redaction since phase 2 — so the thing worth asserting is the
    composition, which no single earlier test covered.
    """
    with make_client(tmp_path, public=DEMO) as demo:
        for path in READ_PAGES:
            assert demo.get(path).status_code == 200, path
        # The corpus is still whole on the two pages §2.4 gives the demo
        # unredacted: every state, every video, the seven stages.
        table = page(demo, f"{ROOT}/videos")
        assert "aaaaaaaaaaa" in table and "kCc8FmEb1nY" in table
        assert table.count('class="cov cov-') == 4 * 3
        detail = page(demo, f"{ROOT}/videos/kCc8FmEb1nY")
        for stage in ("fetch", "stt", "chunk", "text_embed", "keyframe", "ocr"):
            assert stage in detail
        # And the write side is absent, not refusing (§2.3).
        registered = {str(getattr(r, "path", "")) for r in demo.app.routes}
        assert not (registered & set(WRITE_ROUTES))
        assert demo.get(f"{ROOT}/index").status_code == 404
        assert demo.get(f"{ROOT}/login").status_code == 404
        assert "Add videos" not in table and "Re-index" not in table


def test_the_overview_projection_keeps_the_corpus_and_drops_the_box(
    tmp_path: Path,
) -> None:
    """§2.4: "counts, channels, tags, coverage — no settings, no paths".

    The delta `docs/deploy-public.md` §1.1 flagged and this phase closes: the
    overview was rendering the declared checkpoint ids, the drift reason and
    two byte totals to anonymous traffic. Asserted **both ways** in one test,
    so a redaction that grows to swallow the corpus fails here.
    """
    with make_client(tmp_path, public=DEMO) as demo:
        body = page(demo, ROOT)
        for leaked in OPERATOR_STRINGS:
            assert leaked not in body, f"{leaked} is on the demo overview"
        # …and the corpus is all still there.
        assert "transcript cues" in body and "on-screen lines" in body
        assert "Andrej Karpathy" in body and "topic:attention" in body
        assert "Recently indexed" in body and "What is missing" in body
        assert re.search(r'class="pill tone-\w+">(ok|partial|degraded|indexing|empty)<', body)
        # The rail says what the reader may do and stops there.
        assert "read-only demo" in body
        # The way back into the search engine, which exists under this flag and
        # only under it.
        assert 'href="/">Search the corpus' in body

    with make_client(tmp_path) as owner:
        body = page(owner, ROOT)
        for kept in OPERATOR_STRINGS:
            assert kept in body, f"{kept} vanished from the owner's overview"
        assert 'href="/">Search the corpus' not in body


def test_the_drift_reason_is_the_operators_sentence_and_the_effect_is_not(
    tmp_path: Path,
) -> None:
    """The banner splits rather than disappearing.

    A visitor is entitled to know that search is answering from full-text —
    it changes what the results mean. They are not entitled to the dimension
    mismatch that caused it, which names models and is addressed to whoever set
    the environment.
    """
    reason = "the worker is serving other-model but the corpus used qwen."
    for public, expect_reason in ((DEMO, False), (PublicSettings(enabled=False), True)):
        with make_client(tmp_path, public=public) as client:
            client.app.state.assembled.db.vectors.disable(reason)
            body = page(client, ROOT)
            assert "full-text" in body
            assert (reason in body) is expect_reason
            assert ("The corpus and the worker disagree" in body) is expect_reason


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
        assert demo.get(ROOT).status_code == 200
        assert demo.get(f"{ROOT}/videos").status_code == 200
        refused = demo.get(f"{ROOT}/jobs")
        assert refused.status_code == 429
        assert refused.json()["bucket"] == "dashboard"
        # The demo's own buckets are untouched by it.
        assert demo.get("/api/videos").status_code == 200


def test_the_projection_still_goes_through_the_byte_capped_frame_cache(
    tmp_path: Path,
) -> None:
    """§6.4: three fixed widths, never base64, on the public surface too."""
    with make_client(tmp_path, public=DEMO) as demo:
        for path in (ROOT, f"{ROOT}/videos", f"{ROOT}/videos/kCc8FmEb1nY"):
            body = page(demo, path)
            # The favicon is a drawn `data:` SVG in the `<head>` and always was;
            # what must never appear is a *frame* inlined as bytes.
            assert "src=\"data:image" not in body
            for width in set(re.findall(r"/frames/[\w.-]+\.jpg\?w=(\d+)", body)):
                assert int(width) in (192, 512, 1280), f"{width} on {path}"


# --- 9.1 codex gap 1: the queue, on the first screen (§5.1)


def test_the_overview_counts_the_queue_and_links_into_it(client: TestClient) -> None:
    """Active, deferred and recently-failed — the numbers, and where they go.

    The fixture is the three shapes the jobs view exists for: one queued job
    held off by a `not_before` in the future, one running, and one that failed
    an hour and a half ago. So the counts are 2 active (1 of them deferred) and
    1 recently failed, and each figure is a link into the jobs view already
    filtered to what it counted.
    """
    body = page(client, ROOT)
    assert "The queue" in body
    assert f'href="{ROOT}/jobs?state=active">2</a>' in body
    assert f'href="{ROOT}/jobs?state=failed">1</a>' in body
    assert "1 of them waiting on a backoff" in body
    assert "failed in the last 24 hours" in body
    # The links are real filters, not decoration.
    assert "job_running001" in page(client, f"{ROOT}/jobs?state=active")
    assert "job_finished01" in page(client, f"{ROOT}/jobs?state=failed")


def test_the_queue_read_is_one_query_and_the_projection_keeps_it(
    tmp_path: Path,
) -> None:
    """It is a corpus-shaped fact, so it survives the demo (§2.4) — and it is
    one grouped statement, because the overview is the one page that answers
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
        assert "The queue" in page(demo, ROOT)


# --- 9.2 codex gap 2: the date filters (§5.2)


def test_the_date_filters_narrow_both_axes_and_never_overload_them(
    client: TestClient,
) -> None:
    """CLAUDE.md's two-axis rule, as two controls.

    `published_*` picks videos and `indexed_*` picks when this box did the
    work; the fixture publishes across 2023–2025 and indexed all three ready
    videos at one moment, so a filter that confused them would be visible here.
    """
    after = page(client, f"{ROOT}/videos?published_after=2024-01-01")
    assert "kCc8FmEb1nY" not in after  # published 2023-01-17
    assert "zduSFxRajkE" in after and "eMlx5fFNoYc" in after

    # The named day is *included*: `< before` is the clause, so `before`
    # resolves to the start of the next day. A range that dropped everything
    # published on its own end date would read as a bug because it is one.
    on_the_day = page(client, f"{ROOT}/videos?published_before=2024-02-20")
    assert "zduSFxRajkE" in on_the_day and "kCc8FmEb1nY" in on_the_day
    assert "eMlx5fFNoYc" not in on_the_day

    # The other axis, and the video with no `indexed_at` at all is not caught
    # by it.
    indexed = page(client, f"{ROOT}/videos?indexed_after=2025-06-01")
    assert "kCc8FmEb1nY" in indexed and "aaaaaaaaaaa" not in indexed
    assert "aaaaaaaaaaa" in page(client, f"{ROOT}/videos?index_state=indexing")


def test_the_date_filters_compose_with_every_other_filter_and_carry(
    client: TestClient,
) -> None:
    """A filtered table is a link somebody can send — all of it, not most."""
    both = page(
        client,
        f"{ROOT}/videos?published_after=2024-01-01&index_state=ready&has=transcript"
        "&order=title&limit=2",
    )
    assert "zduSFxRajkE" in both and "kCc8FmEb1nY" not in both
    # Every sort head carries the range — a filter that survives one click and
    # not the next is a filter the reader has to re-apply.
    assert both.count("published_after=2024-01-01") == 4  # the four sortable columns
    # …and the page says out loud what is narrowing it.
    assert "published <span" in both and "2024-01-01" in both

    # So does the pager, which only exists when there is a second page.
    paged = page(client, f"{ROOT}/videos?published_after=2024-01-01&limit=1")
    assert "more available" in paged
    assert "published_after=2024-01-01&amp;published_before=&amp;" in paged
    assert "offset=1" in paged


def test_the_date_filters_are_clamped_and_canonicalised_server_side(
    client: TestClient,
) -> None:
    """A date in the URL bar is an input, and the page shows what it became.

    Two things happen server-side and both are then *visible*: a generous
    spelling (`30d ago`, `today`) resolves to a UTC day, and an absurd one is
    clamped to a year out. Silently applying either would be the narrowing
    CLAUDE.md forbids; applying it and printing it is the filter explaining
    itself.
    """
    import time as _time

    from vidtheque_mcp.text import iso_day

    ceiling = iso_day(int(_time.time()) + 365 * 86_400)
    clamped = page(client, f"{ROOT}/videos?published_before=2999-01-01")
    assert f'value="{ceiling}"' in clamped
    assert "2999" not in clamped

    today = iso_day(int(_time.time()))
    relative = page(client, f"{ROOT}/videos?indexed_after=today")
    assert f'value="{today}"' in relative
    assert "indexed_after=today" not in relative  # the link carries the day

    # An overlong value is truncated rather than handed to the parser whole.
    assert client.get(f"{ROOT}/videos?published_after={'9' * 400}").status_code in (200, 400)


def test_a_date_that_will_not_parse_is_a_typed_refusal_not_a_dropped_filter(
    client: TestClient,
) -> None:
    """`timeparse` refuses rather than ignoring, and the page says so.

    A silently dropped filter is a table reporting the wrong result set with
    total confidence. The other seven controls survive the refusal, so the one
    that broke can be fixed without losing the query.
    """
    response = client.get(f"{ROOT}/videos?published_after=lastwinter&channel=GPU+MODE")
    assert response.status_code == 400
    body = response.text
    assert "E_BAD_TIME_FORMAT" in body
    assert "lastwinter" in body
    assert 'value="GPU MODE"' in body  # the rest of the band is still set
    assert "kCc8FmEb1nY" not in body  # and no rows are claimed


# --- 9.3 the welcome page's one link


def test_the_welcome_page_gains_its_link_into_the_browsable_corpus(
    tmp_path: Path,
) -> None:
    """§2.4: the demo page keeps its aesthetic and gains one link.

    It is hidden in the markup and unhidden by `/api/meta`, so a deployment
    that turned the route group off — or the edge rule in
    `deploy/cloudflared.example.yml` that 404s `^/dashboard` — does not leave
    an invitation to a dead page in the masthead.
    """
    with make_client(tmp_path, public=DEMO) as demo:
        # `/demo` since 2026-08-11: the landing took `/` (demo-site.md §1).
        body = demo.get("/demo").text
        assert 'id="browse"' in body and 'href="/dashboard"' in body
        assert "Browse the corpus" in body
        assert demo.get("/api/meta").json()["browse"] == ROOT
        # One link, not a nav: the welcome page is a search engine (§6).
        assert body.count('class="browse"') == 1

    with make_client(
        tmp_path, public=DEMO, dashboard=DashboardSettings(enabled=False)
    ) as off:
        assert off.get("/api/meta").json()["browse"] is None
        assert off.get(ROOT).status_code == 404


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


def test_the_pages_keep_the_owner_page_size_and_that_is_the_decision(
    tmp_path: Path,
) -> None:
    """§2.4: what phase 5 keyed off the credential is the JSON, not the HTML.

    The hatch is an `/api/*` parameter and reaches no page — no template renders
    untruncated transcript text and no page takes `max_text_chars`. What is left
    between the policies on a page is rows-per-page, on a listing the demo
    publishes in full anyway, so keying it off the credential would paginate the
    browsable corpus at 24 rows to protect nothing.
    """
    with make_client(tmp_path, public=PublicSettings(enabled=True)) as demo:
        body = page(demo, f"{ROOT}/videos?limit=100000")
        assert "max_text_chars" not in body
    # And the page's bound is still the server's, not the URL's.
    assert OWNER_CLAMPS.videos_max_limit == 100


# ------------------------------- 9. the digest, and the wrap sweep behind it
#
# Tom, 2026-08-10: "there are some weird wrapping issues notably and make sure
# nothing is out of place", and the OCR matches block "eats a huge column of
# vertical space" on dense slides. The design that answered both is DESIGN.md,
# **The digest**; what a CPU test can hold is the half a screenshot cannot check
# twice — that the bounds are real numbers, that the expander counts the list it
# is actually holding, and that the full text never left the page.

DENSE_SLIDE_LINES = 30


def _dense_corpus(tmp_path: Path) -> Path:
    """The fixture corpus with one keyframe carrying a slide's worth of text."""
    data = _corpus(tmp_path)
    conn = open_write_connection(data / "vidtheque.db")
    try:
        conn.execute("BEGIN IMMEDIATE")
        keyframe = conn.execute(
            "SELECT k.id, k.video_id, k.t_s FROM keyframes k "
            "JOIN videos v ON v.id = k.video_id "
            "WHERE v.source_id = 'kCc8FmEb1nY' ORDER BY k.ord LIMIT 1"
        ).fetchone()
        start = int(
            conn.execute(
                "SELECT COALESCE(MAX(line_no), -1) + 1 FROM ocr_lines WHERE keyframe_id = ?",
                (keyframe["id"],),
            ).fetchone()[0]
        )
        for offset in range(DENSE_SLIDE_LINES):
            conn.execute(
                "INSERT INTO ocr_lines (keyframe_id, video_id, t_s, line_no, text, conf, "
                "x0, y0, x1, y1) VALUES (?, ?, ?, ?, ?, 0.9, 0, 0, 1, 1)",
                (
                    keyframe["id"],
                    keyframe["video_id"],
                    keyframe["t_s"],
                    start + offset,
                    f"block_bytes = 2 * block_size * num_kv_heads, row {offset}",
                ),
            )
        conn.execute("COMMIT")
    finally:
        conn.close()
    return data


def _figure(body: str, frame_id: str) -> str:
    """The one card in the frames grid that belongs to this frame.

    Since the merge (round 4, item 2) that is a `<li class="framecard">` and
    not a `<figure>`: one card is the whole of a frame, and it is found by the
    `data-ocrframe` it has always carried.
    """
    start = body.index(f'data-ocrframe="{frame_id}"')
    start = body.rindex('<li class="framecard', 0, start)
    # To the next card, or to the end of the grid: a card's own `</li>` cannot
    # be found by searching, because the OCR lines inside it are `<li>`s too.
    after = body.find('<li class="framecard', start + 1)
    return body[start : after if after != -1 else body.index("</ul>", start)]


def test_a_dense_slide_prints_every_line_in_one_scrolling_list(
    tmp_path: Path,
) -> None:
    """The OCR panel is a scrollbox now (Tom, 2026-08-10), not a digest.

    What replaced the `+ N more` expander is one continuous `<ol>` of every
    line, bounded in CSS rather than in markup. The reason is the linkage: two
    lists meant the box↔line highlight could only be wired for the lines a
    stylesheet could enumerate, and an opened expander then held lines that
    pointed at nothing.
    """
    _dense_corpus(tmp_path)
    with make_client(tmp_path) as client:
        body = page(client, f"{ROOT}/videos/kCc8FmEb1nY")

    figure = _figure(body, "kCc8FmEb1nY-00000")
    total = figure.count('<li class="ocrline"')

    # Every line the server read is in the markup, in one list, with no
    # expander and no second list to hold a remainder.
    assert total == DENSE_SLIDE_LINES + 1  # the slide, plus the fixture's own line
    assert figure.count('<ol class="ocrlines"') == 1
    for gone in ("digest", "ocrlines-head", "ocrlines-rest", "more line(s)"):
        assert gone not in figure, f"{gone} survived the scrollbox"
    assert "<script" not in figure


def test_every_ocr_line_and_its_box_are_paired_by_index(tmp_path: Path) -> None:
    """The linkage is complete, and that is the whole point of the redesign.

    `data-line` is the index of a line in its frame's own ordered list, and the
    box drawn over the still carries the same number. Because it is an index
    rather than a position in the DOM, the pairing holds for **every** line —
    the previous design capped it at the eight the stylesheet could name as
    `:has()` pairs, so a dense slide had twenty-six lines that lit nothing.
    """
    _dense_corpus(tmp_path)
    with make_client(tmp_path) as client:
        body = page(client, f"{ROOT}/videos/kCc8FmEb1nY")
    figure = _figure(body, "kCc8FmEb1nY-00000")

    lines = re.findall(r'<li class="ocrline" data-line="(\d+)"', figure)
    boxes = re.findall(r'<span class="ocrbox" aria-hidden="true" data-line="(\d+)"', figure)
    assert lines and lines == boxes, "a box with no line, or a line with no box"
    assert lines == [str(n) for n in range(len(lines))], "the indexes are the list order"

    # And no enumerated pair survives in the stylesheet: one of them coming
    # back would be a second, shorter answer to the same question.
    css = (STATIC / "dashboard.css").read_text()
    assert not re.search(r"\.ocrlines[^{]*nth-child\(\d+\)", css)
    assert ".ocrbox.is-lit" in css and ".ocrline.is-lit" in css


def test_a_frame_is_one_card_that_opens_the_one_overlay(tmp_path: Path) -> None:
    """§5.3 + Tom, 2026-08-10, and the merge that finished the argument.

    A frame used to be drawn twice — once in the strip and once in the OCR
    grid — and the second pass made both open the same overlay. Round 4 merged
    them: there is one card, it carries the still, the detection boxes, the
    lines and every `data-*` the lightbox reads, and one delegated click
    handler serves it. A second opener would be a second place for the lightbox
    contract to drift; a second *card* was a second place for everything else.
    """
    with make_client(tmp_path) as client:
        body = page(client, f"{ROOT}/videos/kCc8FmEb1nY")
    card = _figure(body, "kCc8FmEb1nY-00000")
    assert 'class="framebtn"' in card
    for attribute in ("data-large=", "data-frame=", "data-caption=", "data-link="):
        assert attribute in card, attribute
    # Still the 512px variant from the fixed width set, never base64.
    assert re.search(r'src="/frames/[\w.-]+\.jpg\?w=512', card)

    # One card per frame on the page, and no trace of the second grid.
    assert body.count('class="framecard') == body.count("data-ocrframe=")
    for gone in ("ocrgrid", "ocrstage", "ocrframe ", 'id="ocr-', 'id="ocr"'):
        assert gone not in body, f"{gone} survived the merge"
    # The card is still the shot bar's anchor and the evidence mark's target.
    assert 'id="frame-0"' in card and "data-shot=" in card


def test_the_event_log_shows_the_newest_and_counts_the_older(tmp_path: Path) -> None:
    """The same primitive on the other unbounded block.

    Newest first, so the bounded half is the half that answers "what just
    happened"; an overnight batch's other fifty rows keep their count and their
    place.
    """
    data = _corpus(tmp_path)
    conn = open_write_connection(data / "vidtheque.db")
    try:
        job = int(
            conn.execute(
                "SELECT id FROM jobs WHERE public_id='job_deferred01'"
            ).fetchone()[0]
        )
        conn.execute("BEGIN IMMEDIATE")
        for n in range(20):
            conn.execute(
                "INSERT INTO job_events (job_id, at, level, message) VALUES "
                "(?, unixepoch() - ?, 'info', ?)",
                (job, 100 + n, f"stage keyframe: decoded {n * 250} frames"),
            )
        conn.execute("COMMIT")
    finally:
        conn.close()

    with make_client(tmp_path) as client:
        body = page(client, f"{ROOT}/jobs/job_deferred01")

    section = body[body.index('id="events"') :]
    preview, _, older = section.partition('<details class="digest">')
    total = section.count('<li class="event"')
    assert preview.count('<li class="event"') == EVENT_PREVIEW
    assert older.count('<li class="event"') == total - EVENT_PREVIEW
    assert (
        f'<span class="digest-count">{total - EVENT_PREVIEW}</span> older event(s)'
        in older
    )
    # `jobs.js` prepends a live event into `[data-events]`, so that attribute
    # stays on the list the reader is looking at rather than inside the drawer.
    assert 'class="events" data-events' in preview
    assert "data-events" not in older


def test_the_live_tick_looks_for_an_event_across_the_whole_digest() -> None:
    """Split the log in two and a poller that only knows the first list
    re-prepends every event that has scrolled into the second one."""
    script = (STATIC / "jobs.js").read_text()
    known = script[script.index("const known = new Set(") :][:200]
    assert 'document.querySelectorAll("[data-event]")' in known


def _rule(css: str, selector: str) -> str:
    """The declaration block of the first rule whose selector list starts here.

    Anchored to the start of a line, because these selectors are also named in
    the prose above them and a comment is not a rule.
    """
    match = re.search("^" + re.escape(selector) + r"[^{}]*\{([^}]*)\}", css, re.M)
    assert match, f"no rule for {selector}"
    return match.group(1)


def test_a_corpus_string_can_never_take_the_page_sideways() -> None:
    """The No-Sideways Rule, at the four places a corpus string broke it.

    Measured on 2026-08-10 against a fixture carrying a 78-character unbroken
    token in a video title, a 130-character channel name and a yt-dlp traceback
    with a signed URL in it: the video title took the document to 1445px at a
    1440px viewport, the overview's arrival list to 572px at 390, and the
    degraded list on the jobs page squeezed a video id to one character per line
    getting there.
    """
    css = (STATIC / "dashboard.css").read_text()
    for selector in (".pagehead h1", ".row-title", ".errtext, .eventtext"):
        assert "overflow-wrap: anywhere" in _rule(css, selector), selector
    # Flex line-breaking uses the hypothetical main size, so an `auto` basis on
    # the row body let a long title push the thumbnail onto a line of its own.
    assert "flex: 1 1 0" in _rule(css, ".row-body")


def test_the_scroll_wrapper_is_the_containing_block_for_what_it_clips() -> None:
    """`.sr-only` is `position: absolute` with no offsets.

    Without a positioned ancestor its containing block is the page, an
    out-of-flow box is not clipped by a scroll container it merely sits inside,
    and the coverage column's invisible descriptions scrolled the whole videos
    page 185px sideways at 1024 (measured 2026-08-10).
    """
    css = (STATIC / "dashboard.css").read_text()
    wrapper = _rule(css, ".tablewrap")
    assert "position: relative" in wrapper
    assert "overflow-x: auto" in wrapper
    assert "position: absolute" in _rule(css, ".sr-only")


def test_a_wrapped_meta_strip_never_starts_a_line_with_a_separator(
    tmp_path: Path,
) -> None:
    """The separator belongs to the fact before it.

    Written the other way round the only break opportunity in the strip was in
    front of the middot, so a strip that wrapped began its second line with a
    dangling `· en · indexed 2025-08-03`.
    """
    templates = Path(__file__).resolve().parents[1] / "src/vidtheque_mcp/dashboard/templates"
    for name in ("overview.html", "videos.html", "video.html", "jobs.html", "job.html"):
        body = (templates / name).read_text()
        for block in re.findall(r'<p class="pagehead-meta">(.*?)</p>', body, re.S):
            assert not re.search(r'\s<span class="sep">·</span>', block), (
                f"{name}: a middot with whitespace in front of it can start a line"
            )
    # And it is still one strip on the page, not a run of naked separators.
    with make_client(tmp_path) as client:
        assert '</span><span class="sep">·</span>' in page(
            client, f"{ROOT}/videos/kCc8FmEb1nY"
        )


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
    literal, so the next bump cannot leave a surface behind.
    """
    import tomllib

    from vidtheque_mcp import __version__

    root = Path(__file__).resolve().parents[2]
    declared = tomllib.loads((root / "mcp/pyproject.toml").read_text())["project"]["version"]
    worker = tomllib.loads((root / "worker/pyproject.toml").read_text())["project"]["version"]
    assert __version__ == declared == worker == "0.0.2"

    with make_client(tmp_path) as client:
        assert client.get("/healthz").json()["version"] == __version__
        assert f"<code>{__version__}</code>" in page(client, ROOT)


def test_the_rail_drops_the_heading_that_named_nothing(client: TestClient) -> None:
    """Item 13. Three routes are their own label; the groups that say what a
    deployment *is* keep theirs."""
    body = page(client, ROOT)
    assert "The index" not in body
    assert '<ul class="navlist">' in body  # the links themselves are untouched


def test_the_video_header_labels_both_states_as_one_object(client: TestClient) -> None:
    """Item 1. A bare pill, a floating label and a second bare pill were three
    objects with nothing saying which name owned which state.

    §4.5 is why both have to be named at all: the four `data_status`
    vocabularies are deliberately not unified, so a bare `ready` beside a bare
    `no_frames` reads as the page contradicting itself.
    """
    body = page(client, f"{ROOT}/videos/kCc8FmEb1nY")
    assert '<span class="statepair-key">index_state</span>' in body
    # Every key is joined to a pill, never left floating.
    for pair in re.findall(r'<span class="statepair">(.*?)</span>\s*</span>', body, re.S):
        assert "statepair-key" in pair and 'class="pill' in pair


def test_the_transcript_is_a_scrollbox_headed_by_the_videos_totals(
    client: TestClient,
) -> None:
    """Item 6, amended by round 4's item 4.

    No "Next N cues" button as the primary control — the pager stays in the
    markup as the no-JavaScript path and the appender's own fallback when a
    fetch is refused, and is not the thing the reader has to click.

    What changed in round 4 is the line above the box. It used to print the
    *position* (`cues 1–150 of 1,203`), which the scrollbar already said, which
    moved under the reader on every appended batch, and which answered nothing
    a reader of a transcript wants. It prints the video's own totals now, and
    the word count uses the chunk marker's definition so the two agree.
    """
    # `?cues=2` is the only way this fixture has more than one batch: six cues
    # is under the default page, and a scrollbox with nothing to append is not
    # what this test is about.
    body = page(client, f"{ROOT}/videos/kCc8FmEb1nY?cues=2")
    assert 'class="cuebox"' in body and "data-cuelist" in body
    assert "cue-range" not in body and "cue-position" not in body
    header = re.search(
        r'<p class="cuepos" data-field="cue-totals">(.*?)</p>', body, re.S
    )
    assert header, "the totals line"
    figures = re.findall(r'<span class="mono">([\d,]+)</span>\s*(\w+)', header.group(1))
    assert [word for _, word in figures] == ["cues", "words", "chars"]
    counted = {word: int(value.replace(",", "")) for value, word in figures}
    assert counted["cues"] == 6  # the count "What was stored" already read
    # The same definition the chunk marker uses — `len(text.split())` — over
    # the fixture's own six cues, and the characters they are made of.
    cues = [
        "we cache the keys and the values at every new token",
        "otherwise you would recompute attention over the entire prefix",
        "which is quadratic in the sequence length",
        "the cache makes it linear in the number of new tokens",
        "and the price you pay for that is memory",
        "much later we talk about tokenization instead",
    ]
    assert counted["words"] == sum(len(c.split()) for c in cues)
    assert counted["chars"] == sum(len(c) for c in cues)

    assert 'data-cue-total="6"' in body and 'data-cue-more="yes"' in body
    assert "data-cue-pager" in body  # the fallback survives, hidden by the script
    # And the appender no longer maintains a moving number of its own.
    script = (STATIC / "dashboard.js").read_text()
    assert "cue-range" not in script


def test_the_transcript_batches_arrive_preformatted(client: TestClient) -> None:
    """The appender's source. Same clamps as the page, every string formatted
    server-side, `has_more` and never a total."""
    payload = client.get(
        f"{ROOT}/api/videos/kCc8FmEb1nY/cues?offset=0&limit=100000"
    ).json()
    assert payload["limit"] == 200  # CUE_PAGE_MAX, not the URL's number
    assert payload["has_more"] is False
    assert "total" not in payload
    cue = payload["cues"][0]
    # The script assigns these verbatim; it owns no clock and no chunk label.
    assert re.fullmatch(r"\d+:\d\d(:\d\d)?", cue["at"])
    assert set(cue) == {"at", "t", "text", "speaker", "conf", "in_chunk", "chunk"}
    assert client.get(f"{ROOT}/api/videos/nosuchvideo1/cues").status_code == 404


def test_the_chunk_marker_counts_words_as_well_as_characters(
    client: TestClient,
) -> None:
    """Item 5. Characters are what the chunker clamps on; words are what a
    human has an intuition for. Both, and the text itself stays off the page."""
    body = page(client, f"{ROOT}/videos/kCc8FmEb1nY")
    assert re.search(r"chunk \d+ · [\d:]+–[\d:]+ · \d+ words · \d+ chars", body)


def test_the_transcript_drops_the_per_cue_origin_badge(client: TestClient) -> None:
    """Item 4. `whisperx` on a thousand rows is not a label.

    The fact is not lost: "What was stored" prints it once per origin with a
    count, and the `stt` row in Provenance names the model that produced it.
    """
    body = page(client, f"{ROOT}/videos/kCc8FmEb1nY")
    assert "badge-spoken" not in body and "badge-screen" not in body
    assert "whisperx" in body  # still counted, once, in the band above


def test_the_jobs_view_reports_liveness_through_the_work_and_not_a_badge(
    client: TestClient,
) -> None:
    """Items 11 and 12.

    The `live` badge is gone — on a dashboard served by the process that holds
    the runner it was always true and therefore said nothing. What replaced it
    is three real signals, and the one this test can hold is the gate:
    `is-working` appears on a `running` job and on nothing else.
    """
    body = page(client, f"{ROOT}/jobs")
    assert 'class="live"' not in body and "livedot" not in body

    rows = re.findall(r'<tr data-job="([^"]+)">(.*?)</tr>', body, re.S)
    working = {job for job, row in rows if "is-working" in row}
    running = {job for job, row in rows if re.search(r'data-field="job-state">running<', row)}
    assert working == running == {"job_running001"}
    # And the clock only ticks for a job that is still live.
    ticking = re.findall(r'data-wall="(\d*)"', body)
    assert ticking.count("") == 1  # the finished one, and only it


def test_the_state_markers_in_a_cell_are_one_height(client: TestClient) -> None:
    """Item 10. The countdown was 26px beside a 17px pill and the cell read as
    two kinds of object. One token, `--chip-h`, and every marker takes it."""
    css = (STATIC / "dashboard.css").read_text()
    for selector in (".pill", ".countdown", ".statepair-key"):
        # Anchored to the rule whose selector is *only* this one: every marker
        # here is also a member of the two-channel mono selector list, and
        # `_rule` would find that block first.
        match = re.search("^" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.M)
        assert match, selector
        assert "height: var(--chip-h)" in match.group(1), selector


def test_a_frame_is_not_a_control_and_does_not_take_the_control_height() -> None:
    """A geometry bug the fixture hid, found in review 2026-08-10.

    The chassis gives every `button` the 34px control height. `.framebtn` is a
    button holding a 16/9 picture, and a *definite* height beats an aspect
    ratio — so every keyframe in the strip was rendering as a 34px letterbox.
    It was invisible in the suite and in a screenshot because the fixture's
    JPEGs do not decode, so the box was empty either way.
    """
    css = (STATIC / "dashboard.css").read_text()
    rule = _rule(css, ".framebtn")
    assert "height: auto" in rule and "aspect-ratio: 16 / 9" in rule


def test_a_state_marker_keeps_the_spaces_around_its_own_clock() -> None:
    """The other one from the same review.

    `held <span>1m 28s</span> more` inside an `inline-flex` box loses both
    spaces — a flex container makes an item of every text run and drops the
    whitespace between them — and the jobs table printed `held1m 28smore`. The
    markers take their height from the box and their centring from the leading,
    which is what `inline-block` plus a `line-height` is for.
    """
    css = (STATIC / "dashboard.css").read_text()
    for selector in (".pill", ".countdown", ".statepair-key"):
        match = re.search("^" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.M)
        assert match, selector
        block = match.group(1)
        assert "display: inline-block" in block, selector
        assert "line-height: calc(var(--chip-h)" in block, selector


def test_the_overview_retires_the_gaps_panel_when_there_are_none(
    tmp_path: Path,
) -> None:
    """Item 7. Three zeros is not a panel.

    Nothing is hidden by it: every figure in that panel is a link into a filter
    of the videos table, and an empty filter is one click from wherever the
    reader already is. The queue panel deliberately does *not* do this — an
    empty queue is a fact about this second, and a panel that vanished when the
    batch finished would take the operator's place-marker with it.
    """
    data = _corpus(tmp_path)
    with make_client(tmp_path) as client:
        assert "What is missing" in page(client, ROOT)  # the fixture has gaps

    conn = open_write_connection(data / "vidtheque.db")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE videos SET index_state = 'ready'")
        conn.execute(
            "INSERT OR REPLACE INTO video_stages (video_id, stage, state, model_key) "
            "SELECT id, 'ocr', 'done', 'seed' FROM videos"
        )
        conn.execute("COMMIT")
    finally:
        conn.close()

    with make_client(tmp_path) as client:
        body = page(client, ROOT)
        assert "What is missing" not in body
        assert "The queue" in body  # the panel about *now* stays whatever it says


def test_an_untagged_corpus_says_no_tags_and_stops(tmp_path: Path) -> None:
    """Item 8. The format lesson that followed it belongs beside the field that
    enforces it, not on the surface whose brief is that it does not narrate."""
    data = _corpus(tmp_path)
    conn = open_write_connection(data / "vidtheque.db")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM video_tags")
        conn.execute("COMMIT")
    finally:
        conn.close()
    with make_client(tmp_path) as client:
        body = page(client, ROOT)
    assert "no tags" in body
    assert "namespace" not in body


# ------------------------------------ 8. the fourth review pass (2026-08-10)


def test_the_overlay_line_list_scrolls_down_and_never_sideways() -> None:
    """Round 4, item 3. The bar that jumped between adjacent columns.

    `columns: 2` on a box with a bounded height does not draw two columns and
    scroll down: it paginates the overflow *sideways*. Measured on a real slide
    the list was 15,501px wide inside a 1,126px scrollport, so the reader was
    looking at one screenful of a horizontal filmstrip — and every
    `scrollIntoView` in it resolved to a horizontal jump, including for a line
    that was already fully on screen.
    """
    css = (STATIC / "dashboard.css").read_text()
    rule = _rule(css, ".shot-lines")
    assert not re.search(r"(?<!-)columns:", rule), "multicol paginated sideways"
    assert "display: grid" in rule
    assert "overflow: hidden auto" in rule, "down, and never sideways again"

    script = (STATIC / "dashboard.js").read_text()
    assert "scrollIntoView" not in script.split("function revealLine")[1].split(
        "\n}"
    )[0], "the reveal measures, it does not delegate"
    # Nothing scrolls a line that is already inside its box…
    assert "if (item.top >= box.top && item.bottom <= box.bottom) return;" in script
    # …and what does scroll only ever moves the one axis this box has.
    assert "scroller.scrollTop +=" in script
    assert "scrollLeft" not in script


def test_the_gold_evidence_mark_is_not_clipped_by_the_frame_it_marks() -> None:
    """The other half of round 4's item 1, and the reason it read as "nothing".

    Every gold mark in the frames panels was an `outline` with a positive
    `outline-offset` on the `<img>`. Both boxes that hold one — `.framebtn` and
    `.ocrstage` — are `overflow: hidden` and the image fills them exactly, so
    the outline was drawn *outside the image and inside the clip*: painted, and
    invisible on every screen. An element's own outline is not clipped by its
    own overflow, so the mark belongs on the box.

    Three marks were affected: the timeline↔strip hover link, `:target`, and
    the evidence selection a shot bar makes.
    """
    css = (STATIC / "dashboard.css").read_text()
    for selector in (
        ".framecard.is-linked .framebtn",
        ".framecard:target .framebtn",
        ".framecard.is-selected .framebtn",
    ):
        rule = _rule(css, selector)
        assert "outline: 2px solid var(--accent)" in rule, selector
    # …and nothing puts one back on an image inside a clipping box.
    assert not re.search(r"^\.framecard[^{}]*img[^{}]*\{[^}]*outline:", css, re.M)
    assert "overflow: hidden" in _rule(css, ".framebtn"), ".framebtn still clips"


def test_the_overview_masthead_carries_the_state_and_the_clock_only(
    client: TestClient,
) -> None:
    """Round 4, item 5. Two facts re-homed, for two different reasons.

    The state word wore a bare pill on the title's baseline, at a different
    weight from the machine strings beside it, reading as a caption that had
    drifted. §12.5 item 1 had already settled what a state looks like on this
    surface — a key joined to its own pill — and this page has one other fact
    wearing that primitive, so now it has one idiom rather than two.

    The published span was never a masthead fact at all: it says what is *in*
    the corpus, not what state the corpus is in or when this box last worked.
    It sits under the `videos` figure in the ledger, beside the count it is
    about.
    """
    body = page(client, ROOT)
    head = body[body.index('<div class="pagehead">') : body.index("</div>\n</div>")]
    assert '<span class="statepair-key">data_status</span>' in head
    assert "published" not in head, "the span left the masthead"
    assert "indexed" in head, "the freshness clock stays"

    ledger = body[body.index('<dl class="ledger">') : body.index("</dl>")]
    videos_figure = ledger[: ledger.index("<dt>runtime</dt>")]
    assert re.search(
        r'published <span class="fact"><span class="mono">[\d-]+</span>', videos_figure
    ), "…and landed on the count it describes"


def test_a_job_row_says_what_it_contains(client: TestClient) -> None:
    """Round 4, item 6b. A row printing only `job_uid` is an opaque handle.

    What a job contains is its items, and an item that has been fetched has
    resolved to a video with a title and a channel — both corpus, both already
    published on two other pages. The submitted URL is the redacted field
    (§2.4) and stays off this row in both modes.
    """
    body = page(client, f"{ROOT}/jobs")
    rows = dict(re.findall(r'<tr data-job="([^"]+)">(.*?)</tr>', body, re.S))

    running = rows["job_running001"]
    assert "Let&#39;s build GPT: from scratch" in running, "the first item's video"
    assert '<span class="row-more">+1 more</span>' in running, "and the rest, counted"
    assert "Andrej Karpathy" in running, "one channel across the items, so it is named"
    # The id is still on the row — it is the identifier — in the meta line with
    # the other machine strings rather than as the row's headline.
    assert "<code>job_running001</code>" in running

    # A job whose items have not been fetched has no title to borrow and says
    # what it does know instead of wearing its own id as a name.
    assert "1 item(s), none fetched yet" in rows["job_deferred01"]

    # And nothing on the page prints what was submitted, in either mode.
    for url in ("https://youtu.be/queuedvideo", "https://youtu.be/deferredvid"):
        assert url not in body


def test_a_job_row_costs_the_page_one_more_read_and_not_one_per_row(
    client: TestClient,
) -> None:
    """§6.3 again: what a job contains is one grouped query for the page.

    And it is deliberately not on the poll target — what a job holds does not
    change between two ticks, so the JSON the 2 s tick reads does not carry it.
    """
    one = _count_reads(client, f"{ROOT}/jobs?limit=1")
    many = _count_reads(client, f"{ROOT}/jobs?limit=100")
    assert one == many <= 4, f"{one} reads for 1 job, {many} for 100"

    payload = client.get(f"{ROOT}/api/jobs").json()
    assert payload["jobs"] and "contents" not in payload["jobs"][0]


def test_the_progress_figure_carries_its_own_breakdown(client: TestClient) -> None:
    """Round 4, item 6c. A percentage nobody can decompose is a number a reader
    has to take on trust.

    Five buckets, always, including the zeroes — the point of the tally is that
    it adds up to `n_items` — plus the rule that turns them into one number,
    which is two sentences and does not belong repeated on sixty rows.
    """
    body = page(client, f"{ROOT}/jobs")
    rows = dict(re.findall(r'<tr data-job="([^"]+)">(.*?)</tr>', body, re.S))
    running = rows["job_running001"]

    assert 'aria-describedby="pct-job_running001"' in running
    assert '<span class="hint" role="tooltip" id="pct-job_running001">' in running
    tally = re.search(r'data-field="job-tally">([^<]+)<', running).group(1)
    assert tally == "0 done · 0 failed · 0 skipped · 0 cancelled · 2 still to run"
    basis = re.search(r'data-field="job-basis">([^<]+)<', running).group(1)
    assert "of 2 item(s)" in basis and "out of 7" in basis  # len(jobs.store.STAGES)

    # Reachable by keyboard, and patched by the tick like every other value on
    # the row, so a hint held open while a job advances stays true.
    assert 'data-field="job-progress" tabindex="0"' in running
    script = (STATIC / "jobs.js").read_text()
    assert 'setText(scope, "job-tally", job.text.tally)' in script
    assert 'setText(scope, "job-basis", job.text.basis)' in script


def test_every_picker_is_this_systems_own_control_and_a_fixed_width(
    client: TestClient,
) -> None:
    """Round 4, items 6a and 7a — and it overturns §12.2's third call.

    That pass kept the platform's disclosure arrow, reasoning that a drawn
    caret would be the first glyph of an icon language this system does not
    have. On the operator's own machine what it bought was a rounded, shaded,
    OS-accented macOS control in a band of square 34px hairline boxes. The mark
    is a 6px square with two of its hairlines drawn, turned 45°, which is the
    same 1px rule everything else in the band is edged with.

    The fixed width is item 7b's half of the same wrapper: a select sized by
    its own content re-flows every control beside it when the reader changes
    its value.
    """
    css = (STATIC / "dashboard.css").read_text()
    assert "appearance: none" in _rule(css, ".pick > select")
    assert "flex: 0 0 var(--pick-w)" in _rule(css, ".field-pick")
    # …and never on a checkbox, which `appearance: none` erases, nor on a date
    # field, whose calendar button is the platform's answer to another question.
    assert "appearance: none" not in _rule(css, ".field input, .field select")

    for path in (f"{ROOT}/jobs", f"{ROOT}/videos"):
        body = page(client, path)
        selects = re.findall(r"<select[^>]*>", body)
        assert selects, path
        # Every picker sits in the wrapper that draws the mark, and every
        # picker's field carries the width, so the band's geometry is the
        # viewport's and never the selected value's.
        assert body.count('<span class="pick">') == len(selects), path
        assert body.count('class="field field-pick"') == len(selects), path


def test_the_videos_band_searches_on_change_and_keeps_its_no_script_path(
    client: TestClient,
) -> None:
    """Round 4, item 7c. Applying a filter *is* changing it.

    The Apply button is still real markup — it is what a browser with the
    script blocked submits — and comes off the page only once the script has
    taken the job over, which is the same contract the transcript's pager
    keeps. Nothing about the URL, the clamps or the handler changes: the script
    submits the same form the button submitted.
    """
    body = page(client, f"{ROOT}/videos")
    assert '<form class="filters" method="get"' in body
    assert "data-autosubmit" in body
    assert re.search(r'<button type="submit" data-apply>Apply</button>', body)

    script = (STATIC / "dashboard.js").read_text()
    assert 'document.querySelectorAll("form.filters[data-autosubmit]")' in script
    assert "requestSubmit" in script
    # A picker submits on the spot; a text field waits for the typing to stop.
    assert 'form.addEventListener("change"' in script
    assert "setTimeout(() => submit(event.target), 450)" in script
    assert "if (apply) apply.hidden = true;" in script


def test_the_videos_masthead_drops_the_order_and_prints_only_narrowing(
    client: TestClient,
) -> None:
    """Round 4, item 7d.

    Every other entry in that strip is something that took rows *out* of the
    table. An order takes nothing out, it never disappeared, and what the rows
    are sorted by is already said by the picker in the band and by the sorted
    column's own gold underline. With it gone the strip is empty when nothing
    is narrowing, so the paragraph goes with it rather than sitting under the
    title as a blank line.
    """
    plain = page(client, f"{ROOT}/videos")
    head = plain[plain.index('<div class="pagehead">') : plain.index("</form>")]
    assert "ordered by" not in head
    assert '<p class="pagehead-meta">' not in head, "no strip when nothing narrows"
    # The sort is still stated, twice, where it belongs.
    assert 'aria-sort="descending"' in plain

    narrowed = page(client, f"{ROOT}/videos?index_state=ready&has=transcript")
    strip = re.search(r'<p class="pagehead-meta">(.*?)</p>', narrowed, re.S).group(1)
    assert "state" in strip and "ready" in strip
    assert "has" in strip and "transcript" in strip
    assert "ordered by" not in strip
    # The last fact carries no separator, whichever one it turns out to be.
    assert strip.rstrip().endswith("</span>")
    assert not re.search(r'<span class="sep">·</span>\s*$', strip.rstrip())


def test_the_favicon_is_the_v_and_carries_no_ground() -> None:
    """Tom picked the mark on 2026-08-10: the wordmark's `v`, and the dot.

    The film frame is gone — it drew the medium rather than the product's
    argument. Two properties of the replacement are load-bearing rather than
    stylistic, so both are pinned: there is no background rectangle, which is
    what lets one drawing sit on a light *and* a dark tab strip without a
    `prefers-color-scheme` variant this single-scheme surface has no business
    carrying; and the gold is cored inside a keyline in `--gold-ink`, which is
    what keeps the shape readable when the strip under it is light.

    It stays an inline `data:` URI: a tab on a tunnelled port gets its icon
    with no second request.
    """
    head = (TEMPLATES / "base.html").read_text()
    icon = re.search(r'<link rel="icon" href="(data:image/svg\+xml,[^"]+)"', head)
    assert icon, "an inline data: icon"
    svg = icon.group(1)
    assert "%23e7b455" in svg, "the gold core"
    assert "%23120c02" in svg and "stroke-width='2'" in svg, "the gold-ink keyline"
    assert "%23040405" not in svg, "no pitch ground — the glyph floats"
    assert "<rect width='32' height='32'" not in svg, "…and no ground rect at all"
    path = re.search(r"d='([^']+)'", svg).group(1)
    assert " " not in path, "comma-separated path data, as the old icon's was"
