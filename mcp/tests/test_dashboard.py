"""The management dashboard — `docs/design/dashboard.md`, phases 1 and 2.

Five read-only pages, `/dashboard/api/*` under owner clamps, and the auth gate
in front of both. Nothing here reaches the network or loads a model: the pages
are rendered against the seeded fixture corpus through the same ASGI app the
server runs.

Phase 2's half is the jobs view — the `not_before` countdown, the retry
counter, the degraded list and the event tail — plus its demo projection, which
keeps every clock and drops exactly two fields.

The two things this file is most interested in are the ones a screenshot cannot
check: that a corpus string never becomes markup, and that every list is
bounded server-side however the URL asks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vidtheque_mcp.app import build_app
from vidtheque_mcp.config import Settings
from vidtheque_mcp.dashboard import ROOT, WRITE_ROUTES
from vidtheque_mcp.dashboard.settings import DashboardSettings
from vidtheque_mcp.db.connection import open_write_connection
from vidtheque_mcp.public.api import OWNER_CLAMPS, PUBLIC_CLAMPS
from vidtheque_mcp.public.settings import PublicSettings

from .conftest import FakeEmbeddings, seed

STATIC = Path(__file__).resolve().parents[1] / "src/vidtheque_mcp/dashboard/static"
DEMO_STATIC = Path(__file__).resolve().parents[1] / "src/vidtheque_mcp/public/static"

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
    return Settings(
        data_dir=_corpus(tmp_path),
        public_url="http://localhost:8080",
        worker_url="http://worker:8081",
        secret="test-secret",
        # See mcp/tests/conftest.py: the shipped relevance floors are
        # deliberately open pending recalibration, and the fixture's
        # stand-in vectors have no geometry to calibrate against.
        vec_max_distance=0.72,
        frame_max_distance=0.96,
        **kwargs,
    )


def make_client(
    tmp_path: Path,
    *,
    auth_mode: str = "none",
    token: str | None = None,
    public: PublicSettings | None = None,
    dashboard: DashboardSettings | None = None,
) -> TestClient:
    settings = _settings(tmp_path, auth_mode=auth_mode, static_token=token)  # type: ignore[arg-type]
    app = build_app(
        settings,
        embeddings=FakeEmbeddings(),
        run_pipeline=False,
        public=public or PublicSettings(enabled=False),
        dashboard=dashboard or DashboardSettings(),
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
    """The dashboard's JSON is `/api`'s handlers at another path, not a copy."""
    with make_client(tmp_path, public=PublicSettings(enabled=True)) as client:
        public = client.get("/api/videos").json()
        owner = client.get("/dashboard/api/videos").json()
    assert set(public) == set(owner) == {"videos", "pagination"}
    assert public["pagination"]["limit"] == 24
    assert owner["pagination"]["limit"] == 50


def test_the_dashboard_json_is_clamped_server_side(client: TestClient) -> None:
    payload = client.get("/dashboard/api/videos?limit=100000").json()
    assert payload["pagination"]["limit"] == 100  # not 100000, and not 50
    search = client.get("/dashboard/api/search?q=cache&limit=999").json()
    assert search["pagination"]["limit"] == 50
    assert client.get("/dashboard/api/meta").json()["clamps"]["policy"] == "owner"


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
    assert "vector legs on" in body and "indexing allowed" in body
    # `data_status` verbatim from corpus-summary, not re-derived.
    assert re.search(r'class="pill tone-\w+">(ok|partial|degraded|indexing|empty)<', body)
    # Storage from the column, and no filesystem path anywhere.
    assert "keyframe JPEGs" in body
    assert "/keyframes/" not in body and "jpeg_path" not in body


def test_the_overview_shows_the_drift_banner_when_vectors_are_off(
    client: TestClient,
) -> None:
    assembled = client.app.state.assembled
    assembled.db.vectors.disable("the worker is serving 'other' but the corpus used 'qwen'.")
    try:
        body = page(client, ROOT)
        assert "The corpus and the worker disagree" in body
        assert "the worker is serving" in body
        assert "vector legs off" in body
    finally:
        assembled.db.vectors.enabled = True
        assembled.db.vectors.reason = None


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
    assert "Provenance records what" in body


def test_the_detail_page_is_honest_about_a_failed_stage(client: TestClient) -> None:
    body = page(client, f"{ROOT}/videos/aaaaaaaaaaa")
    # video-summary refuses a mid-pipeline video; the refusal is rendered
    # verbatim and the panels below still say what exists.
    assert "E_INDEXING" in body
    assert ">failed<" in body
    assert "yt-dlp-2026.07.04" in body  # the stage that did succeed
    # …and the one that did not records no model at all.
    assert "Sign in to confirm you are not a bot" in body
    assert "not recorded" in body


def test_the_detail_page_counts_are_bounded_and_per_video(client: TestClient) -> None:
    body = page(client, f"{ROOT}/videos/kCc8FmEb1nY")
    assert "What was stored" in body
    assert "the embedding unit" in body
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
    assert "There is no scenes table" in body


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
    boxes = re.findall(r'class="ocrline"\s+data-box="([^"]+)"', body)
    assert len(boxes) == 3
    for box in boxes:
        values = [float(v) for v in box.split(",")]
        assert len(values) == 4 and all(0.0 <= v <= 1.0 for v in values)
    assert "paged kv cache" in body
    assert "normalised 0–1 at write time" in body


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


def test_the_deferred_job_explains_itself(client: TestClient) -> None:
    body = page(client, f"{ROOT}/jobs/job_deferred01")
    assert "Waiting, not stuck" in body
    assert "E_RATE_LIMIT" in body
    # The event tail is the only place a non-rate-limit deferral exists at all.
    assert "retrying in 300s after E_RATE_LIMIT" in body
    assert "here and nowhere else" in body


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
    assert one == many <= 3, f"{one} reads for 1 job, {many} for 100"
    detail = _count_reads(client, f"{ROOT}/jobs/job_finished01")
    assert detail <= 8, f"{detail} reads for one job page"


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
    its own copy of the six custom properties. This is the thing that stops the
    copy drifting into a second visual world."""

    def palette(css: str) -> list[tuple[str, str]]:
        blocks = re.findall(r":root \{(.*?)\}", css, re.S)
        out = []
        for block in blocks:
            for name, value in re.findall(r"--([\w-]+):\s*([^;]+);", block):
                if name in ("bg", "fg", "muted", "line", "accent", "raised"):
                    out.append((name, value.strip()))
        return out

    demo = palette((DEMO_STATIC / "style.css").read_text())
    dash = palette((STATIC / "dashboard.css").read_text())
    assert demo and len(demo) == 12  # six properties, two schemes
    assert dash[: len(demo)] == demo


