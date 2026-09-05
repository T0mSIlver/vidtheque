"""`/dashboard/api/{overview,ledger,session}` — the first JSON slice for the
React dashboard (`docs/design/frontend-migration.md`).

Three additive reads over the assemblers the Jinja pages already use, so what
this file is actually interested in is the three ways that arrangement could go
wrong: the JSON skipping the gate the pages sit behind, the JSON skipping the
projection the pages apply, and the session endpoint — the one read that is
*deliberately* open — answering with something a signed-out browser has no
business learning.

The fixture corpus, the seeded jobs and the client builders are
`test_dashboard.py`'s: this is the same deployment matrix (`none`, `token`,
`VIDTHEQUE_PUBLIC_READONLY=1`) asked a different question.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx2 as httpx
import pytest
from starlette.testclient import TestClient

from vidtheque_mcp.auth.login import SESSION_COOKIE
from vidtheque_mcp.dashboard import ROOT
from vidtheque_mcp.dashboard.read_models import (
    BUDGET_WINDOW_S,
    CHANNEL_CAP,
    CHECK_CAP,
    EVENT_CAP,
    FAILED_WINDOW_S,
    FOLLOW_PAGE,
    FOLLOW_PAGE_MAX,
    HELD_BAND_CAP,
    INDEX_JOB_CAP,
    NEAR_MISS_S,
    OCR_LINE_CAP,
    RECENT_CAP,
    SEEN_PAGE_MAX,
    TAG_CAP,
)
from vidtheque_mcp.dashboard.settings import DashboardSettings
from vidtheque_mcp.db.connection import open_write_connection
from vidtheque_mcp.text import clock, iso_day

from .test_dashboard import (
    BEARER,
    DEMO,
    PASSWORD,
    SAME_ORIGIN,
    TOKEN,
    make_client,
    owner_client,
)
from .test_dashboard import _corpus as corpus_dir

# The follow fixture and its two deployments (§22). Aliased rather than
# imported over the names above: this file asks `test_dashboard.py`'s corpus
# most of its questions and the following corpus only the following ones.
from .test_dashboard_following import make_client as follow_client
from .test_dashboard_following import owner_client as follow_owner
from .test_dashboard_following import sign_in

OVERVIEW = f"{ROOT}/api/overview"
LEDGER = f"{ROOT}/api/ledger"
SESSION = f"{ROOT}/api/session"
# The videos table and the video detail page (§20). `library`, not `videos`:
# `/dashboard/api/videos` is the facade's listing at this prefix and stays it.
LIBRARY = f"{ROOT}/api/library"
FACADE = f"{ROOT}/api/videos"
FACADE_SEARCH = f"{ROOT}/api/search"

# The hit fields `dashboard/templates/search.html` renders, listed here rather
# than scraped, because the point is to notice when one of them stops arriving.
# Six the template names directly — `hit.thumb`, `hit.thumb_large`,
# `hit.frame_id`, `hit.match_start`, `hit.title`, `hit.channel` — and four it
# reaches through `views.search`'s four derivations: `link` becomes `receipt`,
# `video_id` (with `frame_id`) becomes `inside`, `source` becomes `evidence`,
# `text` becomes `parts`. The React search page reads the same payload, so a
# trim of the facade that would empty that page fails here first.
SEARCH_HIT_KEYS = {
    "channel",
    "frame_id",
    "link",
    "match_start",
    "source",
    "text",
    "thumb",
    "thumb_large",
    "title",
    "video_id",
}
# The half-indexed video: two stage rows, one of them failed with yt-dlp's
# prose in it, which is what the detail projection has to lose.
HALF = "aaaaaaaaaaa"
FIRST = "kCc8FmEb1nY"

# A drift reason is a config/dimension mismatch written for the operator, and
# it names what the worker is serving. The *effect* it caused is the visitor's
# business; this sentence is not.
PRIVATE_REASON = "the worker is serving 'other' but the corpus used 'qwen'."

# What a formatted clock looks like on the way out: `iso_z`/`iso_minute`'s
# stamps, and `render.span`'s spoken durations and relative ages.
ISO_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
SPOKEN_DURATION = re.compile(r"\d+\s*(?:m \d+s|h \d+m|hours? ago|minutes? ago|days? ago)")


def read(client: TestClient, path: str, status: int = 200, **kwargs) -> dict:
    response = client.get(path, **kwargs)
    assert response.status_code == status, f"{path} -> {response.status_code}"
    assert response.headers["cache-control"] == "no-store", path
    return response.json()


# ------------------------------------------------------------------- the gate


def test_the_two_corpus_reads_sit_behind_the_pages_own_gate(tmp_path: Path) -> None:
    """A JSON route that skips the credential check is the hole the pages were
    guarded against — and the refusal is the typed envelope, not a page."""
    with owner_client(tmp_path) as client:
        for path in (OVERVIEW, LEDGER):
            refused = client.get(path)
            assert refused.status_code == 401, path
            body = refused.json()
            assert body["error"] == "E_AUTH_REQUIRED"
            assert "Bearer" in body["next"] or "Sign in" in body["next"]
            # Nothing about the corpus rides out on a refusal.
            assert "videos" not in body
            assert client.get(path, headers=BEARER).status_code == 200


def test_the_json_401_is_not_cacheable(tmp_path: Path) -> None:
    """A refused read of the owner surface is still a read of it — a shared
    cache holding the 401 would go on answering it after the caller signs in."""
    with owner_client(tmp_path) as client:
        refused = client.get(OVERVIEW)
        assert refused.status_code == 401
        assert refused.headers["cache-control"] == "no-store"


def test_a_session_cookie_reads_the_json_and_a_stale_one_does_not(
    tmp_path: Path,
) -> None:
    """The same cookie the pages take, checked in the same table."""
    with owner_client(tmp_path) as client:
        store = client.app.state.assembled.auth.store
        assert store is not None
        store.save_session("live", "owner", int(time.time()) + 600)
        store.save_session("dead", "owner", int(time.time()) - 1)

        client.cookies.set(SESSION_COOKIE, "live")
        assert client.get(OVERVIEW).status_code == 200
        client.cookies.set(SESSION_COOKIE, "dead")
        assert client.get(OVERVIEW).status_code == 401
        client.cookies.set(SESSION_COOKIE, "never-existed")
        assert client.get(LEDGER).status_code == 401


def test_the_three_routes_are_get_only_and_go_when_the_group_does(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        registered = {
            str(route.path): set(route.methods or ())
            for route in client.app.routes
            if str(getattr(route, "path", "")) in (OVERVIEW, LEDGER, SESSION)
        }
        assert set(registered) == {OVERVIEW, LEDGER, SESSION}
        for path, methods in registered.items():
            assert methods <= {"GET", "HEAD"}, path

    with make_client(tmp_path, dashboard=DashboardSettings(enabled=False)) as off:
        for path in (OVERVIEW, LEDGER, SESSION):
            assert off.get(path).status_code == 404, path


# ---------------------------------------------------------------- the session


def test_the_session_endpoint_answers_a_signed_out_browser(tmp_path: Path) -> None:
    """The one read that is open, because "am I signed in?" cannot need a
    signed-in caller — and because the 401 page has told an anonymous browser
    the auth mode and this exact hint since phase 1."""
    with owner_client(tmp_path) as client:
        response = client.get(SESSION)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["auth_mode"] == "token"
        assert body["authenticated"] is False
        assert body["is_owner"] is False
        assert body["signed_in"] is False
        assert body["has_session_cookie"] is False
        assert body["policy"] == "public"
        # There is a write side here, so there is somewhere to sign in and the
        # client can say which secret this instance takes.
        assert body["write_side"] is True
        assert body["login_url"] == f"{ROOT}/login"
        assert body["accepts_password"] is True
        assert body["accepts_token"] is True
        # …and neither secret's value, nor anything about the operator's box.
        raw = response.text
        for secret in (TOKEN, PASSWORD, str(tmp_path), "vidtheque.db", "worker:8081"):
            assert secret not in raw, f"{secret} leaked through the session endpoint"


def test_signed_in_is_the_validated_session_never_the_cookie(tmp_path: Path) -> None:
    """A cookie whose row has expired is exactly the case that must read false:
    it is the one that would otherwise render a dashboard shell for a caller
    every subsequent request refuses."""
    with owner_client(tmp_path) as client:
        store = client.app.state.assembled.auth.store
        assert store is not None
        store.save_session("live", "owner", int(time.time()) + 600)
        store.save_session("dead", "owner", int(time.time()) - 1)

        client.cookies.set(SESSION_COOKIE, "dead")
        stale = read(client, SESSION)
        assert stale["signed_in"] is False
        assert stale["authenticated"] is False
        assert stale["is_owner"] is False
        assert stale["policy"] == "public"

        client.cookies.set(SESSION_COOKIE, "live")
        live = read(client, SESSION)
        assert live["signed_in"] is True
        assert live["authenticated"] is True
        assert live["is_owner"] is True
        assert live["policy"] == "owner"

        # The bearer is a credential and an owner, and it is nothing to sign
        # out of: `signed_in` names the cookie, not access. It is checked first,
        # so a dead cookie riding along changes nothing.
        client.cookies.set(SESSION_COOKIE, "dead")
        bearer = read(client, SESSION, headers=BEARER)
        assert (bearer["is_owner"], bearer["signed_in"]) == (True, False)
        assert bearer["policy"] == "owner"


def test_has_session_cookie_is_the_cookie_and_signed_in_is_the_row(
    tmp_path: Path,
) -> None:
    """The two facts the payload carries side by side (Tom, 2026-09-05).
    `signed_in` is authorization; `has_session_cookie` is whether there is a
    cookie to clear — the question the HTML rail has always asked, so a stale
    cookie still gets a sign-out button rather than a dashboard shell."""
    with owner_client(tmp_path) as client:
        store = client.app.state.assembled.auth.store
        assert store is not None
        store.save_session("live", "owner", int(time.time()) + 600)
        store.save_session("dead", "owner", int(time.time()) - 1)

        # No cookie at all: nothing to clear, and nothing to serve.
        none = read(client, SESSION)
        assert (none["has_session_cookie"], none["signed_in"]) == (False, False)

        client.cookies.set(SESSION_COOKIE, "live")
        live = read(client, SESSION)
        assert (live["has_session_cookie"], live["signed_in"]) == (True, True)

        # A cookie whose row has expired, and one that never had a row: both
        # are a cookie the browser will keep sending until something clears it.
        for value in ("dead", "never-existed"):
            client.cookies.set(SESSION_COOKIE, value)
            stale = read(client, SESSION)
            assert stale["has_session_cookie"] is True, value
            assert stale["signed_in"] is False, value
            assert stale["authenticated"] is False, value


def test_the_session_endpoint_describes_the_deployment_it_is_in(
    tmp_path: Path,
) -> None:
    """`none` has no write side and nowhere to sign in; the demo projection has
    neither either, and says so rather than pointing at a page that 404s."""
    with make_client(tmp_path) as open_mode:  # AUTH=none
        body = read(open_mode, SESSION)
        assert body["auth_mode"] == "none"
        # Open for reading, and still not the owner: `"open"` is the absence of
        # a check, which is what the clamp policy turns on.
        assert body["authenticated"] is True
        assert body["is_owner"] is False
        assert body["policy"] == "public"
        assert body["write_side"] is False
        assert body["login_url"] is None
        assert body["accepts_password"] is False
        assert body["accepts_token"] is False
        assert body["readonly"] is False
        # Nobody is ever refused here, so there is no refusal to quote. The
        # hint `sign_in_hint` builds names a bearer unconditionally — right for
        # a 401 that only renders in a mode that takes one, and untrue as a
        # standing description of a deployment that accepts no credential.
        assert body["sign_in_hint"] is None

    with make_client(tmp_path, public=DEMO) as demo:
        body = read(demo, SESSION)
        assert body["readonly"] is True
        assert body["write_side"] is False
        assert body["login_url"] is None
        assert body["sign_in_hint"] is None


# --------------------------------------------------------------- typed values


def test_the_overview_json_is_typed_values_and_no_display_strings(
    tmp_path: Path,
) -> None:
    """Tom, 2026-09-05: typed on the wire, formatted in React. Counts are ints,
    clocks are epoch seconds, and nothing here says "4m 12s"."""
    with make_client(tmp_path) as client:
        body = read(client, OVERVIEW)

    corpus = body["corpus"]
    assert corpus["videos"] == 4  # three ready, one half-indexed
    for key in ("videos", "queryable_videos", "cues", "keyframes", "ocr_lines"):
        assert isinstance(corpus[key], int), key
    assert isinstance(corpus["duration_s"], float)
    # Seconds are the stored fact and the rollup's `hours` is its own 0.1
    # rounding of them (`queries._CORPUS_SQL`). A display rounding on the wire
    # is the split leaking back the other way, so it is not sent.
    assert "hours" not in corpus
    assert corpus["videos_by_index_state"]["ready"] == 3
    assert corpus["videos_by_index_state"]["indexing"] == 1
    # `data_status` verbatim from `corpus-summary`, never re-derived here.
    assert corpus["data_status"] in ("ok", "partial", "degraded", "indexing", "empty")
    # Two time axes, and both of them are seconds — never a rendered day.
    for stamp in (corpus["published"]["oldest"], corpus["published"]["newest"]):
        assert isinstance(stamp, int)
    assert corpus["last_indexed"] is None or isinstance(corpus["last_indexed"], int)

    # The queue, with the window its count was taken over.
    assert body["jobs"] == {
        "active": 2,
        "running": 1,
        "deferred": 1,
        "failed_recent": 1,
        "failed_window_s": FAILED_WINDOW_S,
    }
    assert body["counted_at"] <= int(time.time())
    assert body["redacted"] is False
    # Every clock in the payload, including the readiness observation's. The
    # page renders that one as ISO-8601 because a `<time datetime=…>` attribute
    # wants it; the JSON must not, or the one field React does not format is
    # the one Python already did.
    assert isinstance(body["readiness"]["checked_at"], int)
    assert "checked_at_s" not in body["readiness"]

    # Tags are a list of pairs, not an object keyed by corpus strings.
    assert isinstance(body["tags"], list)
    for row in body["tags"]:
        assert set(row) == {"tag", "videos"} and isinstance(row["videos"], int)
    for row in body["channels"]:
        assert set(row) == {"channel", "videos", "seconds"}
        assert isinstance(row["videos"], int) and isinstance(row["seconds"], float)

    for row in body["recent"]:
        assert set(row) == {
            "video_id",
            "title",
            "channel",
            "duration_s",
            "indexed_at",
            "thumb",
        }
        assert row["indexed_at"] is None or isinstance(row["indexed_at"], int)
        # A frame URL a browser resolves against the page it is reading, not
        # against PUBLIC_URL (dashboard.md §8).
        assert row["thumb"] is None or row["thumb"].startswith("/frames/")

    # The owner's half: present, and both figures are byte counts.
    assert isinstance(body["storage"]["keyframe_bytes"], int)
    assert isinstance(body["storage"]["database_bytes"], int)
    assert body["declared_models"], "the owner sees what the corpus was built with"


def test_the_ledger_json_is_the_tally_the_page_prints(tmp_path: Path) -> None:
    """The same numbers as `/dashboard/ledger`, which is the point of sharing
    the assembler: four videos split three ready and one indexing, three jobs
    split one queued, one running and one failed."""
    with make_client(tmp_path) as client:
        body = read(client, LEDGER)

    assert body["corpus"]["videos"] == 4
    assert body["videos_by_state"] == {
        "ready": 3,
        "pending": 0,
        "indexing": 1,
        "failed": 0,
        "stale": 0,
    }
    # A video state and a job state are deliberately different numbers.
    assert body["jobs_by_state"]["queued"] == 1
    assert body["jobs_by_state"]["running"] == 1
    assert body["jobs_by_state"]["failed"] == 1
    assert body["jobs_by_state"]["done"] == 0
    assert body["queue"]["active"] == 2
    assert body["queue"]["failed_window_s"] == FAILED_WINDOW_S
    for key in ("chunks", "tags", "channels", "cues", "keyframes", "ocr_lines"):
        assert isinstance(body["corpus"][key], int), key
    assert set(body["embed_backlog"]) == {"text", "frame"}


def test_the_ledger_carries_the_published_span_the_band_prints(
    tmp_path: Path,
) -> None:
    """The band under the video count reads "published <oldest> – <newest>",
    and the payload had no field for it (2026-09-05).

    Same name and same shape as the overview's, off the same `corpus_rollup`
    the counts above come from — one more read is not what this cost, and a
    client reading both payloads must not have to learn two spellings of one
    fact.
    """
    with make_client(tmp_path) as client:
        ledger = read(client, LEDGER)
        overview = read(client, OVERVIEW)

    span = ledger["corpus"]["published"]
    assert set(span) == {"oldest", "newest"}
    assert isinstance(span["oldest"], int) and isinstance(span["newest"], int)
    assert span["oldest"] <= span["newest"]
    # Epoch seconds, never the `day` filter's string the template renders.
    assert span == overview["corpus"]["published"]


def test_neither_payload_carries_a_rendered_clock(tmp_path: Path) -> None:
    """The rule with the one field that nearly broke it.

    Tom, 2026-09-05: typed on the wire, React formats. The assemblers still
    stamp the readiness observation with `iso_z` beside its epoch, because the
    Jinja pages wanted a `<time datetime=…>` attribute — so a payload that
    forwarded an assembler's dict wholesale would ship a rendered day under a
    rule that says it must not. Nothing here prints "4m 12s" or "3 hours ago"
    either.
    """
    with owner_client(tmp_path) as client:
        for path in (OVERVIEW, LEDGER):
            raw = json.dumps(read(client, path, headers=BEARER))
            assert not ISO_STAMP.search(raw), f"a rendered date reached {path}"
            assert not SPOKEN_DURATION.search(raw), f"a rendered duration reached {path}"


def test_the_ledger_takes_one_figure_out_of_gaps_and_not_its_prose(
    tmp_path: Path,
) -> None:
    """`queries.gaps` also returns the failed *rows*, and `video_stages.error`
    is the pipeline talking about the operator's box — yt-dlp output, worker
    URLs, cookiefile paths. It reaches neither surface from here, on the owner's
    instance as much as on the demo."""
    with make_client(tmp_path) as owner:
        body = read(owner, LEDGER)
        raw = json.dumps(body)
    assert set(body["gaps"]) == {"transcript_no_ocr"}
    assert isinstance(body["gaps"]["transcript_no_ocr"], int)
    for prose in (
        "worker returned 503",
        "Sign in to confirm you are not a bot",
        "cookiefile",
        "Half-indexed",
    ):
        assert prose not in raw, f"{prose} leaked into the ledger JSON"


def test_the_reads_take_no_parameters_and_stay_inside_the_pages_caps(
    tmp_path: Path,
) -> None:
    """Neither endpoint reads the query string, so there is nothing to clamp —
    and the lists are still the page's lists, bounded server-side."""
    with make_client(tmp_path) as client:
        plain = read(client, OVERVIEW)
        asked = read(client, f"{OVERVIEW}?limit=100000&offset=999999&max_tags=500")
        assert plain["corpus"] == asked["corpus"]
        assert len(asked["channels"]) <= CHANNEL_CAP
        assert len(asked["tags"]) <= TAG_CAP
        assert len(asked["recent"]) <= RECENT_CAP

        ledger_plain = read(client, LEDGER)
        ledger_asked = read(client, f"{LEDGER}?limit=100000")
        assert ledger_plain["videos_by_state"] == ledger_asked["videos_by_state"]


