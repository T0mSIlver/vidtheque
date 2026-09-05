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