def test_both_schemes_and_a_mobile_viewport_are_declared(client: TestClient) -> None:
    body = page(client, ROOT)
    assert body.count('name="theme-color"') == 2
    assert 'content="light dark"' in body
    assert "width=device-width" in body
    css = (STATIC / "dashboard.css").read_text()
    assert "prefers-color-scheme: dark" in css
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
    tmp_path: Path, *, readonly: bool = False, password: str | None = PASSWORD, **kwargs
) -> TestClient:
    """`token` mode with a password — the deployment phase 3 is written for."""
    settings = _settings(
        tmp_path, auth_mode="token", static_token=TOKEN, password=password
    )
    app = build_app(
        settings,
        embeddings=FakeEmbeddings(),
        run_pipeline=False,
        public=PublicSettings(enabled=readonly),
        dashboard=kwargs.pop("dashboard", None) or DashboardSettings(),
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
        assert "Add to the index" not in table


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
            f"{ROOT}/videos/kCc8FmEb1nY/reindex",
            f"{ROOT}/videos/kCc8FmEb1nY/tags",
        ):
            # 404 rather than 405: `Mount("/")` is a full match for the path,
            # so the router never falls back to the method-mismatch answer the
            # POST-only route would have given. Either way nothing fires.
            assert client.get(path).status_code in (404, 405), path
        # `/index` has a GET and it is the form, which reads and never writes.
        assert client.get(f"{ROOT}/index").status_code == 200


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
        sign_in(client)
        assert "Add to the index" in page(client, ROOT)
        assert "Re-index" in page(client, f"{ROOT}/videos")
        assert 'id="manage"' in page(client, f"{ROOT}/videos/kCc8FmEb1nY")
        assert "Sign out" in page(client, ROOT)

    with make_client(tmp_path) as none_mode:  # auth=none
        overview = page(none_mode, ROOT)
        assert "Add to the index" not in overview
        assert "Sign out" not in overview
        # …and it says why, with the one-line fix (§3.2 rule 3).
        assert "VIDTHEQUE_AUTH=token" in overview
        assert "Re-index" not in page(none_mode, f"{ROOT}/videos")
        assert 'id="manage"' not in page(none_mode, f"{ROOT}/videos/kCc8FmEb1nY")