def test_the_worker_probe_is_the_pages_and_carries_only_its_three_fields(
    tmp_path: Path,
) -> None:
    """`/status` returns more than this surface has any use for. The readiness
    block is task, model and loaded — the operator's VRAM and queue depth are
    not on the page and are not in the JSON."""

    def worker(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "backends": [
                    {"task": "stt", "model": "served/stt", "loaded": True},
                    {"task": "embed", "model": "served/text", "loaded": False},
                ],
                "vram": {"used_mb": 9999},
                "queue": {"depth": 4},
            },
        )

    with owner_client(tmp_path, worker_handler=worker) as client:
        response = client.get(OVERVIEW, headers=BEARER)
        assert response.status_code == 200
        body = response.json()

    readiness = body["readiness"]
    assert readiness["mcp"] == "ready" and readiness["database"] == "ready"
    assert readiness["worker"]["state"] == "ready"
    assert [model["model"] for model in readiness["worker"]["models"]] == [
        "served/stt",
        "served/text",
    ]
    assert readiness["worker"]["models"][0]["loaded"] is True
    assert readiness["worker"]["models"][1]["loaded"] is False
    raw = json.dumps(body)
    assert "9999" not in raw and "vram" not in raw and "depth" not in raw


def test_an_unreachable_worker_degrades_rather_than_failing_the_payload(
    tmp_path: Path,
) -> None:
    """The default handler in these fixtures refuses the connection."""
    with owner_client(tmp_path) as client:
        body = read(client, OVERVIEW, headers=BEARER)
    assert body["readiness"]["worker"]["state"] == "unavailable"
    assert body["corpus"]["videos"] == 4  # the corpus half arrived anyway


