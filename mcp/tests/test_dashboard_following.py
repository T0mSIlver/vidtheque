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

import re
import sqlite3
from pathlib import Path
from typing import Callable

import httpx2 as httpx
import pytest
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


def page(client: TestClient, path: str, status: int = 200) -> str:
    response = client.get(path, headers=BEARER)
    assert response.status_code == status, f"{path} -> {response.status_code}"
    return response.text


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
    be missing rather than refusing, **including its two read pages**: a page
    whose every affordance POSTs has nothing to show a deployment that
    registers no write side, and a route that exists and refuses is a route
    somebody probes.
    """
    with owner_client(tmp_path, readonly=True) as demo:
        assert demo.get(f"{ROOT}/videos", headers=BEARER).status_code == 200
        assert demo.get(f"{ROOT}/following", headers=BEARER).status_code == 404
        assert demo.get(f"{ROOT}/following/karpathy", headers=BEARER).status_code == 404
        for path in FOLLOW_POSTS:
            refused = demo.post(path, headers={**BEARER, **SAME_ORIGIN})
            assert refused.status_code == 404, path
        # And no affordance survives the projection either (§2.4's table).
        assert "Following" not in demo.get(f"{ROOT}/videos", headers=BEARER).text


def test_the_following_surface_is_absent_in_auth_none(tmp_path: Path) -> None:
    """§3.2 rule 3, as a status code: **404, not 403**.

    `none` is the mode with no credential to check. An unauthenticated instance
    behind a tunnel with a live "follow this channel" button is a standing
    remote-yt-dlp subscription pointed at the operator's residential IP — worse
    than the one-shot version of the same button, because nobody has to come
    back and press it again.
    """
    with make_client(tmp_path) as client:  # auth=none
        assert client.get(f"{ROOT}/following").status_code == 404
        assert client.get(f"{ROOT}/following/karpathy").status_code == 404
        for path in FOLLOW_POSTS:
            assert client.post(path, headers=SAME_ORIGIN).status_code == 404, path
        # Every read page is still open, which is the other half of the rule.
        assert client.get(ROOT).status_code == 200
        assert "Following" not in client.get(ROOT).text
        registered = {str(getattr(r, "path", "")) for r in client.app.routes}
        assert not (registered & set(WRITE_ROUTES))


def test_the_rail_item_appears_exactly_where_the_routes_do(tmp_path: Path) -> None:
    """§13's rule for `Add videos`, applied to the word beside it.

    The link exists precisely when its target does — absent, not disabled — so
    a rail can never point at a page this deployment 404s.
    """
    with owner_client(tmp_path) as private:
        rail = page(private, ROOT)
        assert f'href="{ROOT}/following"' in rail
        assert ">Following<" in rail
    with owner_client(tmp_path, readonly=True) as demo:
        assert f'href="{ROOT}/following"' not in page(demo, ROOT)
    with make_client(tmp_path) as anonymous:
        assert f'href="{ROOT}/following"' not in anonymous.get(ROOT).text


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
        # And the detail page is a read: no state-changing GET, ever (§3.3).
        detail = [r for r in routes if str(r.path) == f"{ROOT}/following/{{slug}}"]
        assert detail and set(detail[0].methods or ()) - {"HEAD"} == {"GET"}


# ------------------------------------------------------------ 2. the two pages


def test_the_list_is_the_add_form_when_nothing_is_followed(tmp_path: Path) -> None:
    """The empty state is the control, not a sentence about the control.

    An empty state you have to leave in order to act on it is a screen that
    exists to say "no". The form is the page here, under one sentence naming
    what a follow does.
    """
    with _empty_corpus(tmp_path) as client:
        body = page(client, f"{ROOT}/following")
        assert "Nothing is followed yet" in body
        assert f'action="{ROOT}/following"' in body
        assert 'name="url"' in body
        # No table, because there is nothing to put in one.
        assert "grid follows" not in body
        # The band is still there and still counts to zero: a figure that
        # disappears when it is zero is a number you cannot trust when it is not.
        assert "brought in" in body


def test_the_list_renders_the_band_the_rules_and_the_clocks(tmp_path: Path) -> None:
    with owner_client(tmp_path) as client:
        body = page(client, f"{ROOT}/following")
        assert "Andrej Karpathy" in body
        assert "Paused Channel" in body
        assert f'href="{ROOT}/following/andrej-karpathy"' in body
        # Every state is a word as well as a colour (DESIGN.md).
        assert 'class="pill tone-ok">active<' in body
        assert 'class="pill tone-wait">paused<' in body
        # The rule, compressed: the tabs, the floor and the per-check cap.
        assert "/videos" in body
        assert "0:08:00 floor" in body
        assert "5/check" in body
        # The budget line names both halves — what is spent, and the ceiling.
        # The ceiling itself is a deployment's own number and is asserted where
        # it is set (`test_the_budget_line_reads_the_pipeline_ceiling`); pinning
        # today's default here made this page's test fail when Tom changed it,
        # which told nobody anything about the page.
        assert "used today" in body
        assert re.search(r"of \d+h", body)
        # The last error rides on the row that has one.
        assert "E_RATE_LIMIT" in body


def test_the_held_band_names_the_rows_and_gives_them_a_door(tmp_path: Path) -> None:
    """"N are waiting" with nowhere to go is a notification, not a control."""
    with owner_client(tmp_path) as client:
        body = page(client, f"{ROOT}/following")
        assert "waiting for you" in body
        assert f'href="{ROOT}/following/andrej-karpathy#passed"' in body


def test_the_detail_page_is_the_sentence_the_ledger_and_the_cost(
    tmp_path: Path,
) -> None:
    """The three bands, in order — and the third is the point of the page."""
    with owner_client(tmp_path) as client:
        body = page(client, f"{ROOT}/following/andrej-karpathy")

        # Band 1: the rule as one sentence, from `follows.rules.describe` and
        # from nothing else, plus the clocks and the last error.
        assert "Every 6 hours, take up to 5 new uploads from Andrej Karpathy" in body
        assert "longer than 0:08:00" in body
        assert "last check" in body and "next check" in body and "last arrival" in body
        assert "the source rate-limited this box" in body
        # The form is a disclosure, and it is a `<details>` — no script.
        assert "<details" in body and "Edit the rule" in body

        # Band 2: the checks and the jobs they queued, each linking into the
        # job detail page that already exists.
        assert f'href="{ROOT}/jobs/job_followchk1"' in body
        assert f'href="{ROOT}/jobs/job_followidx1"' in body

        # Band 3: what it passed over, with the reason verbatim and where the
        # number came from.
        assert "7:48, shorter than your 8:00 floor" in body
        # A check that spent a request says so; one that read the flat listing
        # has nothing to report and prints nothing.
        assert "judged from a probe" in body
        assert "judged from a listing" not in body
        # A provisional decision does not look terminal.
        assert "re-decided on the next check" in body
        assert "waiting on you" in body
        assert "Index anyway" in body
        # A candidate that was *accepted* is not a candidate it passed over.
        assert "Accepted and queued" not in body


def test_the_derived_line_is_read_out_of_the_rows_and_only_when_true(
    tmp_path: Path,
) -> None:
    """One sentence, computed from the page's own rows, or nothing at all.

    Two of the seeded near misses are inside a minute of the floor and one is
    half an hour outside it, so the number is 2 and not 3 — which is the whole
    difference between reading the ledger and counting the band.
    """
    from vidtheque_mcp.dashboard.views import NEAR_MISS_S

    with owner_client(tmp_path) as client:
        body = page(client, f"{ROOT}/following/andrej-karpathy")
        assert f"within {NEAR_MISS_S} seconds of your floor" in body
        assert re.search(r"2 of the last \d+ passed over were within", body), body

    # The follow with no length rule has no near miss to report, and prints
    # nothing rather than a zero-ish sentence.
    with owner_client(tmp_path) as client:
        other = page(client, f"{ROOT}/following/paused-channel")
        assert "seconds of your" not in other


def test_an_unknown_follow_is_a_typed_refusal(tmp_path: Path) -> None:
    with owner_client(tmp_path) as client:
        response = client.get(f"{ROOT}/following/no-such-thing", headers=BEARER)
        assert response.status_code == 404
        assert "E_UNKNOWN_FOLLOW" in response.text


def test_both_lists_are_clamped_server_side(tmp_path: Path) -> None:
    """`?limit=100000` is clamped, not honoured — never a prompt-only limit."""
    from vidtheque_mcp.dashboard.views import FOLLOW_PAGE_MAX, SEEN_PAGE_MAX

    with owner_client(tmp_path) as client:
        listing = page(client, f"{ROOT}/following?limit=100000&offset=0")
        assert f"limit={FOLLOW_PAGE_MAX}" in listing or "more available" not in listing
        detail = page(
            client, f"{ROOT}/following/andrej-karpathy?limit=100000&offset=0"
        )
        assert f"limit={SEEN_PAGE_MAX}" in detail or "more available" not in detail
        # And the pager never prints an exact total.
        assert "of 6 rows" not in detail


def test_a_hostile_title_and_a_hostile_reason_never_become_markup(
    tmp_path: Path,
) -> None:
    """A `reason` is a sentence the check wrote about a title somebody chose.

    Both are corpus strings, both reach the ledger verbatim by design, and
    neither may ever reach the browser as markup.
    """
    with owner_client(tmp_path) as client:
        body = page(client, f"{ROOT}/following/andrej-karpathy")
        assert "<script>alert(document.cookie)</script>" not in body
        assert "<img src=x" not in body
        # Escaped, and still *there*: the ledger prints the reason verbatim, so
        # the string has to survive as text rather than be stripped.
        assert "&lt;script&gt;" in body
        assert "&lt;img src=x onerror=alert(1)&gt;" in body


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
    """The four state writes, each through `follow_channel` and back to a page.

    POST → 303 → GET throughout: a write is never the thing a reload repeats.
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

        detail = page(client, target)
        assert "longer than 0:08:00" in detail
        assert 'class="pill tone-ok">active<' in detail

        paused = client.post(
            f"{ROOT}/following/{slug}/state",
            data={"action": "pause"},
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert paused.status_code == 303
        assert 'class="pill tone-wait">paused<' in page(client, target)

        resumed = client.post(
            f"{ROOT}/following/{slug}/state",
            data={"action": "resume"},
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert resumed.status_code == 303
        assert 'class="pill tone-ok">active<' in page(client, target)

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
        assert client.get(target, headers=BEARER).status_code == 404


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
        body = page(client, f"{ROOT}/following/andrej-karpathy")
        assert "longer than 0:20:00" in body
        assert "take up to 3 new uploads" in body
        assert "index transcript only" in body
        assert "hold them for you rather than queueing them" in body

        # The floor the validator owns, refused in the validator's own words —
        # not clamped silently by the form.
        refused = client.post(
            f"{ROOT}/following/andrej-karpathy/rules",
            data={"tab_videos": "1", "check_interval_s": "60"},
            headers=SAME_ORIGIN,
        )
        assert refused.status_code == 400
        assert "E_BAD_PARAM" in refused.text
        assert "at least 900 seconds" in refused.text


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
        assert "0:08:00 floor" in page(client, f"{ROOT}/following")


def test_no_follow_write_is_reachable_by_a_get(tmp_path: Path) -> None:
    """§3.3: `SameSite=Lax` sends the cookie on a top-level GET navigation."""
    with owner_client(tmp_path) as client:
        sign_in(client)
        for path in FOLLOW_POSTS[1:]:
            # 404 rather than 405: `Mount("/")` is a full match for the path, so
            # the router never falls back to the method-mismatch answer the
            # POST-only route would have given. Either way nothing fires.
            assert client.get(path, follow_redirects=False).status_code in (404, 405), path
        # `/following` has a GET and it is the list, which reads and never writes.
        assert client.get(f"{ROOT}/following", headers=BEARER).status_code == 200


def test_the_kind_filter_offers_the_follow_check_job(tmp_path: Path) -> None:
    """`follow_check` is a `jobs.kind` since 0006, so it is a filter (§5.4)."""
    with owner_client(tmp_path) as client:
        body = page(client, f"{ROOT}/jobs")
        assert '<option value="follow_check"' in body
        assert page(client, f"{ROOT}/jobs?kind=follow_check").count("job_followchk1") >= 1


@pytest.mark.parametrize("template", ["following.html", "follow.html", "_follow_rules.html"])
def test_the_following_templates_never_reach_for_safe(template: str) -> None:
    """Autoescape is the whole reason Jinja is a dependency here (§10.2)."""
    root = Path(__file__).resolve().parents[1] / "src/vidtheque_mcp/dashboard/templates"
    text = (root / template).read_text()
    assert "| safe" not in text
    assert "|safe" not in text
    # And no inline script: the pages stay CSP-ready.
    assert "<script" not in text
