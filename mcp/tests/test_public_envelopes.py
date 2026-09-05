"""The `/api/*` envelopes nobody had asserted (roadmap item 9).

Listed as untested in `research/website-test-2026-08-09.md` and never turned
into tests: what a bad parameter answers, what a wrong method answers, what a
traversal under `/static/` answers, and what a visitor who spent the search
bucket is told on their next page load.

The last one found a real defect. `/api/meta` shares the `search` bucket, so a
rate-limited reload used to boot a page whose meta-derived fields were all
`undefined`. The page moved to `web/` on 2026-09-05 and its half of that test
went with it; what stays here is what Python hands the page — a refusal that
names its bucket and its retry, rather than a JSON body a client mistakes for
an answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vidtheque_mcp.public.settings import PublicSettings

from .test_public import PUBLIC, PUBLIC_WITH_KEY, make_client


@pytest.fixture
def public_client(tmp_path: Path) -> TestClient:
    with make_client(tmp_path, PUBLIC) as client:
        yield client


@pytest.fixture
def private_client(tmp_path: Path) -> TestClient:
    with make_client(tmp_path, PublicSettings(enabled=False)) as client:
        yield client


# ------------------------------------------------------------ bad parameters


def test_a_search_with_no_query_and_no_filter_is_a_typed_400(
    public_client: TestClient,
) -> None:
    response = public_client.get("/api/search")
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "E_EMPTY_QUERY"
    assert body["next"]


def test_an_unknown_content_type_names_the_ones_that_exist(
    public_client: TestClient,
) -> None:
    response = public_client.get("/api/search?q=cache&content_type=bogus")
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "E_BAD_PARAM"
    assert "all, transcript, ocr, frame" in body["message"]


def test_a_non_numeric_limit_falls_back_to_the_default_rather_than_erroring(
    public_client: TestClient,
) -> None:
    """Same rule the frame route's `_clamp` uses: an unparseable number is the
    absence of one, not a request to refuse. What matters is that it neither
    500s nor silently returns an unbounded page."""
    response = public_client.get("/api/search?q=cache&limit=abc")
    assert response.status_code == 200
    assert response.json()["results"]


def test_a_non_numeric_offset_does_the_same(public_client: TestClient) -> None:
    response = public_client.get("/api/search?q=cache&offset=xyz")
    assert response.status_code == 200
    assert response.json()["results"]


# ----------------------------------------------------------------- traversal


def test_no_path_under_static_reaches_the_filesystem(public_client: TestClient) -> None:
    """There is no `/static/` route to walk out of since 2026-09-05.

    It used to be a real asset handler that resolved a path parameter, and the
    traversal spellings below were the check that its containment held. The
    handler moved to the Next.js app with the two pages it served, so these are
    now 404 by absence rather than by refusal — kept because the *answer* a
    prober gets is the property, and it did not change.
    """
    assert public_client.get("/static/../../etc/passwd").status_code == 404
    assert public_client.get("/static/..%2f..%2fetc/passwd").status_code == 404
    assert public_client.get("/static/demo/app.js").status_code == 404


# -------------------------------------------------------------- wrong method


def test_a_wrong_method_is_a_404_because_the_mcp_mount_outranks_the_405(
    tmp_path: Path,
) -> None:
    """Documented, not endorsed.

    `Mount("/", app=mcp_app)` is last in the route list and matches everything.
    Starlette treats a path-match-method-mismatch as a *partial* match and
    prefers any later full match, so the mount answers first and every wrong
    method on our own routes reads `404 Not Found` rather than
    `405 Method Not Allowed`. `POST /api/search` and `GET /api/ask` are the two
    a client is most likely to try.
    """
    with make_client(tmp_path, PUBLIC_WITH_KEY) as client:
        assert client.get("/api/meta").json()["ask_enabled"] is True
        assert client.post("/api/ask", json={}).status_code == 400  # the route is there
        assert client.get("/api/ask").status_code == 404  # ...but GET is not a 405
        assert client.post("/api/search?q=cache").status_code == 404


# ------------------------------------------------------------------ the 429s


def test_meta_and_videos_share_the_search_bucket(tmp_path: Path) -> None:
    """Not a defect on its own — one visitor, one budget across the read API."""
    tight = PublicSettings(enabled=True, search_per_min=1)
    with make_client(tmp_path, tight) as client:
        assert client.get("/api/search?q=cache").status_code == 200
        assert client.get("/api/search?q=cache").status_code == 429
        assert client.get("/api/meta").status_code == 429
        assert client.get("/api/videos").status_code == 429


def test_a_refused_boot_call_says_which_bucket_and_for_how_long(
    tmp_path: Path,
) -> None:
    """The Python half of the boot defect, which is the half that is still here.

    `/api/meta` shares the `search` bucket, so a visitor who reloads after
    searching gets a 429 on the one call the page boots from. The defect was
    that the refusal is *JSON* — `.json()` resolved, the client's catch never
    fired, and every meta-derived field came back `undefined`. What Python owes
    a client is therefore a refusal it can tell apart from an answer: the
    status, the named bucket, and a `Retry-After` it can put on the screen.
    The page that has to act on it is `web/`'s now; this pins what it is given.
    """
    tight = PublicSettings(enabled=True, search_per_min=1)
    with make_client(tmp_path, tight) as client:
        assert client.get("/api/search?q=cache").status_code == 200
        refused = client.get("/api/meta")
    assert refused.status_code == 429
    assert refused.json()["bucket"] == "search"
    assert int(refused.headers["retry-after"]) >= 1


def test_a_spent_frame_bucket_refuses_the_image_and_nothing_else(
    tmp_path: Path,
) -> None:
    """One bucket per surface: thumbnails cannot starve the JSON the page reads."""
    tight = PublicSettings(enabled=True, frames_per_min=1)
    with make_client(tmp_path, tight) as client:
        assert client.get("/frames/kCc8FmEb1nY-00000.jpg").status_code == 200
        assert client.get("/frames/kCc8FmEb1nY-00000.jpg").status_code == 429
        assert client.get("/api/meta").status_code == 200


def test_the_private_deployment_registers_no_public_api(
    private_client: TestClient,
) -> None:
    assert private_client.get("/api/meta").status_code == 404
    assert private_client.get("/api/search?q=cache").status_code == 404

