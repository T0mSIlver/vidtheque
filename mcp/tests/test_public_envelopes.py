"""The `/api/*` envelopes nobody had asserted (roadmap item 9).

Listed as untested in `research/website-test-2026-08-09.md` and never turned
into tests: what a bad parameter answers, what a wrong method answers, what
`/static/` does with a traversal, and what a visitor who spent the search
bucket sees on their next page load.

The last one found a real defect. `/api/meta` shares the `search` bucket, so a
rate-limited reload used to boot a page whose meta-derived fields were all
`undefined` — see `test_the_page_boot_survives_a_spent_search_bucket`.
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


def test_static_refuses_to_walk_out_of_its_directory(public_client: TestClient) -> None:
    assert public_client.get("/static/../../etc/passwd").status_code == 404
    assert public_client.get("/static/..%2f..%2fetc/passwd").status_code == 404


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


def test_the_page_boot_survives_a_spent_search_bucket(tmp_path: Path) -> None:
    """The document still serves while its bootstrap call is refused.

    That combination is what made the defect invisible: the HTML is a static
    asset on no bucket, so the page loads, and only the `/api/meta` fetch is
    refused. A 429 carries a JSON error body, so `.json()` resolved and every
    field the boot path read came back `undefined` — the MCP line rendered the
    string "undefined" and the ask switch hid itself as though the deployment
    had no key. `app.js` now throws on any non-2xx so the existing failure path
    runs, and says "rate limited" rather than "could not reach the server".
    """
    tight = PublicSettings(enabled=True, search_per_min=1)
    with make_client(tmp_path, tight) as client:
        assert client.get("/api/search?q=cache").status_code == 200
        assert client.get("/api/search?q=cache").status_code == 429
        page = client.get("/demo")
        assert page.status_code == 200
        assert client.get("/api/meta").status_code == 429

    app_js = (
        Path(__file__).resolve().parents[1]
        / "src/vidtheque_mcp/public/static/demo/app.js"
    ).read_text()
    assert "if (!response.ok) throw new Error(String(response.status));" in app_js
    assert "too many requests — this page loads again in a minute" in app_js


def test_a_spent_frame_bucket_refuses_the_image_not_the_page(tmp_path: Path) -> None:
    tight = PublicSettings(enabled=True, frames_per_min=1)
    with make_client(tmp_path, tight) as client:
        assert client.get("/frames/kCc8FmEb1nY-00000.jpg").status_code == 200
        assert client.get("/frames/kCc8FmEb1nY-00000.jpg").status_code == 429
        assert client.get("/demo").status_code == 200


def test_the_demo_page_itself_is_on_no_bucket(tmp_path: Path) -> None:
    """A static document must not be spendable, or a reload is a denial of service."""
    tight = PublicSettings(enabled=True, search_per_min=1, frames_per_min=1)
    with make_client(tmp_path, tight) as client:
        for _ in range(5):
            assert client.get("/demo").status_code == 200
            assert client.get("/").status_code == 200


def test_the_private_deployment_registers_no_public_api(
    private_client: TestClient,
) -> None:
    assert private_client.get("/api/meta").status_code == 404
    assert private_client.get("/api/search?q=cache").status_code == 404