# ------------------------------------------------------------- the projection


def test_the_public_projection_drops_the_operators_box_from_both_reads(
    tmp_path: Path,
) -> None:
    """§2.4, in JSON: the demo keeps the corpus and loses the machine it runs
    on — and it loses it by *not being sent it*, not by a flag a client could
    ignore. The worker is never even asked."""
    called = False

    def worker(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(
            200, json={"backends": [{"task": "embed", "model": "private/model-id"}]}
        )

    with make_client(tmp_path, public=DEMO, worker_handler=worker) as demo:
        demo.app.state.assembled.db.vectors.disable(PRIVATE_REASON)
        try:
            overview = read(demo, OVERVIEW)
            ledger = read(demo, LEDGER)
        finally:
            demo.app.state.assembled.db.vectors.enabled = True
            demo.app.state.assembled.db.vectors.reason = None

    assert not called, "the projection made an operator-only probe"
    for body in (overview, ledger):
        assert body["redacted"] is True
        assert body["storage"] is None
        assert body["readiness"]["worker"] is None
        # The *state* is the visitor's business — search answers differently
        # without the vector legs — and the reason is not.
        assert body["readiness"]["vectors"]["enabled"] is False
        assert body["readiness"]["vectors"]["reason"] is None
        raw = json.dumps(body)
        for leaked in (
            PRIVATE_REASON,
            "private/model-id",
            "Qwen/Qwen3-VL-Embedding-2B",
            "vidtheque.db",
            str(tmp_path),
        ):
            assert leaked not in raw, f"{leaked} is in the demo payload"
    assert overview["declared_models"] is None
    # …and the corpus is all still counted.
    assert overview["corpus"]["videos"] == 4
    assert ledger["jobs_by_state"]["failed"] == 1


def test_the_owner_sees_the_box_the_demo_does_not(tmp_path: Path) -> None:
    """The contrast, so the projection test above cannot pass by the payload
    being empty for everyone."""
    with owner_client(tmp_path) as client:
        client.app.state.assembled.db.vectors.disable(PRIVATE_REASON)
        try:
            body = read(client, OVERVIEW, headers=BEARER)
        finally:
            client.app.state.assembled.db.vectors.enabled = True
            client.app.state.assembled.db.vectors.reason = None

    assert body["redacted"] is False
    assert body["readiness"]["vectors"]["reason"] == PRIVATE_REASON
    assert any(
        row["value"] == "Qwen/Qwen3-VL-Embedding-2B" for row in body["declared_models"]
    )
    assert body["storage"]["database_bytes"] > 0
    # Even here, a byte total is a number and never a path.
    assert str(tmp_path) not in json.dumps(body)


# ----------------------------------------------------- the videos table (§20)


def test_the_two_video_reads_sit_behind_the_same_gate_and_are_get_only(
    tmp_path: Path,
) -> None:
    with owner_client(tmp_path) as client:
        for path in (LIBRARY, f"{LIBRARY}/{FIRST}"):
            refused = client.get(path)
            assert refused.status_code == 401, path
            assert refused.json()["error"] == "E_AUTH_REQUIRED"
            # Nothing about the corpus rides out on a refusal — not even the
            # title of the video that was asked for.
            assert "videos" not in refused.json()
            assert client.get(path, headers=BEARER).status_code == 200, path

    with make_client(tmp_path) as client:
        registered = {
            str(route.path): set(route.methods or ())
            for route in client.app.routes
            if str(getattr(route, "path", "")).startswith(LIBRARY)
        }
        assert set(registered) == {LIBRARY, f"{LIBRARY}/{{video_id}}"}
        for path, methods in registered.items():
            assert methods <= {"GET", "HEAD"}, path


def test_the_facade_search_hit_still_carries_every_field_the_page_reads(
    tmp_path: Path,
) -> None:
    """`/dashboard/api/search` is a superset of what the search page renders.

    The Jinja page and the React one read one payload — `search_payload`'s —
    and the Jinja one derives its receipt, its in-index link, its evidence
    badges and its marked snippet from fields the facade returns rather than
    from a second query. Pinning the set means a future trim of the facade
    fails a test instead of quietly emptying a page.

    `read()` also asserts the `no-store` this surface answers with, which the
    facade's handlers now carry on both of their prefixes.
    """
    with make_client(tmp_path) as client:
        payload = read(client, f"{FACADE_SEARCH}?q=cache")

    assert payload["results"], "the seeded corpus should match 'cache'"
    for hit in payload["results"]:
        missing = SEARCH_HIT_KEYS - set(hit)
        assert not missing, f"the search page reads these and the facade dropped them: {missing}"


def test_the_table_and_the_facade_are_two_contracts_and_two_paths(
    tmp_path: Path,
) -> None:
    """`/dashboard/api/videos` is the facade's listing and stays it.

    The two are not a duplicate: the facade answers "what is in the corpus" in
    the corpus's own shape, with `published` and `duration` already rendered
    for a reader of the tool's text block, and it lists only what is queryable.
    The table answers what the management page shows, which is a different set
    of rows and a different set of columns.
    """
    with make_client(tmp_path) as client:
        # `read()` like every other route on this surface: the facade answers
        # `no-store` too now, on both of its prefixes.
        facade = read(client, FACADE)
        table = read(client, LIBRARY)

    assert set(facade) == {"videos", "pagination"}
    # The facade's records still carry the rendered pair, untouched.
    assert facade["videos"][0]["published"] == "2024-04-01"
    assert ":" in facade["videos"][0]["duration"]
    # …and the three queryable videos, because that is the query surface's
    # meaning of "in the corpus".
    assert len(facade["videos"]) == 3

    # The table sees all four, including the one that never finished, and
    # carries the state the facade has no field for.
    assert len(table["videos"]) == 4
    assert {row["index_state"] for row in table["videos"]} == {"ready", "indexing"}
    assert "published" not in table["videos"][0]
    assert "duration" not in table["videos"][0]


def test_the_videos_table_json_is_the_pages_read_typed(tmp_path: Path) -> None:
    """The same four rows `/dashboard/videos` renders, in epochs and booleans.

    `all` means all: the half-indexed video is in the corpus and on the table,
    which is §5.2's default and the reason this page exists at all.
    """
    with make_client(tmp_path) as client:
        body = read(client, LIBRARY)

    assert body["redacted"] is False
    assert body["counted_at"] <= int(time.time())
    # Explicit, always — never left for a client to infer from `q`.
    assert body["order"] == "recency"
    # The exact count of the filtered set, not the tool's bounded probe: no
    # `approx_total` and no tilde reaches this payload.
    assert body["total"] == 4
    assert body["pagination"] == {"limit": 50, "offset": 0, "has_more": False}
    assert body["notes"] == []
    assert body["filters"] == {
        "q": None,
        "channel": None,
        "tags": [],
        "has": "any",
        "index_state": "all",
        "published_after": None,
        "published_before": None,
        "indexed_after": None,
        "indexed_before": None,
    }

    for row in body["videos"]:
        assert set(row) == {
            "video_id",
            "title",
            "channel",
            "published_at",
            "duration_s",
            "indexed_at",
            "index_state",
            "coverage",
            "tags",
            "thumb",
            "link",
        }
        # The two columns `list-videos` renders on its way out, undone.
        assert isinstance(row["published_at"], int)
        assert isinstance(row["duration_s"], float)
        assert row["indexed_at"] is None or isinstance(row["indexed_at"], int)
        assert set(row["coverage"]) == {"transcript", "ocr", "frames"}
        assert all(isinstance(v, bool) for v in row["coverage"].values())
        assert isinstance(row["tags"], list)
        # A frame URL a browser resolves against the page it is reading.
        assert row["thumb"] is None or row["thumb"].startswith("/frames/")

    by_id = {row["video_id"]: row for row in body["videos"]}
    assert by_id[HALF]["index_state"] == "indexing"
    assert by_id[HALF]["indexed_at"] is None  # it never finished
    assert by_id[FIRST]["coverage"] == {"transcript": True, "ocr": True, "frames": True}
    assert by_id[FIRST]["tags"] == ["topic:attention"]


def test_the_table_clamps_every_bound_and_says_when_one_moved(
    tmp_path: Path,
) -> None:
    """A limit above the cap is clamped, and the payload says so.

    The page echoes its clamps back into its own form, where a reader sees the
    accepted number in the box they typed into. A JSON caller has no form, so
    the sentence is the disclosure — and it is Python's, like every other piece
    of policy text on this surface.

    The four date bounds are the same rule and move for one more reason: each
    is filtered as a whole UTC day, so an instant becomes the day around it.
    Clamped or snapped, the query ran on something other than what the URL
    said, which is the narrowing CLAUDE.md forbids doing quietly.
    """
    with make_client(tmp_path) as client:
        clamped = read(client, f"{LIBRARY}?limit=100000&offset=999999")
        assert clamped["pagination"]["limit"] == 100  # the owner ceiling
        assert clamped["pagination"]["offset"] == 10_000
        notes = " ".join(clamped["notes"])
        assert "limit=100000 → 100" in notes
        assert "offset=999999 → 10000" in notes

        # A value the server did not recognise is not honoured and not an
        # error: it falls back, and says which value answered.
        coerced = read(client, f"{LIBRARY}?has=banana&index_state=nonsense")
        assert coerced["filters"]["has"] == "any"
        assert coerced["filters"]["index_state"] == "all"
        assert len(coerced["videos"]) == 4
        text = " ".join(coerced["notes"])
        assert "has=" in text and "index_state=" in text

        # A year out is the ceiling, and the note names the day that ran
        # rather than the year that did not.
        ceiling = iso_day(int(time.time()) + 365 * 86_400)
        future = read(client, f"{LIBRARY}?published_before=2999-01-01")
        assert f"published_before=2999-01-01 → {ceiling}" in " ".join(future["notes"])
        # `_before` is exclusive: the epoch echoed is the start of the day
        # *after* the one the note names, which is what includes that day.
        assert future["filters"]["published_before"] % 86_400 == 0

        # Below the floor: the column is unix seconds and 1970 is where they
        # start, so the filter that ran is the first day and it says so.
        ancient = read(client, f"{LIBRARY}?indexed_after=1960-01-01")
        assert ancient["filters"]["indexed_after"] == 0
        assert "indexed_after=1960-01-01 → 1970-01-01" in " ".join(ancient["notes"])

        # Inside the bounds and still moved: a bare unix stamp is an instant,
        # and the query filtered the whole day around it.
        midday = read(client, f"{LIBRARY}?published_after=1673957000")
        assert midday["filters"]["published_after"] == 1673913600
        assert "published_after=1673957000 → 2023-01-17" in " ".join(midday["notes"])

        instant = read(client, f"{LIBRARY}?indexed_before=2023-01-17T12:00:00Z")
        assert instant["filters"]["indexed_before"] == 1673913600 + 86_400
        assert "indexed_before=2023-01-17T12:00:00Z → 2023-01-17" in " ".join(instant["notes"])

        # A number that was inside the bounds says nothing at all, and neither
        # does a date that already names the day it was going to be filtered on.
        assert read(client, f"{LIBRARY}?limit=2")["notes"] == []
        assert read(client, f"{LIBRARY}?published_after=2023-01-17")["notes"] == []


def test_the_table_orders_and_filters_the_way_the_page_does(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        titled = read(client, f"{LIBRARY}?order=title")
        assert titled["order"] == "title"
        titles = [row["title"] for row in titled["videos"]]
        assert titles == sorted(titles)

        # `relevance` is the default *with* a query and refused without one:
        # the tool's own typed refusal, at the status the code maps to.
        queried = read(client, f"{LIBRARY}?q=cache")
        assert queried["order"] == "relevance"
        assert queried["filters"]["q"] == "cache"
        refused = client.get(f"{LIBRARY}?order=relevance")
        assert refused.status_code == 400
        assert refused.json()["error"] == "E_ORDER_SCOPE"

        # One state, and the filtered total moves with it.
        only = read(client, f"{LIBRARY}?index_state=indexing")
        assert [row["video_id"] for row in only["videos"]] == [HALF]
        assert only["total"] == 1

        # Two rows at a time, and `has_more` rather than an exact page count.
        paged = read(client, f"{LIBRARY}?limit=2")
        assert len(paged["videos"]) == 2
        assert paged["pagination"]["has_more"] is True
        assert paged["total"] == 4

        # The two time axes, never overloaded: `published_*` picks videos, and
        # the payload echoes the epochs the query actually filtered on — the
        # `_before` bound exclusive, which is what includes its own date.
        dated = read(client, f"{LIBRARY}?published_after=2024-01-01")
        assert dated["filters"]["published_after"] == 1704067200
        assert dated["filters"]["published_before"] is None
        assert all(row["published_at"] >= 1704067200 for row in dated["videos"])
        assert dated["total"] == len(dated["videos"]) < 4

        # A date that will not parse is a refusal, not a dropped filter.
        bad = client.get(f"{LIBRARY}?indexed_after=nonsense")
        assert bad.status_code == 400
        assert bad.json()["error"] == "E_BAD_TIME_FORMAT"
        assert bad.json()["next"]


# ---------------------------------------------------- the video detail (§20)


def test_the_detail_json_is_the_pipeline_the_facade_cannot_answer(
    tmp_path: Path,
) -> None:
    """§5.3's panels: the seven stages, the counts, the shots, the strip.

    None of it has an equivalent in `/api/videos/{id}`, which is
    `video-summary` — the corpus's answer about a video. This is the
    pipeline's answer about the same video, which is the page's whole argument.
    """
    with make_client(tmp_path) as client:
        body = read(client, f"{LIBRARY}/{FIRST}?frames=2")
        # The endpoint this payload points at instead of serving cues itself.
        assert client.get(body["transcript"]["endpoint"]).status_code == 200

    assert body["redacted"] is False
    video = body["video"]
    assert video["video_id"] == FIRST
    assert isinstance(video["published_at"], int)
    assert isinstance(video["duration_s"], float)
    # Presence, not location (§5.3): no path to anything on the operator's box.
    assert set(video) == {
        "video_id", "title", "channel", "published_at", "duration_s", "language",
        "index_state", "indexed_at", "added_at", "url", "description", "tags",
    }
    # `data_status` verbatim from `video-summary`, never re-derived.
    assert body["data_status"] == "ok"
    assert body["summary_error"] is None

    # All seven, with the ones that never ran present rather than missing.
    assert [stage["stage"] for stage in body["stages"]] == [
        "fetch", "stt", "chunk", "text_embed", "keyframe", "ocr", "frame_embed",
    ]
    assert any(stage["state"] == "absent" for stage in body["stages"])
    assert any(stage["model_key"] == "seed" for stage in body["stages"])

    counts = body["counts"]
    assert counts["cues"] == 6 and counts["chunks"] == 1
    assert counts["keyframes_kept"] < counts["keyframes"]  # the dedup story
    assert all(isinstance(n, int) for n in counts.values())
    assert body["cue_origins"] == {"whisperx": 6}

    # The transcript is totals and a pointer. This payload does not serve cues:
    # the endpoint it names already pages them under the same clamps.
    assert body["transcript"]["cues"] == 6
    assert body["transcript"]["endpoint"] == f"{ROOT}/api/videos/{FIRST}/cues"
    assert "cues" not in body

    # Shots are positions, not percentages: the runtime is on the payload and
    # the arithmetic belongs to whoever draws the band.
    shots = body["shots"]["shots"]
    assert shots and all(shot["end_s"] >= shot["start_s"] for shot in shots)
    assert all("left" not in shot and "width" not in shot for shot in shots)
    assert all(shot["preview"].startswith("/frames/") for shot in shots)
    assert body["shots"]["capped"] is False

    # The strip, paged, with its OCR boxes already normalised 0–1.
    frames = body["frames"]
    assert len(frames["frames"]) == 2 and frames["has_more"] is True
    first = frames["frames"][0]
    assert first["thumb"].endswith("w=192&q=70")
    assert first["large"].endswith("w=1280&q=70")
    assert "base64" not in json.dumps(frames)
    box = first["lines"][0]["box"]
    assert len(box) == 4 and all(0.0 <= value <= 1.0 for value in box)

    # The runs that touched this video, capped and never counted (§16.4) — the
    # code a job failed with, and never the message it failed with.
    history = body["job_history"]
    assert [job["job_id"] for job in history["jobs"]] == ["job_running001"]
    assert history["cap"] == 10
    job = history["jobs"][0]
    assert set(job) == {
        "job_id", "state", "kind", "created_at", "finished_at",
        "error_code", "degraded_stages",
    }
    assert isinstance(job["created_at"], int) and job["finished_at"] is None


def test_the_detail_strip_is_clamped_and_pages_independently_of_the_cues(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        clamped = read(client, f"{LIBRARY}/{FIRST}?frames=100000&frame_offset=999999")
        assert clamped["frames"]["limit"] == 96
        assert clamped["frames"]["offset"] == 100_000
        notes = " ".join(clamped["notes"])
        assert "frames=100000 → 96" in notes and "frame_offset=999999 → 100000" in notes
        # Past the end of the strip is an empty page, not a refusal.
        assert clamped["frames"]["frames"] == []
        assert clamped["frames"]["has_more"] is False

        second = read(client, f"{LIBRARY}/{FIRST}?frames=1&frame_offset=1")
        assert [frame["ord"] for frame in second["frames"]["frames"]] == [1]
        assert second["notes"] == []


def test_an_unknown_video_is_a_typed_404_with_a_way_back(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get(f"{LIBRARY}/not-a-video")
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["error"] == "E_UNKNOWN_VIDEO"
        assert "not-a-video" in body["message"]
        assert body["next"]


def test_the_detail_projection_drops_the_operators_prose_and_its_models(
    tmp_path: Path,
) -> None:
    """§2.4, on the one read page that carries the pipeline's own words.

    The demo gets the video detail whole — every panel, every count, every
    clock — minus the two fields that are the operator's console: `model_key`,
    a declared model id, and `error`, which is yt-dlp quoted verbatim and
    carries cookiefile paths and player-client names. The half-indexed video is
    the one that has both.
    """
    with owner_client(tmp_path) as owner:
        seen = read(owner, f"{LIBRARY}/{HALF}", headers=BEARER)
    with make_client(tmp_path, public=DEMO) as demo:
        hidden = read(demo, f"{LIBRARY}/{HALF}")
        table = read(demo, LIBRARY)

    # The owner sees both, or the projection below proves nothing.
    assert any(stage["model_key"] == "yt-dlp-2026.07.04" for stage in seen["stages"])
    assert any(stage["error"] for stage in seen["stages"])
    assert seen["redacted"] is False

    assert hidden["redacted"] is True
    assert all(stage["model_key"] is None for stage in hidden["stages"])
    assert all(stage["error"] is None for stage in hidden["stages"])
    # …and the shape survives: the states, the versions and the clocks are what
    # a reader can act on, and dropping them would leave an empty shell.
    assert [stage["state"] for stage in hidden["stages"]] == [
        stage["state"] for stage in seen["stages"]
    ]
    assert any(stage["state"] == "failed" for stage in hidden["stages"])
    # The refusal `video-summary` answers a mid-pipeline video with is policy
    # text, and it stays: it is why the panels below it are thin.
    assert hidden["summary_error"]["code"] == "E_INDEXING"

    raw = json.dumps(hidden)
    for leaked in (
        "Sign in to confirm you are not a bot",
        "cookiefile",
        "yt-dlp-2026.07.04",
        "Qwen/Qwen3-VL-Embedding-2B",
        "worker:8081",
        "vidtheque.db",
        "keyframes/",
        str(tmp_path),
    ):
        assert leaked not in raw, f"{leaked} is in the demo detail payload"

    # The table itself is not redacted — §2.4 gives it to the demo whole,
    # because everything on it is corpus rather than deployment.
    assert len(table["videos"]) == 4
    assert table["redacted"] is True


def test_neither_video_payload_carries_a_rendered_clock(tmp_path: Path) -> None:
    """The same rule as the overview and the ledger, on the two payloads whose
    source fields are rendered strings to begin with.

    `list-videos` writes `published` as an `iso_day` string and `duration` as a
    `1:56:40` clock, because its reader is a model reading a `tsv` block. Both
    would have travelled verbatim if this surface had forwarded the tool's
    record, which is exactly the failure this scans for.
    """
    with owner_client(tmp_path) as client:
        for path in (LIBRARY, f"{LIBRARY}/{FIRST}", f"{LIBRARY}/{HALF}"):
            raw = json.dumps(read(client, path, headers=BEARER))
            assert not ISO_STAMP.search(raw), f"a rendered date reached {path}"
            assert not SPOKEN_DURATION.search(raw), f"a rendered duration reached {path}"
            # `duration_clock`'s shape, which no regex above would catch on its
            # own: a bare `1:56:40` in a payload of seconds.
            assert not re.search(r'"\d+:\d{2}(?::\d{2})?"', raw), path


# -------------------------------------------------------- the cues endpoint (§3)

# The transcript pane's source, and the one endpoint of the three §3 names where
# the typed half had to be *added* rather than a string dropped.
CUES = f"{ROOT}/api/videos/{FIRST}/cues"


def test_the_cues_endpoint_carries_the_typed_half_beside_the_strings(
    tmp_path: Path,
) -> None:
    """Tom, 2026-09-05: add the typed fields, cut the strings at the port.

    `at`, `conf` and `chunk` are renderings of numbers `api._cue_rows` already
    had, and this endpoint sent only the renderings. The numbers are on the wire
    now, under `_cue_rows`' own names, and every one of them has to agree with
    the string beside it — two ways of saying when a cue starts that can
    disagree is worse than one that is only a string.
    """
    with make_client(tmp_path) as client:
        body = read(client, CUES)

    assert body["cues"], "the fixture's six cues"
    for cue in body["cues"]:
        assert isinstance(cue["start_s"], float)
        assert isinstance(cue["end_s"], float)
        assert cue["end_s"] >= cue["start_s"]
        # `at` is `clock(start_s)` and `t` is its floor: the same instant.
        assert cue["at"] == clock(cue["start_s"])
        assert cue["t"] == int(cue["start_s"])

        if cue["avg_logprob"] is None:
            assert cue["conf"] is None
        else:
            assert isinstance(cue["avg_logprob"], float)
            assert cue["conf"] == f"{cue['avg_logprob']:.2f}"

        # The composed sentence, and the five fields it is composed from.
        opens = cue["chunk_opens"]
        if opens is None:
            assert cue["chunk"] is None
        else:
            assert set(opens) == {"seq", "start_s", "end_s", "n_chars", "n_words"}
            assert isinstance(opens["seq"], int)
            assert isinstance(opens["n_words"], int)
            assert isinstance(opens["n_chars"], int)
            assert cue["chunk"] == (
                f"chunk {opens['seq']} · "
                f"{clock(opens['start_s'])}–{clock(opens['end_s'])} · "
                f"{opens['n_words']} words · {opens['n_chars']} chars"
            )
        # `in_chunk` is the two markers collapsed into one bool, which is why
        # both of them are sent: a chunk's last cue is not its first.
        assert isinstance(cue["chunk_closes"], bool)
        assert cue["in_chunk"] is (opens is not None or cue["chunk_closes"])

    # Without this the branch above never ran and the sentence is unasserted.
    assert any(cue["chunk_opens"] is not None for cue in body["cues"])


def test_the_cues_endpoint_drops_nothing_new_in_the_projection(
    tmp_path: Path,
) -> None:
    """The typed fields are corpus, not deployment.

    A transcript is what §2.4 gives the demo whole, so this endpoint has never
    redacted a field and the addition must not have introduced the first one.
    """
    with make_client(tmp_path) as owner:
        mine = read(owner, CUES)
    with make_client(tmp_path, public=DEMO) as demo:
        theirs = read(demo, CUES)

    assert theirs["cues"] == mine["cues"]


# ------------------------------------------------------------ following (§22)

# The follow fixture is `test_dashboard_following.py`'s, because a follow, a
# ledger and the two job kinds a check writes are what these routes read and
# that file already seeds all three. Its clients build the same deployments
# under the same token, so the two suites ask one corpus different questions.
FOLLOWING = f"{ROOT}/api/following"
SLUG = "andrej-karpathy"
FOLLOW = f"{FOLLOWING}/{SLUG}"


def test_the_following_json_is_absent_where_its_whole_surface_is(
    tmp_path: Path,
) -> None:
    """dashboard.md §18.6, applied to the JSON — and it is the whole decision
    this pair of routes had to make.

    The two following reads are declared with the write routes rather than
    beside the other reads: a surface whose every affordance POSTs has nothing
    to show a deployment that registers no write side, and a route that exists
    and refuses is a route somebody probes. The rule outlived the pages it was
    written for — a shell that can read a follow is a shell that can offer to
    change one — so a payload reachable where its writes 404 would still be the
    way back in that §2.3 exists to close.
    """
    with follow_owner(tmp_path, readonly=True) as demo:
        # The reads this deployment *does* serve still answer, or the 404s
        # below would be proving the dashboard is off rather than that this
        # surface is absent.
        assert demo.get(LIBRARY, headers=BEARER).status_code == 200
        for path in (FOLLOWING, FOLLOW):
            assert demo.get(path, headers=BEARER).status_code == 404, path

    with follow_client(tmp_path) as anonymous:  # auth=none
        assert anonymous.get(OVERVIEW).status_code == 200
        for path in (FOLLOWING, FOLLOW):
            assert anonymous.get(path).status_code == 404, path


def test_the_following_json_takes_the_pages_gate_and_is_get_only(
    tmp_path: Path,
) -> None:
    with follow_owner(tmp_path) as client:
        for path in (FOLLOWING, FOLLOW):
            refused = client.get(path)
            assert refused.status_code == 401, path
            assert refused.json()["error"] == "E_AUTH_REQUIRED"
            # Nothing about what this box watches rides out on a refusal.
            assert "follows" not in refused.json()
            assert client.get(path, headers=BEARER).status_code == 200

        registered = {
            str(route.path): set(route.methods or ())
            for route in client.app.routes
            if str(getattr(route, "path", "")).startswith(f"{ROOT}/api/following")
        }
        assert set(registered) == {FOLLOWING, f"{FOLLOWING}/{{slug}}"}
        for path, methods in registered.items():
            assert methods <= {"GET", "HEAD"}, path


def test_the_following_list_is_the_bands_and_the_rows_typed(tmp_path: Path) -> None:
    """The page's four reads, as figures: the band, the budget, the rows.

    The fixture follows two channels — one active with a length rule, an error
    code and a ledger, one paused — so every figure below has a number that
    could only have come from those rows.
    """
    with follow_owner(tmp_path) as client:
        body = read(client, FOLLOWING, headers=BEARER)

    # Explicit, never inferred, and it is `list_follows`' own single order:
    # whatever is failing, then whatever was checked most recently.
    assert body["order"] == "failing_first"
    assert body["totals"]["follows"] == 2
    assert body["totals"]["active"] == 1
    assert body["totals"]["paused"] == 1
    assert body["totals"]["brought_in"] == 1
    assert body["totals"]["held"] == 2
    assert [follow["slug"] for follow in body["follows"]] == [SLUG, "paused-channel"]
    assert body["pagination"] == {"limit": FOLLOW_PAGE, "offset": 0, "has_more": False}
    assert body["notes"] == []

    # The budget is seconds of video against an hours ceiling, and the window
    # it rolls over is named rather than assumed to be a day.
    assert body["budget"]["spent_s"] == 3600.0
    assert isinstance(body["budget"]["ceiling_h"], float)
    assert body["budget"]["window_s"] == BUDGET_WINDOW_S
    # The two deployment facts that say what the clocks mean.
    assert body["checks_enabled"] is True
    assert body["vectors"] is True

    active = body["follows"][0]
    assert active["state"] == "active"
    assert active["min_duration_s"] == 480
    assert active["max_per_check"] == 5
    assert active["tags"] == ["topic:llm"]
    assert active["tabs"] == ["videos"]
    assert isinstance(active["last_check_at"], int)
    # The table's column is the code. The fetch failure's own prose is read on
    # the follow's page, not in a list of sixty rows.
    assert active["last_error_code"] == "E_RATE_LIMIT"
    assert "last_error_message" not in active
    # No rendered rule anywhere: the page compresses one to `0:08:00 floor`
    # and `every 6h` for a column an operator scans, and both are renderings.
    assert "facts" not in active

    # The held band names what is waiting on a person, with the follow that
    # holds it, and is capped independently of the pager.
    assert body["held_cap"] == HELD_BAND_CAP
    assert body["held_more"] is False
    assert [item["slug"] for item in body["held"]] == [SLUG]
    assert body["held"][0]["url"].endswith("heldreview1")


def test_a_read_and_a_write_describe_a_follow_identically(tmp_path: Path) -> None:
    """§21's outcome block and §22's row are one function, and this is why.

    A client that pauses a follow and then re-lists it must not be handed two
    shapes for the same row. The write re-reads after the write; the read reads
    the same columns through the same `Rules.from_row`, so the outcome and the
    detail are the same dictionary — and the list is that dictionary minus the
    message its table has no column for.
    """
    with follow_owner(tmp_path) as client:
        sign_in(client)
        paused = client.post(
            f"{ROOT}/following/{SLUG}/state",
            data={"action": "pause"},
            headers={"Accept": "application/json", **SAME_ORIGIN},
        )
        assert paused.status_code == 200, paused.text
        outcome = paused.json()["follow"]

        listed = read(client, FOLLOWING, headers=BEARER)["follows"]
        row = next(item for item in listed if item["slug"] == SLUG)
        detail = read(client, FOLLOW, headers=BEARER)["follow"]

    assert row["state"] == "paused"
    # The outcome and the detail are one function, key for key and value for
    # value: nothing is renamed, re-typed or re-read into a second shape.
    assert detail == outcome
    # The list is the one payload that stops at the code. Everything it does
    # carry, it carries identically.
    assert set(outcome) - set(row) == {"last_error_message"}
    assert {key: outcome[key] for key in row} == row


def test_the_follow_detail_is_the_three_bands_typed(tmp_path: Path) -> None:
    """§18.4, as figures — and the third band is the point of the endpoint."""
    with follow_owner(tmp_path) as client:
        body = read(client, FOLLOW, headers=BEARER)

    # Band 1: the rule as columns, the clocks as epochs, the error as its code
    # and the operator's own message beside it.
    assert body["follow"]["slug"] == SLUG
    assert body["follow"]["check_interval_s"] == 21_600
    assert body["follow"]["last_error_code"] == "E_RATE_LIMIT"
    assert body["follow"]["last_error_message"] == "the source rate-limited this box"
    # The deployment fact the clocks above are read against, on this payload as
    # well as on the list's.
    assert body["checks_enabled"] is True
    # The sentence is not sent: `describe` renders a policy as English and the
    # columns above are what it renders. One renderer, and it is not this one.
    assert "sentence" not in body["follow"] and "sentence" not in body

    # Band 2: this follow's checks and the jobs they queued, each by job id
    # rather than as a second copy of the job.
    assert [check["job_id"] for check in body["checks"]] == ["job_followchk1"]
    assert body["checks"][0]["state"] == "done"
    assert isinstance(body["checks"][0]["finished_at"], int)
    assert [job["job_id"] for job in body["index_jobs"]] == ["job_followidx1"]
    assert body["index_jobs"][0]["n_done"] == 1
    assert body["in_flight"] is None
    assert body["caps"] == {"checks": CHECK_CAP, "index_jobs": INDEX_JOB_CAP}

    # Band 3: everything the rule turned away, newest decision first, with the
    # reason verbatim — it carries the number that made the call, which is the
    # whole argument for the band.
    assert body["order"] == "newest"
    decisions = [row["decision"] for row in body["seen"]]
    assert "queued" not in decisions  # a candidate it accepted is not one it passed over
    assert set(decisions) == {
        "already_indexed",
        "held_review",
        "held_budget",
        "skipped_duration",
    }
    reasons = [row["reason"] for row in body["seen"]]
    assert "7:48, shorter than your 8:00 floor" in reasons
    assert {row["judged_from"] for row in body["seen"]} == {"listing", "probe"}
    near = next(row for row in body["seen"] if row["duration_s"] == 468.0)
    assert isinstance(near["decided_at"], int) and near["published_at"] == 1740000000

    # The per-follow tally, off the grouped query the page already made.
    assert body["brought_in"] == 1
    assert body["counts"]["skipped_duration"] == 3
    assert body["counts"]["queued"] == 1

    # The one derived observation, typed rather than spoken: two of the six on
    # this page are inside a minute of the floor and one is half an hour
    # outside it, so the number is 2 — which is the difference between reading
    # the ledger and counting the band.
    assert body["near_miss"] == {
        "count": 2,
        "of": len(body["seen"]),
        "within_s": NEAR_MISS_S,
        "edge": "floor",
    }


def test_both_payloads_say_when_follow_checks_are_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one fact on these payloads that is about the box, not the follow.

    With `VIDTHEQUE_FOLLOW_CHECKS=0` every `next_check_at` on both payloads is
    a time at which nothing will happen. Neither page could say so from the
    columns beside it — no arithmetic over a row produces "the scheduler is
    off" — so it is a field on both, and it names no environment variable.
    """
    monkeypatch.setenv("VIDTHEQUE_FOLLOW_CHECKS", "0")
    with follow_owner(tmp_path) as client:
        listing = read(client, FOLLOWING, headers=BEARER)
        detail = read(client, FOLLOW, headers=BEARER)

    assert listing["checks_enabled"] is False
    assert detail["checks_enabled"] is False
    # The clock is still sent, because it is still what the row says: the fact
    # above is what tells a page how to read it.
    assert isinstance(detail["follow"]["next_check_at"], int)


def test_the_near_miss_is_absent_rather_than_zero(tmp_path: Path) -> None:
    """A "0 of the last 25" finding is a fact about nothing dressed as one.

    The paused follow has no length rule at all, so there is no edge to be near
    and the field is `null` rather than a zero a client would have to know to
    hide.
    """
    with follow_owner(tmp_path) as client:
        assert read(client, f"{FOLLOWING}/paused-channel", headers=BEARER)[
            "near_miss"
        ] is None


def test_both_following_reads_clamp_and_say_when_a_bound_moved(
    tmp_path: Path,
) -> None:
    """Never a prompt-only limit, and never a silent one either.

    The pages echo an accepted `limit` back into the pager they render; a JSON
    caller has no pager to read it out of, so the clamp is disclosed in `notes`
    exactly as the videos table discloses its own.
    """
    with follow_owner(tmp_path) as client:
        listing = read(client, f"{FOLLOWING}?limit=100000", headers=BEARER)
        detail = read(client, f"{FOLLOW}?limit=100000&offset=-4", headers=BEARER)

    assert listing["pagination"]["limit"] == FOLLOW_PAGE_MAX
    assert listing["notes"] == [f"limit=100000 → {FOLLOW_PAGE_MAX}"]
    assert detail["pagination"]["limit"] == SEEN_PAGE_MAX
    assert detail["pagination"]["offset"] == 0
    assert detail["notes"] == [f"limit=100000 → {SEEN_PAGE_MAX}", "offset=-4 → 0"]


def test_an_unknown_slug_is_a_typed_404_with_a_way_back(tmp_path: Path) -> None:
    with follow_owner(tmp_path) as client:
        response = client.get(f"{FOLLOWING}/no-such-thing", headers=BEARER)
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["error"] == "E_UNKNOWN_FOLLOW"
        assert "no-such-thing" in body["message"]
        assert body["next"]


def test_neither_following_payload_carries_a_rendered_clock(tmp_path: Path) -> None:
    """The same rule as the four payloads above, on the surface that had the
    most rendered strings to leave behind.

    The two pages print `0:08:00 floor`, `every 6h`, a spoken elapsed time per
    check and the rule as a whole English sentence. None of them is a value a
    client cannot compute from the columns beside it, so none of them is here —
    and the scan is what keeps it that way.

    `reason` is the deliberate exception and is not a rendering: it is the
    receipt the check wrote, policy text under `DECISIONS.md`'s split, and it
    carries the number that made the decision inside a sentence rather than as
    a formatted field.
    """
    with follow_owner(tmp_path) as client:
        for path in (FOLLOWING, FOLLOW):
            payload = read(client, path, headers=BEARER)
            for row in payload.get("seen", []):
                row["reason"] = ""
            raw = json.dumps(payload)
            assert not ISO_STAMP.search(raw), f"a rendered date reached {path}"
            assert not SPOKEN_DURATION.search(raw), f"a rendered duration reached {path}"
            assert not re.search(r'"\d+:\d{2}(?::\d{2})?"', raw), path
            # The sentence renderers, by the words only they produce.
            assert "Every 6 hours" not in raw
            assert "passed over" not in raw


# --------------------------------------------- the jobs table (§5.4, the list)

# The two oldest JSON routes on this surface, and the two the React port found
# short: the poll target had no row headline, and neither payload said what it
# had filtered on.
JOBS = f"{ROOT}/api/jobs"
JOB = f"{ROOT}/api/jobs/job_finished01"
RUNNING = f"{ROOT}/api/jobs/job_running001"


def _typed(value):  # type: ignore[no-untyped-def]
    """The payload with every `text` block dropped.

    The jobs pair is the one place on this surface that still carries rendered
    strings beside its numbers — a script with no formatter of its own reads
    them, and §5.4 deletes them with that script. The scans below are about the
    *typed* half, which is the half a React page reads and the half that has to
    be complete on its own.
    """
    if isinstance(value, dict):
        return {k: _typed(v) for k, v in value.items() if k != "text"}
    if isinstance(value, list):
        return [_typed(v) for v in value]
    return value


def test_the_jobs_list_says_what_each_row_holds(tmp_path: Path) -> None:
    """The row headline, on the payload as well as the page (§5.4, 2026-09-05).

    A jobs table whose rows print only `job_uid` is a list of opaque handles,
    which is why the Jinja page grew `contents` in the first place — and the
    poll target went without it, so a React table had a count where the title
    goes. It is on both now, out of one grouped read.
    """
    with make_client(tmp_path) as client:
        payload = read(client, JOBS)
    rows = {row["job_id"]: row for row in payload["jobs"]}

    running = rows["job_running001"]["contents"]
    # Two items, one of them resolved: the first video's title, the rest
    # counted after it, and the channel every resolved item came from.
    assert running["title"] == "Let's build GPT: from scratch"
    assert running["more"] == 1
    assert running["channel"] == "Andrej Karpathy"
    assert running["note"] is None

    # A job whose items have not been fetched has no title to print, and says
    # so with the count it does have rather than borrowing the id as a name.
    assert rows["job_deferred01"]["contents"] == {
        "title": None,
        "more": 0,
        "channel": None,
        "note": "1 item(s), none fetched yet",
    }


def test_the_jobs_list_echoes_the_filters_it_actually_ran(tmp_path: Path) -> None:
    """What narrowed the set, resolved server-side and sent back.

    The page re-prints its own values into its band; a JSON caller has no band,
    so it reads them here. `error_code` is `None` rather than the form's empty
    string, and `limit`/`offset` stay in `pagination` — one fact, one spelling.
    """
    with make_client(tmp_path) as client:
        default = read(client, JOBS)
        narrowed = read(
            client, f"{JOBS}?state=failed&kind=index&error_code=E_SOURCE&order=priority"
        )

    assert default["filters"] == {
        "state": "all",
        "kind": "all",
        "error_code": None,
        "degraded": False,
        "order": "newest",
    }
    assert default["notes"] == []
    assert narrowed["filters"] == {
        "state": "failed",
        "kind": "index",
        "error_code": "E_SOURCE",
        "degraded": False,
        "order": "priority",
    }


def test_a_jobs_filter_that_fell_back_says_so_rather_than_going_quiet(
    tmp_path: Path,
) -> None:
    """The `all` invariant, on the payload that had no way to state it.

    `state=nonsense` has always fallen back to `all` — the right behaviour, and
    silent, which is the half that is wrong: a listing answering a question
    nobody asked, with nothing on it saying so, is the wrong result set
    reported with total confidence. Each vocabulary names the value that ran.
    """
    with make_client(tmp_path) as client:
        payload = read(
            client,
            f"{JOBS}?state=nonsense&kind=sideways&order=alphabetical&degraded=yes",
        )

    assert payload["filters"]["state"] == "all"
    assert payload["filters"]["kind"] == "all"
    assert payload["filters"]["order"] == "newest"
    assert payload["filters"]["degraded"] is False
    notes = payload["notes"]
    assert any("state='nonsense'" in note and "state=all" in note for note in notes)
    assert any("kind='sideways'" in note for note in notes)
    assert any("order='alphabetical'" in note for note in notes)
    assert any("degraded='yes'" in note for note in notes)
    for note in notes:
        assert note.startswith("note: ")
    # The rows are the unnarrowed set, which is what the notes are about.
    assert len(payload["jobs"]) == 3


def test_the_jobs_list_clamps_every_bound_and_says_when_one_moved(
    tmp_path: Path,
) -> None:
    """Server-side clamps, named — the same wording as the videos table's (§20).

    And silent when nothing moved: a payload that announced a clamp on every
    request is a line nobody reads by the second page.
    """
    with make_client(tmp_path) as client:
        clamped = read(client, f"{JOBS}?limit=100000&offset=99999999")
        quiet = read(client, f"{JOBS}?limit=25")
        long_code = read(client, f"{JOBS}?error_code={'E' * 200}")

    assert clamped["pagination"]["limit"] == 100
    assert clamped["pagination"]["offset"] == 10_000
    assert any(
        "limit=100000 → 100" in note and "offset=99999999 → 10000" in note
        for note in clamped["notes"]
    )
    assert quiet["notes"] == []
    # A code is a token, not a sentence: the filter carries 64 characters and
    # says it cut the rest, rather than filtering on a prefix in silence.
    assert len(long_code["filters"]["error_code"]) == 64
    assert any("error_code was cut" in note for note in long_code["notes"])


def test_the_jobs_list_keeps_the_projection_on_the_fields_it_grew(
    tmp_path: Path,
) -> None:
    """§2.4 on the row headline: the title and the channel are corpus, the
    submitted URL is not — it is `args_json`'s content by another name.

    The page and the payload apply one rule, because they are one assembly:
    neither reads a field the other drops.
    """
    with make_client(tmp_path, public=DEMO) as demo:
        payload = read(demo, JOBS)
    rows = {row["job_id"]: row for row in payload["jobs"]}

    assert rows["job_running001"]["contents"]["title"] == "Let's build GPT: from scratch"
    assert rows["job_running001"]["contents"]["channel"] == "Andrej Karpathy"
    assert rows["job_deferred01"]["error_code"] == "E_RATE_LIMIT"
    assert rows["job_deferred01"]["error_message"] is None
    raw = json.dumps(payload)
    for leaked in (
        "cookiefile",
        "/home/dev/.cookies.txt",
        "youtu.be/deferredvid",
        "Sign in to confirm",
        str(tmp_path),
    ):
        assert leaked not in raw, f"{leaked} is in the demo payload"


def test_the_jobs_lists_new_fields_carry_no_rendered_clock(tmp_path: Path) -> None:
    """The typed rule, on the half of this payload that is not the `text` block.

    The jobs pair is the one place here that still ships rendered strings, and
    they are fenced: every one is under `text`, every one renders a number sent
    beside it, and §5.4 deletes the block with `static/jobs.js`. Nothing added
    on 2026-09-05 may join them.
    """
    with make_client(tmp_path) as client:
        payload = read(client, f"{JOBS}?limit=100000")
    grown = json.dumps(
        {
            "contents": [_typed(row["contents"]) for row in payload["jobs"]],
            "filters": payload["filters"],
            "notes": payload["notes"],
        }
    )
    assert not ISO_STAMP.search(grown), "a rendered date reached the jobs list"
    assert not SPOKEN_DURATION.search(grown), "a rendered duration reached the jobs list"
    assert not re.search(r'"\d+:\d{2}(?::\d{2})?"', grown)


# ------------------------------------------- one job's war story (§5.4, typed)


def test_the_job_detail_carries_the_panels_the_page_renders(tmp_path: Path) -> None:
    """The six fields the payload dropped on its way out of `_job_detail`.

    The page has rendered all of them since phase 2 and the poll target sent
    `job`, `items` and `events`, so a React detail page could show the item
    table and nothing underneath it — including the degraded list, which is the
    one panel this page exists for: `done`, `n_failed=0`, and a stage failed
    underneath, which is a search channel silently missing.
    """
    with make_client(tmp_path) as client:
        finished = read(client, JOB)
        running = read(client, RUNNING)

    # Items by state, beside the job's own five counts.
    assert finished["counts"] == {"done": 1, "failed": 1}
    assert finished["error_counts"] == {"E_SOURCE": 1}
    assert finished["items_capped"] is False

    # The silent loss, by item, by stage, and on a video a reader can open.
    assert [row["stage"] for row in finished["degraded"]] == ["ocr"]
    assert finished["degraded"][0]["seq"] == 0
    assert isinstance(finished["degraded"][0]["video_id"], str)
    assert "503" in finished["degraded"][0]["error"]

    # The item the stage table is about: running first, else the last to
    # finish. A running job is on its running item.
    assert running["focus"]["state"] == "running"
    assert running["focus"]["stage"] == "stt"
    stages = {row["stage"]: row for row in running["stages"]}
    # All seven, in pipeline order, `absent` where no row exists yet.
    assert len(running["stages"]) == len(stages) == 7
    assert stages["fetch"]["state"] in {"done", "absent"}
    assert any(row["state"] == "absent" for row in running["stages"])
    assert set(running["stages"][0]) == {
        "stage",
        "state",
        "started_at",
        "finished_at",
        "took_s",
    }


def test_a_job_with_nothing_in_focus_sends_the_absence_rather_than_a_shell(
    tmp_path: Path,
) -> None:
    """An item that never resolved to a video has no stages to show.

    `video_stages` is keyed on a video, so the deferred job — one item, never
    fetched — has nothing to be in focus. `focus` is `null`, which is the field
    the panel is keyed on: `stages` is still the seven pipeline rows and every
    one of them is `absent`, because the stage list is the pipeline's shape and
    not a claim about this job.
    """
    with make_client(tmp_path) as client:
        payload = read(client, f"{ROOT}/api/jobs/job_deferred01")

    assert payload["focus"] is None
    assert {row["state"] for row in payload["stages"]} == {"absent"}
    assert payload["counts"] == {"queued": 1}
    assert payload["error_counts"] == {}
    assert payload["degraded"] == []


def test_the_job_detail_projection_drops_the_operators_prose(tmp_path: Path) -> None:
    """§2.4 on the six new fields, which is where a stage error would have got
    out: the degraded list quotes the pipeline quoting the worker, and the
    stage table would have carried a declared model id if it read one.

    It reads neither. The clocks survive — what indexing a video costs is the
    demo's business (§10.4) — and so do the codes and the counts.
    """
    with make_client(tmp_path, public=DEMO) as demo:
        finished = read(demo, JOB)
        running = read(demo, RUNNING)

    assert finished["degraded"][0]["stage"] == "ocr"
    assert finished["degraded"][0]["error"] is None
    assert finished["error_counts"] == {"E_SOURCE": 1}
    assert running["focus"]["source_url"] is None
    assert running["focus"]["error_message"] is None
    for row in running["stages"]:
        assert set(row) == {"stage", "state", "started_at", "finished_at", "took_s"}
    raw = json.dumps([finished, running])
    for leaked in (
        "yt-dlp-2026.07.04",
        "Qwen/Qwen3-VL-Embedding-2B",
        "worker returned 503",
        "Sign in to confirm",
        "youtu.be/failedvideo",
        str(tmp_path),
    ):
        assert leaked not in raw, f"{leaked} is in the demo payload"


def test_the_job_details_new_fields_carry_no_rendered_clock(tmp_path: Path) -> None:
    """The typed rule again, on the six fields added on 2026-09-05.

    `focus` is a job item and job items carry the transitional `text` block, so
    the scan is over the typed half — the half a React page reads. What it must
    not find is a stamp or a spoken duration that only Python could have built.
    """
    with make_client(tmp_path) as client:
        for path in (JOB, RUNNING):
            payload = read(client, path)
            grown = json.dumps(
                _typed(
                    {
                        key: payload[key]
                        for key in (
                            "items_capped",
                            "counts",
                            "error_counts",
                            "degraded",
                            "focus",
                            "stages",
                        )
                    }
                )
            )
            assert not ISO_STAMP.search(grown), f"a rendered date reached {path}"
            assert not SPOKEN_DURATION.search(grown), f"a rendered duration reached {path}"
            assert not re.search(r'"\d+:\d{2}(?::\d{2})?"', grown), path


# ------------------------------------ what only the Jinja pages used to pin
#
# The markup suite went with the pages it read (2026-09-06). Six things it was
# the only witness to are here instead, because none of them is a fact about
# HTML: the §6.3 read-count shape, the OCR double cap, the history cap, the
# event tail, the two time axes over real rows, and the relative frame URL.


def _count_reads(client: TestClient, path: str, **kwargs) -> int:
    """How many database reads one request costs.

    §6.3's rule is a *shape*, not a threshold: one row and a hundred must cost
    the same number of reads, and the only way to say that is to count them.
    """
    db = client.app.state.assembled.db
    original = db.read
    calls = 0

    async def spy(fn, budget_s=None):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return await original(fn, budget_s)

    db.read = spy  # type: ignore[method-assign]
    try:
        assert client.get(path, **kwargs).status_code == 200, path
    finally:
        db.read = original  # type: ignore[method-assign]
    return calls


def test_no_read_on_this_surface_issues_a_query_per_row(tmp_path: Path) -> None:
    """§6.3, measured — and measured on the payloads now, not on the pages.

    Every one of these was the page's assertion first, because the page was the
    only caller. The assembly is shared, so the count is the same count; what
    changes is which handler is asked for it.
    """
    with make_client(tmp_path) as client:
        # The table: fifty rows must not become two hundred coverage probes.
        one = _count_reads(client, f"{LIBRARY}?limit=1")
        many = _count_reads(client, f"{LIBRARY}?limit=100")
        assert one == many < 10, f"{one} reads for 1 row, {many} for 100"

        # The detail: one keyframe and ninety-six cost the same.
        small = _count_reads(client, f"{LIBRARY}/{FIRST}?frames=1")
        large = _count_reads(client, f"{LIBRARY}/{FIRST}?frames=96")
        assert small == large < 20, f"{small} reads for 1 frame, {large} for 96"

        # The cue pager, which is the one read a scrollbox repeats.
        cues = f"{ROOT}/api/videos/{FIRST}/cues"
        assert _count_reads(client, f"{cues}?limit=1") == _count_reads(
            client, f"{cues}?limit=200"
        )

        # The jobs table: the degraded badge and the row headline are one
        # grouped read over the ids the page read, not a probe per job.
        jobs_one = _count_reads(client, f"{JOBS}?limit=1")
        jobs_many = _count_reads(client, f"{JOBS}?limit=100")
        assert jobs_one == jobs_many <= 4, f"{jobs_one} for 1 job, {jobs_many} for 100"
        assert _count_reads(client, JOB) <= 8

        # The ledger is a page of aggregates, so its cost is a constant: every
        # figure is a whole-table or index count and none is a probe per video.
        assert _count_reads(client, LEDGER) <= 8


DENSE_SLIDE_LINES = 30


def _dense_corpus(tmp_path: Path) -> Path:
    """The fixture corpus with one keyframe carrying a slide's worth of text."""
    data = corpus_dir(tmp_path)
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


def test_a_dense_slide_sends_every_line_it_read_with_a_box_for_each(
    tmp_path: Path,
) -> None:
    """The OCR panel is a scrollbox of every line, and the linkage is by index.

    The page's version of this counted `<li class="ocrline">`s against
    `<span class="ocrbox">`es. The fact underneath is the payload's: a line and
    its box arrive together, in the frame's own order, so a client can pair
    them by position without a second read — and the *page's* budget, the outer
    half of §5.3's double cap, is a field rather than a silence.
    """
    _dense_corpus(tmp_path)
    with make_client(tmp_path) as client:
        body = read(client, f"{LIBRARY}/{FIRST}?frames=96")

    frames = {frame["frame_id"]: frame for frame in body["frames"]["frames"]}
    dense = frames[f"{FIRST}-00000"]
    # Every line the read returned, in one list — the slide plus the fixture's
    # own line — each with a box beside it and none of them dropped.
    assert len(dense["lines"]) == DENSE_SLIDE_LINES + 1
    assert all(len(line["box"]) == 4 for line in dense["lines"])
    assert [line["line_no"] for line in dense["lines"]] == list(
        range(len(dense["lines"]))
    )
    # The budget is on the payload whether or not it bound, so a client never
    # has to guess whether a short list is the whole list.
    assert body["frames"]["ocr_line_cap"] == OCR_LINE_CAP
    assert body["frames"]["ocr_lines_capped"] is False


def test_the_indexing_history_is_the_latest_ten_and_says_what_went_wrong(
    tmp_path: Path,
) -> None:
    """§16.4: bounded, newest first, never counted — and the table above it is
    unchanged, because history is a detail-only read."""
    with make_client(tmp_path) as client:
        conn = open_write_connection(client.app.state.assembled.db.path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            video = int(
                conn.execute(
                    f"SELECT id FROM videos WHERE public_id='{FIRST}'"
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
                    f"finished_at) VALUES (?, 0, 'https://youtu.be/{FIRST}', ?, "
                    "'done', unixepoch() + ?)",
                    (int(cursor.lastrowid), video, n + 1),
                )
            conn.execute("COMMIT")
        finally:
            conn.close()

        body = read(client, f"{LIBRARY}/{FIRST}")
        ids = [job["job_id"] for job in body["job_history"]["jobs"]]
        assert len(ids) == body["job_history"]["cap"] == 10
        assert "job_history11" in ids and "job_history02" in ids
        assert "job_history01" not in ids  # the eleventh run, dropped by the cap
        assert "total" not in body["job_history"]

        # The listing did not grow a per-row history query on the way.
        assert _count_reads(client, f"{LIBRARY}?limit=1") == _count_reads(
            client, f"{LIBRARY}?limit=100"
        )

        # A run that failed carries the code it failed with and the stage that
        # went missing under a job that reported success.
        degraded = read(client, f"{LIBRARY}/eMlx5fFNoYc")["job_history"]["jobs"]
        entry = next(job for job in degraded if job["job_id"] == "job_finished01")
        assert entry["state"] == "failed" and entry["kind"] == "index"
        assert entry["degraded_stages"] == ["ocr"]


def test_the_event_tail_is_newest_first_and_bounded(tmp_path: Path) -> None:
    """The other unbounded block on the war-story page.

    An overnight batch writes eighty events and the payload carries
    `EVENT_CAP` of them — the **newest**, in the order they happened, so the
    half that is dropped is the half that has scrolled out of relevance. The
    page split them into a preview and a drawer; that split was the page's, and
    what survives it is the cap and which end of the log it keeps.
    """
    data = corpus_dir(tmp_path)
    conn = open_write_connection(data / "vidtheque.db")
    try:
        job = int(
            conn.execute(
                "SELECT id FROM jobs WHERE public_id='job_deferred01'"
            ).fetchone()[0]
        )
        conn.execute("BEGIN IMMEDIATE")
        for n in range(EVENT_CAP + 20):
            conn.execute(
                "INSERT INTO job_events (job_id, at, level, message) VALUES "
                "(?, unixepoch() - ?, 'info', ?)",
                (job, EVENT_CAP + 120 - n, f"stage keyframe: decoded {n * 250} frames"),
            )
        conn.execute("COMMIT")
    finally:
        conn.close()

    with make_client(tmp_path) as client:
        body = read(client, f"{ROOT}/api/jobs/job_deferred01")

    events = body["events"]
    assert len(events) == EVENT_CAP
    stamps = [event["at"] for event in events]
    assert stamps == sorted(stamps, reverse=True), "newest first"
    # The cap took the near end, not the far one: the most recent event is in
    # the payload and the oldest of the eighty is not.
    messages = [event["message"] for event in events]
    assert f"stage keyframe: decoded {(EVENT_CAP + 19) * 250} frames" in messages
    assert "stage keyframe: decoded 0 frames" not in messages
    # The deferral's own receipt is in the tail rather than described: it is
    # the only place a non-rate-limit deferral exists at all (§4.4).
    assert body["job"]["error_code"] == "E_RATE_LIMIT"


def test_the_two_time_axes_pick_different_things(tmp_path: Path) -> None:
    """CLAUDE.md's invariant, over rows rather than over parameters.

    `published_*` picks videos and `indexed_*` picks when this box did the
    work. The fixture publishes across 2023–2025 and indexed its three ready
    videos at one moment, so a filter that confused the two is visible here.
    """
    with make_client(tmp_path) as client:

        def ids(query: str) -> set[str]:
            return {row["video_id"] for row in read(client, f"{LIBRARY}?{query}")["videos"]}

        assert FIRST not in ids("published_after=2024-01-01")  # published 2023-01-17
        assert {"zduSFxRajkE", "eMlx5fFNoYc"} <= ids("published_after=2024-01-01")

        # The named day is *included*: `< before` is the clause, so `before`
        # resolves to the start of the next day. A range that dropped
        # everything published on its own end date would read as a bug.
        on_the_day = ids("published_before=2024-02-20")
        assert {"zduSFxRajkE", FIRST} <= on_the_day and "eMlx5fFNoYc" not in on_the_day

        # The other axis — and the video that never finished has no
        # `indexed_at` at all, so it is not caught by it.
        indexed = ids("indexed_after=2025-06-01")
        assert FIRST in indexed and HALF not in indexed
        assert HALF in ids("index_state=indexing")


def test_a_generous_date_spelling_resolves_to_a_day_and_says_which(
    tmp_path: Path,
) -> None:
    """`today` and `30d` are inputs a human types into a URL; the payload
    answers with the UTC day they became.

    The entry point stays generous and the canonical form is the resolved day,
    which is what the query actually filtered on — and a spelling that landed
    somewhere other than the day it named says so, because applying one
    silently is the narrowing CLAUDE.md forbids.
    """
    with make_client(tmp_path) as client:
        now = int(time.time())
        start_of_today = now - now % 86_400
        # A day-shaped word resolves to that day exactly, so there is nothing
        # to disclose and nothing is said.
        relative = read(client, f"{LIBRARY}?indexed_after=today")
        assert relative["filters"]["indexed_after"] == start_of_today
        assert relative["notes"] == []

        # An instant-shaped one lands mid-day and is snapped down to it, which
        # is a different question from the one asked and says so.
        ago = read(client, f"{LIBRARY}?published_after=30d ago")
        day = iso_day(now - 30 * 86_400)
        assert ago["filters"]["published_after"] % 86_400 == 0
        assert f"published_after=30d ago → {day}" in " ".join(ago["notes"])

        # An overlong value is truncated before it reaches the parser rather
        # than handed to it whole.
        assert client.get(f"{LIBRARY}?published_after={'9' * 400}").status_code in (
            200,
            400,
        )


def test_a_dashboard_frame_url_is_relative_and_still_signed(tmp_path: Path) -> None:
    """A page knows its own host better than `PUBLIC_URL` does.

    A preview on a tunnelled port rendered every thumbnail against a dead
    origin, so this surface hands back a path. The signer covers the frame, the
    width, the quality and the expiry and never the origin, so dropping the
    origin cannot invalidate anything — and the facade, whose reader is an
    agent with no page to resolve against, still sends absolute URLs.
    """
    base = "http://localhost:8080"
    with make_client(tmp_path, auth_mode="token", token=TOKEN) as client:
        table = read(client, LIBRARY, headers=BEARER)
        thumbs = [row["thumb"] for row in table["videos"] if row["thumb"]]
        assert thumbs and all(thumb.startswith("/frames/") for thumb in thumbs)

        # No bearer, no cookie: the signature on the relative URL is the whole
        # credential, exactly as it is on an absolute one.
        assert "sig=" in thumbs[0] and "exp=" in thumbs[0]
        assert client.get(thumbs[0]).status_code == 200
        assert client.get(thumbs[0].split("&sig=")[0] + "&sig=forged").status_code == 401

        # The facade at the same prefix is unchanged.
        facade = read(client, FACADE, headers=BEARER)
        absolute = [row["thumb"] for row in facade["videos"] if row["thumb"]]
        assert absolute and all(url.startswith(f"{base}/frames/") for url in absolute)
