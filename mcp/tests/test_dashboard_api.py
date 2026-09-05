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
from starlette.testclient import TestClient

from vidtheque_mcp.auth.login import SESSION_COOKIE
from vidtheque_mcp.dashboard import ROOT
from vidtheque_mcp.dashboard.read_models import (
    CHANNEL_CAP,
    FAILED_WINDOW_S,
    RECENT_CAP,
    TAG_CAP,
)
from vidtheque_mcp.dashboard.settings import DashboardSettings
from vidtheque_mcp.text import clock

from .test_dashboard import (
    BEARER,
    DEMO,
    PASSWORD,
    TOKEN,
    make_client,
    owner_client,
)

OVERVIEW = f"{ROOT}/api/overview"
LEDGER = f"{ROOT}/api/ledger"
SESSION = f"{ROOT}/api/session"
# The videos table and the video detail page (§20). `library`, not `videos`:
# `/dashboard/api/videos` is the facade's listing at this prefix and stays it.
LIBRARY = f"{ROOT}/api/library"
FACADE = f"{ROOT}/api/videos"
# The half-indexed video: two stage rows, one of them failed with yt-dlp's
# prose in it, which is what the detail projection has to lose.
HALF = "aaaaaaaaaaa"
FIRST = "kCc8FmEb1nY"

# A drift reason is a config/dimension mismatch written for the operator, and
# it names what the worker is serving. The *effect* it caused is the visitor's
# business; this sentence is not.
PRIVATE_REASON = "the worker is serving 'other' but the corpus used 'qwen'."

# What a formatted clock looks like on the way out: `iso_z`/`iso_minute`'s
# stamps, and `render.py`'s spoken durations and relative ages.
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

    Tom, 2026-09-05: typed on the wire, React formats. The pages format the
    readiness stamp with `iso_z` for a `<time datetime=…>` attribute, and the
    JSON reads the same observation — so a payload that forwarded the page's
    dict would ship a rendered day under a rule that says it must not. Nothing
    on either surface prints "4m 12s" or "3 hours ago" either; those are the
    strings `render.py` builds for Jinja and nothing else.
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
        # Not `read()`: the facade's handlers answer without `no-store`, which
        # is their own contract and not this slice's to change.
        facade = client.get(FACADE).json()
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

        # A number that was inside the bounds says nothing at all.
        assert read(client, f"{LIBRARY}?limit=2")["notes"] == []


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

    `at`, `conf` and `chunk` are renderings of numbers `views._cue_rows` already
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
