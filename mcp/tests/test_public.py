"""The public demo surface: masking, the `/api` facade, limits, the page.

Nothing here reaches the network. OpenRouter is faked at the same seam the
worker is — an injected client, here over ``httpx2.MockTransport`` — so the ask
loop is exercised end to end (tool calls, evidence, citations, degradation)
without a key, a model, or a request leaving the process.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import aclosing
from pathlib import Path
from typing import Any, Callable

import httpx2 as httpx
import pytest
from starlette.testclient import TestClient

import vidtheque_mcp.public
from vidtheque_mcp.app import build_app
from vidtheque_mcp.config import Settings
from vidtheque_mcp.public import ratelimit
from vidtheque_mcp.public.ratelimit import Bucket, RateLimiter, client_key
from vidtheque_mcp.public.readonly import WRITE_TOOLS, hidden_tools
from vidtheque_mcp.public.settings import PublicSettings

from .conftest import FakeEmbeddings, rpc, rpc_headers, seed

READ_TOOLS = {
    "search",
    "list-videos",
    "corpus-summary",
    "video-summary",
    "get-segment-context",
    "get-frames",
    "job-status",
}


# --------------------------------------------------------------------- setup


def _settings(tmp_path: Path, fresh: bool = True) -> Settings:
    data = tmp_path / "data"
    if fresh:
        (data / "keyframes").mkdir(parents=True)
        seed(data / "vidtheque.db", data / "keyframes")
    return Settings(
        data_dir=data,
        public_url="http://localhost:8080",
        worker_url="http://worker:8081",
        auth_mode="none",
        secret="test-secret",
        # See mcp/tests/conftest.py: the shipped relevance floors are
        # deliberately open pending recalibration, and the fixture's
        # stand-in vectors have no geometry to calibrate against.
        vec_max_distance=0.72,
        frame_max_distance=0.96,
    )


def make_client(
    tmp_path: Path,
    public: PublicSettings,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    fresh: bool = True,
) -> TestClient:
    """`fresh=False` reopens the data directory a previous client left behind —
    a restart, which is the only way to observe what the process wrote down."""
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler)) if handler else None
    app = build_app(
        _settings(tmp_path, fresh),
        embeddings=FakeEmbeddings(),
        run_pipeline=False,
        public=public,
        public_http=http,
    )
    return TestClient(app, base_url="http://localhost:8080")


PUBLIC = PublicSettings(enabled=True)
PUBLIC_WITH_KEY = PublicSettings(enabled=True, openrouter_key="sk-or-test")


@pytest.fixture
def public_client(tmp_path: Path) -> TestClient:
    with make_client(tmp_path, PUBLIC) as client:
        yield client


@pytest.fixture
def private_client(tmp_path: Path) -> TestClient:
    with make_client(tmp_path, PublicSettings(enabled=False)) as client:
        yield client


def call(client: TestClient, method: str, params: dict | None = None) -> dict:
    name = (params or {}).get("name") or (params or {}).get("uri")
    response = client.post(
        "/mcp", json=rpc(method, params), headers=rpc_headers(method, name=name)
    )
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------ 1. read-only masking


def test_write_tools_are_derived_from_the_annotations() -> None:
    """The mask is not a second hand-written list; it follows readOnlyHint."""
    assert WRITE_TOOLS == {"index-video", "tag-video"}
    assert hidden_tools(False) == frozenset()
    assert hidden_tools(True) == WRITE_TOOLS


def test_private_mode_still_lists_all_nine_tools(private_client: TestClient) -> None:
    tools = {t["name"] for t in call(private_client, "tools/list")["result"]["tools"]}
    assert tools == READ_TOOLS | WRITE_TOOLS


def test_public_mode_never_registers_the_write_tools(public_client: TestClient) -> None:
    tools = {t["name"] for t in call(public_client, "tools/list")["result"]["tools"]}
    assert tools == READ_TOOLS
    assert not tools & WRITE_TOOLS


@pytest.mark.parametrize("name", sorted(WRITE_TOOLS))
def test_calling_a_masked_write_tool_is_unknown_not_refused(
    public_client: TestClient, name: str
) -> None:
    """Absent, not present-and-erroring: the model must read "no such tool"."""
    arguments = {"url": "https://youtu.be/x"} if name == "index-video" else {"video_id": "x"}
    result = call(public_client, "tools/call", {"name": name, "arguments": arguments})["result"]
    assert result["isError"] is True
    assert result["content"][0]["text"] == f"Unknown tool: {name}"
    # The SDK's unknown-tool error, not one of ours: a typed E_* code here would
    # mean the handler ran and refused.
    assert result.get("structuredContent") is None


def test_the_read_surface_is_untouched_in_public_mode(public_client: TestClient) -> None:
    result = call(
        public_client, "tools/call", {"name": "search", "arguments": {"q": "cache", "limit": 3}}
    )["result"]
    assert result["isError"] is False
    assert "https://youtu.be/" in result["content"][0]["text"]
    resources = {
        r["uri"] for r in call(public_client, "resources/list")["result"]["resources"]
    }
    assert resources == {"vidtheque://corpus", "vidtheque://context", "vidtheque://guide"}


# ------------------------------------------------------------ 2. the facade


def test_api_is_absent_outside_public_mode(private_client: TestClient) -> None:
    for path in ("/api/search?q=cache", "/api/videos", "/api/meta"):
        assert private_client.get(path).status_code == 404
    assert private_client.post("/api/ask", json={"q": "x"}).status_code == 404


def test_search_facade_shape(public_client: TestClient) -> None:
    payload = public_client.get("/api/search?q=cache&limit=5").json()
    assert payload["query"] == "cache"
    assert payload["content_type"] == "all"
    assert payload["pagination"]["limit"] == 5
    assert isinstance(payload["notes"], list)
    assert payload["results"], "the seeded corpus should match 'cache'"
    hit = payload["results"][0]
    assert {"video_id", "title", "channel", "start", "text", "link", "timestamp"} <= set(hit)
    assert hit["link"].startswith("https://youtu.be/")
    assert hit["timestamp"].count(":") >= 1


def test_search_facade_emits_thumbnail_urls_for_frame_hits(public_client: TestClient) -> None:
    payload = public_client.get("/api/search?q=nvidia-smi&content_type=ocr").json()
    hits = [h for h in payload["results"] if h["frame_id"]]
    assert hits, "the ocr leg should return frame-backed hits"
    thumb = hits[0]["thumb"]
    assert thumb.startswith("http://localhost:8080/frames/")
    assert thumb.endswith(".jpg?w=192&q=70")  # auth=none: unsigned, and honest about it
    assert public_client.get(thumb).status_code == 200


def test_every_frame_backed_hit_carries_an_enlargeable_url(
    public_client: TestClient,
) -> None:
    """Click-to-enlarge is a second URL, not a second query (§6.4).

    The width is the facade's and the clamp is the route's: a browser cannot
    ask `/frames` for a size of its own, and under `token`/`oauth` it could not
    sign one either.
    """
    payload = public_client.get("/api/search?q=nvidia-smi&content_type=ocr").json()
    hits = [h for h in payload["results"] if h["frame_id"]]
    assert hits, "the ocr leg should return frame-backed hits"
    large = hits[0]["thumb_large"]
    assert large.endswith(".jpg?w=960&q=70")
    assert public_client.get(large).status_code == 200
    # A hit with no keyframe has nothing to enlarge, and says so with a null
    # rather than a URL that 404s inside a dialog.
    empty = [h for h in payload["results"] if not h["frame_id"]]
    assert all(h["thumb_large"] is None for h in empty)


def test_a_frame_hit_asks_for_the_wider_thumbnail(public_client: TestClient) -> None:
    """A frame hit matched on its *image*, so the page shows it bigger (§6.3).

    The width follows the CSS box at 2x, and it is the facade that picks it —
    the browser never gets to ask the route for a size.
    """
    from vidtheque_mcp.public.api import _decorate_hit

    deps = public_client.app.state.assembled.deps
    frame = _decorate_hit(deps, {"source": "frame", "frame_id": "kCc8FmEb1nY-00000"})
    spoken = _decorate_hit(deps, {"source": "transcript", "frame_id": "kCc8FmEb1nY-00000"})
    assert frame["thumb"].endswith("?w=320&q=70")
    assert spoken["thumb"].endswith("?w=192&q=70")
    # Both widths are ones the route already serves, clamp and byte cap intact.
    assert public_client.get(frame["thumb"]).status_code == 200


def test_search_facade_keeps_token_discipline(public_client: TestClient) -> None:
    """The facade's bounds are tighter than the tool's, and server-side."""
    payload = public_client.get("/api/search?q=cache&limit=999&max_text_chars=0").json()
    assert payload["pagination"]["limit"] == 20  # not 50, and not 999
    # There is no full-transcript opt-out on a public endpoint.
    assert all(len(hit["text"]) <= 400 + 80 for hit in payload["results"])


def test_the_public_clamp_policy_is_the_numbers_the_demo_ships() -> None:
    """The demo's bounds are contract-frozen (demo-site.md §2.1, §2.2).

    They moved into a policy object so `/dashboard/api/*` could reuse the same
    handlers (dashboard.md §2.5.1). This asserts the move changed no number:
    the refactor is allowed to add a second policy, never to widen this one.
    """
    from vidtheque_mcp.public.api import PUBLIC_CLAMPS

    assert PUBLIC_CLAMPS.search_max_limit == 20
    assert PUBLIC_CLAMPS.search_default_limit == 10
    assert PUBLIC_CLAMPS.search_text_chars == 400  # forced; no `0` opt-out
    assert PUBLIC_CLAMPS.videos_max_limit == 50
    assert PUBLIC_CLAMPS.videos_default_limit == 24
    assert PUBLIC_CLAMPS.offset_max == 1_000


def test_the_public_meta_names_its_own_policy(public_client: TestClient) -> None:
    clamps = public_client.get("/api/meta").json()["clamps"]
    assert clamps == {"policy": "public", "search_max_limit": 20, "videos_max_limit": 50}


def test_search_facade_maps_typed_errors_onto_status_codes(public_client: TestClient) -> None:
    response = public_client.get("/api/search")  # no q, no filter
    assert response.status_code == 400
    assert response.json()["error"] == "E_EMPTY_QUERY"

    bad = public_client.get("/api/search?q=x&content_type=nonsense")
    assert bad.status_code == 400
    assert bad.json()["error"] == "E_BAD_PARAM"


def test_videos_facade_lists_the_library_with_covers(public_client: TestClient) -> None:
    payload = public_client.get("/api/videos").json()
    ids = {v["video_id"] for v in payload["videos"]}
    assert {"kCc8FmEb1nY", "zduSFxRajkE", "eMlx5fFNoYc"} <= ids
    by_id = {v["video_id"]: v for v in payload["videos"]}
    assert by_id["kCc8FmEb1nY"]["thumb"].endswith("kCc8FmEb1nY-00000.jpg?w=192&q=70")
    # Seeded with no keyframes at all — a cover it does not have is null, not a
    # fabricated URL that 404s in the page.
    assert by_id["eMlx5fFNoYc"]["thumb"] is None
    assert by_id["kCc8FmEb1nY"]["link"] == "https://youtu.be/kCc8FmEb1nY"


def test_meta_reports_the_endpoint_and_the_ask_state(tmp_path: Path) -> None:
    with make_client(tmp_path, PUBLIC) as client:
        payload = client.get("/api/meta").json()
    assert payload["mcp_url"] == "http://localhost:8080/mcp"
    assert payload["auth"] == "none"
    assert payload["ask_enabled"] is False
    assert payload["ask_model"] is None
    assert payload["videos"] == 3
    assert payload["limits"]["ask_per_day"] == 50


# ---------------------------------------------------------------- 3. the page


def test_the_demo_page_is_served_at_the_root(public_client: TestClient) -> None:
    response = public_client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "vidtheque" in body
    assert "/static/app.js" in body
    assert "Add this corpus to your own agent" in body
    assert "Source on GitHub" in body
    assert "Results link to the original talks" not in body


def test_the_enlarge_dialog_is_a_real_dialog(public_client: TestClient) -> None:
    """Esc, the backdrop, the focus trap and the modal role are the platform's."""
    body = public_client.get("/").text
    assert "<dialog id=\"shot\"" in body
    assert 'aria-labelledby="shot-caption"' in body
    # Opening it must not put Enter on "leave the page": Close takes focus.
    assert 'id="shot-close"' in body and "autofocus" in body


def test_the_page_assets_are_served_and_confined(public_client: TestClient) -> None:
    assert public_client.get("/static/style.css").status_code == 200
    assert public_client.get("/static/app.js").status_code == 200
    assert public_client.get("/static/../../config.py").status_code in (404, 400)
    assert public_client.get("/static/nope.css").status_code == 404


def test_the_two_faces_are_served_as_fonts(public_client: TestClient) -> None:
    """A face typed as `text/javascript` loads only until something says nosniff.

    The asset route used to type everything that was not a stylesheet as a
    script, which browsers forgive because they sniff woff2 — right up until
    anything in front of this app sets `X-Content-Type-Options: nosniff`. The
    demo's whole type system rests on these two files (DESIGN.md, Fonts rule 5).
    """
    for name in ("archivo-latin-wght-normal", "jetbrains-mono-latin-wght-normal"):
        response = public_client.get(f"/static/fonts/{name}.woff2")
        assert response.status_code == 200, name
        assert response.headers["content-type"] == "font/woff2"
        assert "immutable" in response.headers.get("cache-control", "")
    # The licence texts beside them are not assets and are not served as one.
    assert public_client.get("/static/fonts/Archivo-OFL.txt").status_code == 404


def test_the_root_is_not_the_page_outside_public_mode(private_client: TestClient) -> None:
    """`/` falls through to the MCP mount, which has nothing there."""
    assert private_client.get("/").status_code == 404
    assert private_client.get("/static/style.css").status_code == 404


# --------------------------------------------------------- 4. rate limiting


def test_bucket_math_refills_continuously() -> None:
    bucket = Bucket(capacity=3, window_s=60.0)
    now = 1000.0
    assert bucket.take(now) == 0.0
    assert bucket.take(now) == 0.0
    assert bucket.take(now) == 0.0
    wait = bucket.take(now)
    assert wait > 0, "an empty bucket refuses"
    assert wait == pytest.approx(20.0, abs=0.01)  # 3/min -> one token per 20s
    assert bucket.take(now + 20.0) == 0.0
    # Capacity is the ceiling: idling for an hour does not bank 60 tokens.
    bucket.take(now + 100_000)
    assert bucket.tokens <= 3


def test_limiter_keys_on_bucket_and_client() -> None:
    limiter = RateLimiter({"search": (1, 60.0)})
    assert limiter.check("search", "1.1.1.1")[0] is True
    assert limiter.check("search", "1.1.1.1")[0] is False
    assert limiter.check("search", "2.2.2.2")[0] is True, "buckets are per client"


def test_limiter_sweeps_full_buckets_first() -> None:
    limiter = RateLimiter({"search": (5, 60.0)}, max_keys=4)
    for i in range(6):
        limiter.check("search", f"ip-{i}")
    assert len(limiter._buckets) <= 4


def test_client_key_prefers_the_trusted_header() -> None:
    scope = {
        "headers": [(b"cf-connecting-ip", b"9.9.9.9, 10.0.0.1")],
        "client": ("127.0.0.1", 5000),
    }
    assert client_key(scope, "CF-Connecting-IP") == "9.9.9.9"
    assert client_key(scope, "") == "127.0.0.1", "empty header name = trust the socket"
    assert client_key({"client": None}, "CF-Connecting-IP") == "unknown"


def test_search_is_limited_with_retry_after(tmp_path: Path) -> None:
    tight = PublicSettings(enabled=True, search_per_min=2)
    with make_client(tmp_path, tight) as client:
        assert client.get("/api/search?q=cache").status_code == 200
        assert client.get("/api/search?q=cache").status_code == 200
        refused = client.get("/api/search?q=cache")
    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) >= 1
    assert refused.headers["x-ratelimit-limit"] == "2"
    body = refused.json()
    assert body["error"] == "E_RATE_LIMIT"
    assert body["bucket"] == "search"


def test_frames_have_their_own_looser_bucket(tmp_path: Path) -> None:
    tight = PublicSettings(enabled=True, search_per_min=1, frames_per_min=2)
    with make_client(tmp_path, tight) as client:
        assert client.get("/api/search?q=cache").status_code == 200
        assert client.get("/api/search?q=cache").status_code == 429
        # A spent search bucket does not stop the page loading its thumbnails.
        assert client.get("/frames/kCc8FmEb1nY-00000.jpg").status_code == 200
        assert client.get("/frames/kCc8FmEb1nY-00000.jpg").status_code == 200
        assert client.get("/frames/kCc8FmEb1nY-00000.jpg").status_code == 429


def test_the_mcp_mount_is_never_rate_limited(tmp_path: Path) -> None:
    """The limiter matches two prefixes; the streaming transport is untouched."""
    tight = PublicSettings(enabled=True, search_per_min=1)
    with make_client(tmp_path, tight) as client:
        for _ in range(4):
            result = call(
                client, "tools/call", {"name": "search", "arguments": {"q": "cache"}}
            )["result"]
            assert result["isError"] is False
        assert client.get("/healthz").status_code == 200


def test_nothing_is_limited_outside_public_mode(tmp_path: Path) -> None:
    with make_client(tmp_path, PublicSettings(enabled=False)) as client:
        for _ in range(40):
            assert client.get("/frames/kCc8FmEb1nY-00000.jpg").status_code == 200


# ------------------------------------------------------------- 5. ask mode


def _completion(content: str | None = None, tool_calls: list[dict] | None = None) -> dict:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message, "finish_reason": "stop"}]}


def _tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


class Upstream:
    """A scripted OpenRouter. Records every request body it was sent."""

    def __init__(self, *responses: httpx.Response | dict) -> None:
        self.scripted = list(responses)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer sk-or-test"
        nxt = self.scripted.pop(0) if len(self.scripted) > 1 else self.scripted[0]
        return nxt if isinstance(nxt, httpx.Response) else httpx.Response(200, json=nxt)


def test_ask_is_unavailable_without_a_key(public_client: TestClient) -> None:
    response = public_client.post("/api/ask", json={"q": "what is a kv cache?"})
    assert response.status_code == 503
    body = response.json()
    assert body == {
        "error": "llm_unavailable",
        "reason": "not_configured",
        "message": "LLM mode unavailable — use search.",
        "retry_after_s": None,
    }


def test_ask_runs_the_tool_loop_and_cites_real_results(tmp_path: Path) -> None:
    upstream = Upstream(
        _completion(tool_calls=[_tool_call("c1", "search", {"query": "kv cache"})]),
        _completion(
            "The cache trades memory for time [1]. A fabricated marker [9] is dropped."
        ),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        payload = client.post("/api/ask", json={"q": "what does the kv cache cost?"}).json()

    # Two completions, not three: one that called the tool, one that answered.
    # `Upstream` repeats its last script entry forever, so without this a loop
    # that fires a spurious extra completion — the way the daily budget dies —
    # passes every other assertion here unchanged.
    assert len(upstream.requests) == 2
    assert payload["rounds"] == 1
    assert payload["model"] == "deepseek/deepseek-v4-flash-0731"
    assert "[1]" in payload["answer"]
    assert "[9]" not in payload["answer"], "a citation naming nothing is stripped"
    assert payload["citations"], "the cited hit comes back with its deep link"
    first = payload["citations"][0]
    assert first["n"] == 1
    assert first["link"].startswith("https://youtu.be/")
    assert first["timestamp"]
    # The model saw the search tool's own bounded text, not a transcript dump.
    tool_message = next(
        m for m in upstream.requests[1]["messages"] if m.get("role") == "tool"
    )
    assert tool_message["content"].startswith("6 results for") or tool_message[
        "content"
    ].startswith("1 results for") or "results for" in tool_message["content"]
    assert len(tool_message["content"]) < 4000


def test_the_model_is_told_which_channel_each_hit_came_from(tmp_path: Path) -> None:
    """Provenance in the prose needs provenance in the evidence (§3.2, §3.3)."""
    upstream = Upstream(
        _completion(tool_calls=[_tool_call("c1", "search", {"query": "kv cache"})]),
        _completion("The slide reads it out [1]."),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        client.post("/api/ask", json={"q": "what does the kv cache cost?"})

    tool_message = next(
        m for m in upstream.requests[1]["messages"] if m.get("role") == "tool"
    )
    labels = {"transcript", "ocr", "frame", "transcript+ocr"}
    numbered = [
        line for line in tool_message["content"].splitlines() if line.startswith("[")
    ]
    assert numbered, "the search tool answers with numbered hits"
    for line in numbered:
        assert line.split(" · ")[0].split("] ")[1] in labels, line

    # And the prompt asks for the distinction rather than templating it — the
    # frame rule especially, which is the one that fails silently.
    system = upstream.requests[0]["messages"][0]["content"]
    assert "frame" in system and "never quote text from one" in system


def test_ask_offers_exactly_two_tools(tmp_path: Path) -> None:
    upstream = Upstream(_completion("nothing to cite."))
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        client.post("/api/ask", json={"q": "hello"})
    names = {t["function"]["name"] for t in upstream.requests[0]["tools"]}
    assert names == {"search", "get_segment_context"}
    # An answer on the first completion is one completion, never a second.
    assert len(upstream.requests) == 1


def test_ask_forces_an_answer_when_the_rounds_run_out(tmp_path: Path) -> None:
    """A model still calling tools on the last round gets tools switched off."""
    looping = _completion(tool_calls=[_tool_call("c", "search", {"query": "cache"})])
    upstream = Upstream(looping, looping, httpx.Response(200, json=_completion("Final answer.")))
    settings = PublicSettings(enabled=True, openrouter_key="sk-or-test", ask_max_rounds=2)
    with make_client(tmp_path, settings, upstream) as client:
        payload = client.post("/api/ask", json={"q": "why?"}).json()
    assert payload["answer"] == "Final answer."
    assert payload["rounds"] == 2
    # Two tool rounds and the forced answer — the round cap is a cap on
    # completions, not only on the rounds counter.
    assert len(upstream.requests) == 3
    assert upstream.requests[-1]["tool_choice"] == "none"


def test_ask_drill_down_tool_reads_the_transcript_window(tmp_path: Path) -> None:
    upstream = Upstream(
        _completion(
            tool_calls=[
                _tool_call("c1", "get_segment_context", {"video_id": "kCc8FmEb1nY", "t": 12})
            ]
        ),
        _completion("It says the price is memory."),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        assert client.post("/api/ask", json={"q": "what price?"}).status_code == 200
    assert len(upstream.requests) == 2
    tool_message = next(
        m for m in upstream.requests[1]["messages"] if m.get("role") == "tool"
    )
    assert "TRANSCRIPT" in tool_message["content"]
    assert "kCc8FmEb1nY" in tool_message["content"]


def test_a_failing_internal_tool_is_reported_to_the_model(tmp_path: Path) -> None:
    """A typed tool error is evidence for the model, not a 500 for the visitor."""
    upstream = Upstream(
        _completion(
            tool_calls=[
                _tool_call("c1", "get_segment_context", {"video_id": "nope", "t": 1})
            ]
        ),
        _completion("The corpus does not have that video."),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        payload = client.post("/api/ask", json={"q": "what about nope?"}).json()
    assert payload["answer"] == "The corpus does not have that video."
    assert payload["citations"] == []
    tool_message = next(
        m for m in upstream.requests[1]["messages"] if m.get("role") == "tool"
    )
    assert tool_message["content"].startswith("error: E_UNKNOWN_VIDEO")


def test_an_unknown_tool_name_is_answered_not_crashed(tmp_path: Path) -> None:
    upstream = Upstream(
        _completion(tool_calls=[_tool_call("c1", "delete_everything", {})]),
        _completion("I only have search."),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        assert client.post("/api/ask", json={"q": "drop the corpus"}).status_code == 200
    tool_message = next(
        m for m in upstream.requests[1]["messages"] if m.get("role") == "tool"
    )
    assert "no tool named" in tool_message["content"]


@pytest.mark.parametrize(
    "status,reason",
    [
        (401, "upstream_rejected"),
        (403, "upstream_rejected"),
        (429, "upstream_rate_limited"),
        (500, "upstream_unavailable"),
        (402, "upstream_unavailable"),
    ],
)
def test_ask_degrades_cleanly_on_every_upstream_failure(
    tmp_path: Path, status: int, reason: str
) -> None:
    secret = "quota exceeded for key sk-or-REALKEY, org 12345"
    upstream = Upstream(httpx.Response(status, json={"error": {"message": secret}}))
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        response = client.post("/api/ask", json={"q": "anything"})
    assert response.status_code == 503
    body = response.json()
    assert body["reason"] == reason
    assert body["message"] == "LLM mode unavailable — use search."
    # Neither the upstream body nor the key appears anywhere in the response.
    blob = response.text + str(dict(response.headers))
    assert "sk-or" not in blob
    assert "quota" not in blob
    assert str(status) not in body["message"]


def test_ask_degrades_when_the_upstream_is_unreachable(tmp_path: Path) -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    with make_client(tmp_path, PUBLIC_WITH_KEY, explode) as client:
        response = client.post("/api/ask", json={"q": "anything"})
    assert response.status_code == 503
    assert response.json()["reason"] == "upstream_unavailable"
    assert "no route to host" not in response.text


def test_ask_degrades_on_a_junk_upstream_body(tmp_path: Path) -> None:
    upstream = Upstream(httpx.Response(200, text="<html>maintenance</html>"))
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        response = client.post("/api/ask", json={"q": "anything"})
    assert response.status_code == 503
    assert "maintenance" not in response.text


def test_ask_rejects_a_body_with_no_question(tmp_path: Path) -> None:
    upstream = Upstream(_completion("unused"))
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        response = client.post("/api/ask", json={})
    assert response.status_code == 400
    assert response.json()["error"] == "E_BAD_PARAM"
    assert not upstream.requests, "a bad request never reaches the upstream"


def test_ask_has_a_per_ip_and_a_global_daily_budget(tmp_path: Path) -> None:
    upstream = Upstream(_completion("fine."))
    settings = PublicSettings(
        enabled=True, openrouter_key="sk-or-test", ask_per_min=5, ask_per_day=2
    )
    with make_client(tmp_path, settings, upstream) as client:
        headers_a = {"CF-Connecting-IP": "1.1.1.1"}
        headers_b = {"CF-Connecting-IP": "2.2.2.2"}
        assert client.post("/api/ask", json={"q": "a"}, headers=headers_a).status_code == 200
        assert client.post("/api/ask", json={"q": "b"}, headers=headers_b).status_code == 200
        # A fresh IP does not get a fresh *global* budget.
        spent = client.post("/api/ask", json={"q": "c"}, headers={"CF-Connecting-IP": "3.3.3.3"})
    assert spent.status_code == 429
    assert spent.json()["bucket"] == "ask_global"


def test_the_per_ip_ask_bucket_is_charged_before_the_global_one(tmp_path: Path) -> None:
    upstream = Upstream(_completion("fine."))
    settings = PublicSettings(
        enabled=True, openrouter_key="sk-or-test", ask_per_min=1, ask_per_day=50
    )
    with make_client(tmp_path, settings, upstream) as client:
        headers = {"CF-Connecting-IP": "1.1.1.1"}
        assert client.post("/api/ask", json={"q": "a"}, headers=headers).status_code == 200
        refused = client.post("/api/ask", json={"q": "b"}, headers=headers)
    assert refused.status_code == 429
    assert refused.json()["bucket"] == "ask", "one visitor cannot spend the day's budget"


# ----------------------------------------------- 6. the page as a page, and XSS

STATIC = Path(vidtheque_mcp.public.__file__).parent / "static"


def _page(client: TestClient) -> str:
    response = client.get("/")
    assert response.status_code == 200
    return response.text


def test_the_page_declares_an_identity_worth_unfurling(public_client: TestClient) -> None:
    """Title, description, the OG pair, viewport, favicon, the one scheme."""
    body = _page(public_client)
    assert "<title>vidtheque — AI Engineer 2026, on tap</title>" in body
    assert body.count("The knowledge of AI Engineer 2026, on tap.") == 3
    assert body.count("Your agent watched it") == 3
    assert "talks you have watched" not in body
    assert '<meta name="description"' in body
    assert 'property="og:title"' in body
    assert 'property="og:description"' in body
    assert 'name="viewport"' in body and "width=device-width" in body
    assert 'rel="icon"' in body and "image/svg+xml" in body
    # Dark only since the projection-room rebuild (DESIGN.md, 2026-08-10): one
    # scheme, one `theme-color`, and no `prefers-color-scheme` anywhere — a
    # projection room does not have a day mode, and a second palette left in
    # the page is a palette somebody uses.
    assert body.count('name="theme-color"') == 1
    assert 'name="theme-color" content="#040405"' in body
    assert 'name="color-scheme" content="dark"' in body
    assert "prefers-color-scheme" not in body


def test_the_cold_page_teaches_instead_of_showing_a_blank(public_client: TestClient) -> None:
    """Before the first search there is something to click, and it is copy.

    Five examples, drawn from a verified harvest rather than written from
    memory (demo-site.md §6.1) — `research/demo-queries-2026-08-10.md` since the
    rebuild, where every pair was checked at click level. The count is not the
    contract and this does not pin it; what is asserted is the *shape* the
    wiring depends on — see the next test for the half that can silently rot.
    """
    body = _page(public_client)
    assert body.count('class="example"') >= 3
    assert "context window costs money tokens" in body, "the flagship on-screen example"
    for landmark in ("<header", "<main>", "<footer>", "<h1"):
        assert landmark in body
    assert 'class="sr-only" for="q"' in body, "the search box has a real label"


def test_an_example_that_needs_a_channel_pins_it(public_client: TestClient) -> None:
    """demo-site.md §6.1: `data-type` on an example, honoured by `app.js`.

    The prices in "context window costs money tokens" are in this corpus only as
    text on a slide — the speaker's whole treatment of it names no figure at all
    (`research/demo-queries-2026-08-10.md` §2.1) — so unpinned it is buried by
    the other legs and the flagship demonstration of "we read the screen"
    demonstrates nothing. The pin is copy, in the HTML beside the query it
    belongs to; the *reset* is the half that lives in `app.js`, and without it
    clicking an on-screen example and then a spoken one runs the second query
    against OCR and reports an empty corpus.
    """
    body = _page(public_client)
    for pin in ('data-type="ocr">context window costs money tokens', 'data-type="frame"'):
        assert pin in body

    script = (STATIC / "app.js").read_text()
    assert 'selectContentType(example.dataset.type || "all", false)' in script


def test_the_page_and_its_assets_are_cacheable(public_client: TestClient) -> None:
    for path in ("/", "/static/style.css", "/static/app.js"):
        cache = public_client.get(path).headers.get("cache-control", "")
        assert "max-age=" in cache, f"{path} is served without a cache lifetime"


def test_the_page_builds_no_html_from_data(public_client: TestClient) -> None:
    """The XSS floor, asserted against the source rather than assumed.

    Everything the page renders is either its own copy or corpus text — and
    corpus text includes OCR, which is *whatever happened to be on someone's
    screen*. One rule covers it and is checkable: no HTML sink, anywhere.
    """
    # Comment lines are stripped: the file *talks* about innerHTML to say it
    # never uses one, and that sentence should stay legal.
    script = "\n".join(
        line
        for line in (STATIC / "app.js").read_text().splitlines()
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
        assert sink not in script, f"app.js reaches for {sink}"
    stripped = _page(public_client).replace(
        '<script type="module" src="/static/app.js"></script>', ""
    )
    assert "<script" not in stripped, "no inline script: the page stays CSP-ready"


# A line of OCR is untrusted input by construction. This is the shape of it.
# The padding has to run past the facade's 400-char budget *after* the OCR leg's
# snippet window (64 tokens, index-schema §2.5), which is why it is wordy.
HOSTILE_OCR = (
    "xsspayload <script>alert(document.cookie)</script> "
    '<img src=x onerror=alert(1)> "><svg onload=alert(1)> javascript:alert(1) '
) + (
    "padding_that_keeps_running_past_the_facade_budget so the truncation "
    "marker has something to mark. " * 12
)


def _inject_hostile_ocr(tmp_path: Path) -> None:
    """Put an adversarial OCR line in the corpus, the way a slide would.

    Both tables, because a slide is one searchable document (`ocr_frames`) made
    of lines (`ocr_lines`) — the upsert here also exercises the frame index's
    UPDATE trigger.
    """
    conn = sqlite3.connect(tmp_path / "data" / "vidtheque.db")
    try:
        row = conn.execute("SELECT id, video_id, t_s FROM keyframes LIMIT 1").fetchone()
        conn.execute(
            "INSERT INTO ocr_lines (keyframe_id, video_id, t_s, line_no, text, conf, "
            "x0, y0, x1, y1) VALUES (?, ?, ?, 9, ?, 0.9, 0, 0, 1, 1)",
            (row[0], row[1], row[2], HOSTILE_OCR),
        )
        conn.execute(
            "INSERT INTO ocr_frames (keyframe_id, video_id, t_s, text) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(keyframe_id) DO UPDATE SET text = text || ' | ' || excluded.text",
            (row[0], row[1], row[2], HOSTILE_OCR),
        )
        conn.commit()
    finally:
        conn.close()


def test_hostile_ocr_text_comes_back_as_data_never_as_markup(tmp_path: Path) -> None:
    with make_client(tmp_path, PUBLIC) as client:
        _inject_hostile_ocr(tmp_path)
        response = client.get("/api/search?q=xsspayload&content_type=ocr")
    assert response.status_code == 200
    # Half the defence is the content type: nothing sniffs this as HTML.
    assert response.headers["content-type"].startswith("application/json")
    hits = [h for h in response.json()["results"] if "xsspayload" in (h["text"] or "")]
    assert hits, "the hostile line is in the corpus and searchable"
    text = hits[0]["text"]
    # Passed through untouched. The facade does not sanitise — the page never
    # parses it as HTML (test above), which is the defence that actually holds.
    assert "<script>alert(document.cookie)</script>" in text
    # And the tool's character budget still applied to it.
    assert len(text) <= 400 + 80


def test_the_facade_humanises_the_mcp_only_truncation_hint(tmp_path: Path) -> None:
    """`pass max_text_chars=0` is advice no browser can take (demo-site.md §2.4)."""
    with make_client(tmp_path, PUBLIC) as client:
        _inject_hostile_ocr(tmp_path)
        payload = client.get("/api/search?q=xsspayload&content_type=ocr").json()
    cut = [h["text"] for h in payload["results"] if "…" in (h["text"] or "")]
    assert cut, "the padded line is long enough to be truncated"
    assert all("max_text_chars" not in text for text in cut)
    assert all("truncated" not in text for text in cut)


# ------------------------------------------------ 6b. the humanising layer


def test_humanise_replaces_the_marker_with_one_ellipsis() -> None:
    from vidtheque_mcp.public import humanize
    from vidtheque_mcp.text import middle_truncate

    assert humanize.snippet("a plain sentence") == "a plain sentence"
    assert humanize.snippet(None) is None
    assert humanize.snippet("") == ""

    marked = middle_truncate("x" * 200, 40)
    assert "max_text_chars" in marked, "the marker is still the tool's"
    human = humanize.snippet(marked)
    assert "max_text_chars" not in human
    assert human.count("…") == 1, human
    assert human.startswith("x") and human.endswith("x")

    # A cut that lands next to text which already trailed off is one ellipsis
    # to a reader, not two.
    from vidtheque_mcp.text import TRUNCATION_MARKER

    doubled = f"and it trails off …{TRUNCATION_MARKER.format(n=12)}the rest of it"
    assert humanize.snippet(doubled) == "and it trails off …the rest of it"

    # Newlines are a line break to a tool's text block and a run of whitespace
    # to a page that renders one line per moment.
    assert humanize.snippet("two\n  lines") == "two lines"


def test_humanise_drops_the_frame_leg_stand_in_text() -> None:
    """A frame that matched on imagery has no text — and says so with none."""
    from vidtheque_mcp.public import humanize

    assert humanize.snippet(humanize.FRAME_WITHOUT_TEXT, "frame") is None
    # Only for the leg that emits it: an OCR line is a line, whatever it says.
    assert humanize.snippet(humanize.FRAME_WITHOUT_TEXT, "ocr") == humanize.FRAME_WITHOUT_TEXT


def test_the_frame_leg_stand_in_string_is_still_the_one_search_emits() -> None:
    """The one retyped string here, checked against its source (humanize.py).

    `search.py` does not export it, so this is the guard that keeps the two in
    step: the day the leg says something else, this fails instead of the page
    quietly printing a sentence where a picture is the evidence.
    """
    from pathlib import Path as _Path

    from vidtheque_mcp.public import humanize
    from vidtheque_mcp.tools import search as search_tool

    source = _Path(search_tool.__file__).read_text()
    assert humanize.FRAME_WITHOUT_TEXT in source


def test_humanise_clips_a_label_at_the_end() -> None:
    """An activity line names a query or a talk, on one line (§2.4, §3.5)."""
    from vidtheque_mcp.public import humanize

    assert humanize.clip("kv cache", 80) == "kv cache"
    assert humanize.clip("  two\n lines  ", 80) == "two lines"
    assert humanize.clip(None, 10) == ""
    # Cut at the end, not in the middle: the front of a title identifies it.
    clipped = humanize.clip("a" * 40 + " tail", 10)
    assert clipped == "a" * 9 + "…"
    assert len(clipped) == 10
    from vidtheque_mcp.public import humanize

    assert humanize.note("note: the frame leg was skipped.") == (
        "The frame leg was skipped."
    )
    assert humanize.note(None) is None
    assert humanize.note("note:   ") is None
    # A line that is already a sentence keeps its words, whitespace and all
    # collapsed onto one line.
    assert humanize.note("already human.\n  still human.") == (
        "Already human. still human."
    )
    assert humanize.notes(["note: one.", "note:", "note: two."]) == ["One.", "Two."]


def test_the_facade_prints_notes_without_the_agent_prefix(public_client: TestClient) -> None:
    """A `note:` prefix is machinery; the page renders notes in their own line."""
    # No word of this query is in the corpus, which is the note the demo hits
    # most: the vector legs are not queried at all.
    payload = public_client.get("/api/search?q=zzzqqqwww").json()
    assert payload["notes"], "this query should skip the semantic legs, and say so"
    for line in payload["notes"]:
        assert not line.lower().startswith("note:")
        assert line[0].isupper(), line


def test_ask_citations_carry_the_evidence_the_model_was_shown(tmp_path: Path) -> None:
    """The answer's source rows read like search rows, from the same hits."""
    upstream = Upstream(
        _completion(tool_calls=[_tool_call("c1", "search", {"query": "kv cache"})]),
        _completion("The cache trades memory for time [1]."),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        payload = client.post("/api/ask", json={"q": "what does the kv cache cost?"}).json()
    first = payload["citations"][0]
    assert first["text"], "a citation without its snippet is a bare title"
    assert first["source"] in {"transcript", "ocr", "frame", "transcript+ocr"}
    assert "max_text_chars" not in first["text"]
    # A source row is a search row, so it enlarges like one — including the
    # null when the cited moment has no keyframe behind it.
    assert "thumb_large" in first
    frames = [c for c in payload["citations"] if c["thumb"]]
    assert all(c["thumb_large"].endswith("?w=960&q=70") for c in frames)


# ------------------------------- 7. the seams a launch day actually finds


class Flapping:
    """An upstream that fails `fails` times and then works.

    The launch-day shape: a free/cheap tier that 5xxs for a few minutes while
    visitors keep pressing the button, then comes back.
    """

    def __init__(self, fails: int, then: dict) -> None:
        self.left = fails
        self.then = then
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        if self.left > 0:
            self.left -= 1
            return httpx.Response(503, json={"error": {"message": "upstream is down"}})
        return httpx.Response(200, json=self.then)


def _ask(client: TestClient, ip: str = "1.1.1.1") -> Any:
    return client.post("/api/ask", json={"q": "what?"}, headers={"CF-Connecting-IP": ip})


def test_an_upstream_flap_does_not_spend_the_global_daily_budget(tmp_path: Path) -> None:
    """The failure mode that locks every visitor out, and the reason for the refund.

    The limiter charges before the handler runs, so without a refund one
    visitor retrying through a flap burns the whole day for everybody — and
    each reclaimed token then trickles back over ~28 minutes.
    """
    upstream = Flapping(fails=3, then=_completion("fine."))
    settings = PublicSettings(
        enabled=True, openrouter_key="sk-or-test", ask_per_min=50, ask_per_day=2
    )
    with make_client(tmp_path, settings, upstream) as client:
        for _ in range(3):
            assert _ask(client).status_code == 503
        # The budget is untouched: both of the day's asks are still there.
        assert _ask(client).status_code == 200
        assert _ask(client, "2.2.2.2").status_code == 200
        spent = _ask(client, "3.3.3.3")
    assert spent.status_code == 429
    assert spent.json()["bucket"] == "ask_global", "and the budget is still enforced"


def test_a_request_that_never_reaches_the_model_costs_no_budget(tmp_path: Path) -> None:
    """A malformed body: rejected before the loop, so it spent nothing."""
    settings = PublicSettings(
        enabled=True, openrouter_key="sk-or-test", ask_per_min=50, ask_per_day=1
    )
    upstream = Upstream(_completion("fine."))
    with make_client(tmp_path, settings, upstream) as client:
        assert client.post("/api/ask", json={}).status_code == 400
        assert client.post("/api/ask", json={}).status_code == 400
        assert _ask(client).status_code == 200, "the day's single ask survived them"


def test_an_unconfigured_ask_costs_no_budget_either(tmp_path: Path) -> None:
    """No key at all: the page hides the toggle, but a direct POST still 503s."""
    keyless = PublicSettings(enabled=True, ask_per_min=50, ask_per_day=1)
    with make_client(tmp_path, keyless) as client:
        assert _ask(client).status_code == 503  # not_configured
        assert _ask(client).status_code == 503, "still 503, never 429 on a spent budget"


def test_the_per_ip_bucket_still_throttles_a_retry_storm(tmp_path: Path) -> None:
    """The refund gives back the *cost* control, never the anti-hammer guard."""
    upstream = Flapping(fails=10, then=_completion("unused"))
    settings = PublicSettings(
        enabled=True, openrouter_key="sk-or-test", ask_per_min=2, ask_per_day=50
    )
    with make_client(tmp_path, settings, upstream) as client:
        assert _ask(client).status_code == 503
        assert _ask(client).status_code == 503
        throttled = _ask(client)
    assert throttled.status_code == 429
    assert throttled.json()["bucket"] == "ask"
    assert len(upstream.requests) == 2, "the throttled one never reached the upstream"


def test_a_refused_request_is_not_charged_to_the_buckets_that_let_it_through() -> None:
    limiter = RateLimiter({"ask": (5, 60.0), "ask_global": (0, 86_400.0)})
    assert limiter.check("ask", "1.1.1.1")[0] is True
    assert limiter.check("ask_global", "@global")[0] is False
    limiter.refund("ask", "1.1.1.1")
    assert limiter._buckets[("ask", "1.1.1.1")].tokens == pytest.approx(5.0, abs=0.01)
    # A refund can never mint tokens the bucket never had.
    limiter.refund("ask", "1.1.1.1")
    assert limiter._buckets[("ask", "1.1.1.1")].tokens <= 5.0
    limiter.refund("ask", "9.9.9.9")  # a bucket that does not exist is a no-op


def test_a_stripped_citation_leaves_no_seam_behind(tmp_path: Path) -> None:
    """Finding 4: a fabricated `[n]` goes, and so does the hole it leaves."""
    upstream = Upstream(
        _completion(tool_calls=[_tool_call("c1", "search", {"query": "kv cache"})]),
        _completion("The block table [9] is kept [1]. See [9]. Also [8][7] here."),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        answer = client.post("/api/ask", json={"q": "what?"}).json()["answer"]
    assert answer == "The block table is kept [1]. See. Also here."
    assert "  " not in answer, "a removed marker must not leave a double space"
    assert " ." not in answer, "nor an orphaned full stop"


def test_an_answer_with_only_real_citations_is_passed_through_verbatim(
    tmp_path: Path,
) -> None:
    """The rewrite runs on every answer, so it has to be a no-op on a clean one."""
    prose = "Paged attention [1] keeps a block table.\n\nIt is the trick [1]."
    upstream = Upstream(
        _completion(tool_calls=[_tool_call("c1", "search", {"query": "kv cache"})]),
        _completion(prose),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        answer = client.post("/api/ask", json={"q": "what?"}).json()["answer"]
    assert answer == prose


def test_a_drill_down_window_is_recorded_as_citable_evidence(tmp_path: Path) -> None:
    """Finding 5: an answer built only from `get_segment_context` keeps its sources."""
    upstream = Upstream(
        _completion(
            tool_calls=[
                _tool_call("c1", "get_segment_context", {"video_id": "kCc8FmEb1nY", "t": 12})
            ]
        ),
        _completion("The price you pay is memory [1]."),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        payload = client.post("/api/ask", json={"q": "what price?"}).json()
    assert payload["answer"] == "The price you pay is memory [1]."
    assert payload["citations"], "a drill-down-only answer used to lose every source"
    cited = payload["citations"][0]
    assert cited["n"] == 1
    assert cited["video_id"] == "kCc8FmEb1nY"
    assert cited["link"].startswith("https://youtu.be/kCc8FmEb1nY")
    assert cited["text"], "the window's own words, so the source row is not a bare title"
    # And the model was told which number the window is, or it could not cite it.
    tool_message = next(
        m for m in upstream.requests[1]["messages"] if m.get("role") == "tool"
    )
    assert tool_message["content"].startswith("This window is [1]")


def test_a_drill_down_into_a_known_hit_reuses_that_hit_number(tmp_path: Path) -> None:
    """Dedup on (video, second) holds across both tools, or the `[n]` would drift."""
    upstream = Upstream(
        _completion(tool_calls=[_tool_call("c1", "search", {"query": "block table"})]),
        _completion(
            tool_calls=[
                _tool_call("c2", "get_segment_context", {"video_id": "zduSFxRajkE", "t": 10})
            ]
        ),
        _completion("Paged attention keeps a block table [1]."),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        payload = client.post("/api/ask", json={"q": "block table?"}).json()
    assert [c["n"] for c in payload["citations"]] == [1]
    context_message = [
        m for m in upstream.requests[2]["messages"] if m.get("role") == "tool"
    ][-1]
    assert context_message["content"].startswith("This window is [1]")


def test_tool_calls_without_ids_get_distinct_synthesised_ones(tmp_path: Path) -> None:
    """Finding 7: two id-less `search` calls used to share one `tool_call_id`."""
    idless = {
        "type": "function",
        "function": {"name": "search", "arguments": json.dumps({"query": "cache"})},
    }
    upstream = Upstream(
        _completion(tool_calls=[dict(idless), dict(idless)]),
        _completion("Both searched."),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        assert client.post("/api/ask", json={"q": "cache?"}).status_code == 200
    messages = upstream.requests[1]["messages"]
    turn = next(m for m in messages if m.get("role") == "assistant" and m.get("tool_calls"))
    ids = [c["id"] for c in turn["tool_calls"]]
    assert len(ids) == 2 and len(set(ids)) == 2 and all(ids)
    tool_ids = [m["tool_call_id"] for m in messages if m.get("role") == "tool"]
    assert sorted(tool_ids) == sorted(ids), "each tool message names exactly one call"


def test_duplicate_tool_call_ids_are_made_unique(tmp_path: Path) -> None:
    upstream = Upstream(
        _completion(
            tool_calls=[
                _tool_call("same", "search", {"query": "cache"}),
                _tool_call("same", "search", {"query": "memory"}),
            ]
        ),
        _completion("Done."),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        assert client.post("/api/ask", json={"q": "cache?"}).status_code == 200
    messages = upstream.requests[1]["messages"]
    turn = next(m for m in messages if m.get("role") == "assistant" and m.get("tool_calls"))
    ids = [c["id"] for c in turn["tool_calls"]]
    assert len(set(ids)) == 2, "a repeated id is a 400 upstream, read as a 503 downstream"
    assert ids[0] == "same"  # the model's own id is kept wherever it can be


def test_the_facade_forwards_data_status_when_nothing_matched(
    public_client: TestClient,
) -> None:
    """Finding 8: "nothing matched" and "nothing is indexed" are different pages."""
    payload = public_client.get("/api/search?q=cache&channel=nobody-at-all").json()
    assert payload["results"] == []
    assert payload["data_status"] == "ok", "a real corpus that this query missed"
    # A page of hits has nothing to say about it, and says nothing.
    assert public_client.get("/api/search?q=cache").json()["data_status"] is None


def test_an_empty_corpus_says_so_rather_than_blaming_the_query(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute("DELETE FROM videos")
        conn.commit()
    finally:
        conn.close()
    app = build_app(
        settings, embeddings=FakeEmbeddings(), run_pipeline=False, public=PUBLIC
    )
    with TestClient(app, base_url="http://localhost:8080") as client:
        payload = client.get("/api/search?q=cache").json()
    assert payload["results"] == []
    assert payload["data_status"] == "empty"


# ------------------------------------------------- 8. the stream (§3.5, §6.6)


NDJSON = {"Accept": "application/x-ndjson"}


def _events(response: Any) -> list[dict[str, Any]]:
    """The stream, parsed. One JSON object per line, and every line complete."""
    assert response.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _stream_ask(client: TestClient, q: str = "what?", ip: str = "1.1.1.1") -> Any:
    return client.post(
        "/api/ask", json={"q": q}, headers={**NDJSON, "CF-Connecting-IP": ip}
    )


def _two_tool_script() -> Upstream:
    return Upstream(
        _completion(
            tool_calls=[_tool_call("c1", "search", {"query": "kv cache", "content_type": "ocr"})]
        ),
        _completion(
            tool_calls=[
                _tool_call("c2", "get_segment_context", {"video_id": "kCc8FmEb1nY", "t": 12})
            ]
        ),
        _completion("The cache trades memory for time [1]."),
    )


def test_the_stream_narrates_every_tool_call_then_answers(tmp_path: Path) -> None:
    """n tool calls → 2n activity events, then exactly one answer (§3.5)."""
    with make_client(tmp_path, PUBLIC_WITH_KEY, _two_tool_script()) as client:
        response = _stream_ask(client)
    assert response.status_code == 200
    events = _events(response)

    kinds = [e["event"] for e in events]
    assert kinds == ["activity"] * 4 + ["answer"], kinds
    # Every start is paired with its own done, in order, by id — that pairing is
    # what lets the page mark exactly one line as the one still running.
    assert [(e["id"], e["phase"]) for e in events[:4]] == [
        (1, "start"),
        (1, "done"),
        (2, "start"),
        (2, "done"),
    ]
    # The answer is not streamed: it arrives whole, once, at the end.
    assert events[-1]["payload"]["answer"] == "The cache trades memory for time [1]."
    assert events[-1]["payload"]["citations"]


def test_a_streamed_answer_is_the_same_payload_as_the_json_one(tmp_path: Path) -> None:
    """One loop, two transports. The page must not get a second-class answer."""
    with make_client(tmp_path / "a", PUBLIC_WITH_KEY, _two_tool_script()) as client:
        streamed = _events(_stream_ask(client))[-1]["payload"]
    with make_client(tmp_path / "b", PUBLIC_WITH_KEY, _two_tool_script()) as client:
        plain = client.post("/api/ask", json={"q": "what?"}).json()
    assert streamed == plain


def test_a_client_that_does_not_ask_for_the_stream_gets_the_old_shape(
    tmp_path: Path,
) -> None:
    """Content negotiation, not a new endpoint: curl's contract is untouched."""
    with make_client(tmp_path, PUBLIC_WITH_KEY, _two_tool_script()) as client:
        response = client.post("/api/ask", json={"q": "what?"})
    assert response.headers["content-type"].startswith("application/json")
    assert set(response.json()) == {"answer", "citations", "rounds", "model"}


def test_the_activity_lines_say_what_was_searched_and_what_came_back(
    tmp_path: Path,
) -> None:
    """Every word of a line comes from the call's args or the tool's result.

    The line is the visitor's only window onto the ninety seconds the model
    spends inside the corpus, so it has to be honest twice: the channel it
    names is the one the model asked for, and the count is counted.
    """
    with make_client(tmp_path, PUBLIC_WITH_KEY, _two_tool_script()) as client:
        events = _events(_stream_ask(client))
    lines = {(e["id"], e["phase"]): e for e in events if e["event"] == "activity"}

    # The channel is the page's own word for that leg, not `content_type=ocr`.
    assert lines[(1, "start")]["text"] == "Searching on-screen text for “kv cache”"
    assert re.fullmatch(r"\d+ hits? in \d+ talks?", lines[(1, "done")]["result"])

    # The drill-down names the talk, because an earlier hit carried its title.
    assert lines[(2, "start")]["text"].startswith("Reading the transcript around 0:12 in “")
    assert "Let's build GPT" in lines[(2, "start")]["text"]
    assert re.fullmatch(r"\d+ lines? of transcript", lines[(2, "done")]["result"])


def test_a_drill_down_into_an_unseen_video_names_the_id_not_a_title(
    tmp_path: Path,
) -> None:
    """No title has been seen for it, so none is invented."""
    upstream = Upstream(
        _completion(
            tool_calls=[
                _tool_call("c1", "get_segment_context", {"video_id": "kCc8FmEb1nY", "t": 12})
            ]
        ),
        _completion("It says the price is memory."),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        events = _events(_stream_ask(client))
    start = next(e for e in events if e.get("phase") == "start")
    assert start["text"] == "Reading the transcript around 0:12 in video kCc8FmEb1nY"


def test_an_unknown_tool_call_is_narrated_rather_than_hidden(tmp_path: Path) -> None:
    """A round that did something the loop refused is not a round that stalled."""
    upstream = Upstream(
        _completion(tool_calls=[_tool_call("c1", "delete_everything", {})]),
        _completion("I only have search."),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        events = _events(_stream_ask(client))
    assert next(e for e in events if e.get("phase") == "start")["text"] == (
        "Asking for “delete_everything”"
    )
    assert next(e for e in events if e.get("phase") == "done")["result"] == "no such tool"


def test_a_search_that_matched_nothing_says_so(tmp_path: Path) -> None:
    """A round that found nothing reads as nothing found, not as a hit count."""
    upstream = Upstream(
        _completion(tool_calls=[_tool_call("c1", "search", {"query": "zzzqqqwww"})]),
        _completion("Nothing in the corpus covers it."),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        events = _events(_stream_ask(client))
    assert next(e for e in events if e.get("phase") == "start")["text"] == (
        "Searching the corpus for “zzzqqqwww”"
    )
    assert next(e for e in events if e.get("phase") == "done")["result"] == "nothing matched"


def test_a_stream_that_dies_mid_way_ends_in_a_terminal_error_event(
    tmp_path: Path,
) -> None:
    """No partial answer, ever: the page gets the degraded pane, not prose."""
    upstream = Upstream(
        _completion(tool_calls=[_tool_call("c1", "search", {"query": "kv cache"})]),
        httpx.Response(429, json={"error": {"message": "quota for key sk-or-REALKEY"}}),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        response = _stream_ask(client)
    events = _events(response)
    assert [e["event"] for e in events] == ["activity", "activity", "error"]
    terminal = events[-1]
    assert terminal["status"] == 503
    # The same body a 503 would have carried, so one renderer serves both.
    assert terminal["payload"] == {
        "error": "llm_unavailable",
        "reason": "upstream_rate_limited",
        "message": "LLM mode unavailable — use search.",
        "retry_after_s": 60,
    }
    # And a stream leaks no more than a status code does.
    assert "sk-or" not in response.text and "quota" not in response.text


def test_a_stream_that_dies_before_the_model_gives_the_day_back(tmp_path: Path) -> None:
    """The refund cannot key on the status: a stream is a 200 whatever happens.

    This is the launch-day failure again (§4.4). The POST path refunds a
    failure that bought nothing; a stream that never got a completion back has
    already sent `200 OK`, so the accounting has to happen inside the body and
    it has to be about the cost — nothing bought, nothing charged.
    """
    upstream = Upstream(httpx.Response(503, json={"error": {"message": "down"}}))
    settings = PublicSettings(
        enabled=True, openrouter_key="sk-or-test", ask_per_min=50, ask_per_day=2
    )
    with make_client(tmp_path, settings, upstream) as client:
        for _ in range(3):
            assert _events(_stream_ask(client))[-1]["event"] == "error"
        # Both of the day's asks are still there.
        upstream.scripted = [_completion("fine.")]
        assert _events(_stream_ask(client))[-1]["event"] == "answer"
        assert _events(_stream_ask(client, ip="2.2.2.2"))[-1]["event"] == "answer"
        spent = _stream_ask(client, ip="3.3.3.3")
    assert spent.status_code == 429
    assert spent.json()["bucket"] == "ask_global"


def test_a_stream_that_dies_after_a_paid_completion_keeps_the_charge(
    tmp_path: Path,
) -> None:
    """The other side of §4.4, and the 2026-08-09 review's HIGH.

    A loop that got one completion back — enough for a tool call, enough for
    the first activity line — has already spent the model's tokens. Refunding
    it because no prose arrived made a paid completion free to anyone willing
    to fail on purpose, and a client can fail on purpose by disconnecting.
    """
    upstream = Upstream(
        _completion(tool_calls=[_tool_call("c1", "search", {"query": "kv cache"})]),
        httpx.Response(503, json={"error": {"message": "upstream is down"}}),
    )
    settings = PublicSettings(
        enabled=True, openrouter_key="sk-or-test", ask_per_min=50, ask_per_day=1
    )
    with make_client(tmp_path, settings, upstream) as client:
        assert _events(_stream_ask(client))[-1]["event"] == "error"
        spent = _stream_ask(client, ip="2.2.2.2")
    assert spent.status_code == 429, "the day's one ask was spent on a real completion"
    assert spent.json()["bucket"] == "ask_global"


# The disconnect itself, which no `TestClient` request can express: the body
# generator is driven by hand and closed mid-flight, which is exactly what an
# ASGI server does to it when the socket goes away. `deps` is never touched
# because both cases stop before a tool runs — the whole point is that the
# second one stops *after* the completion that paid for it.


async def _disconnect_after(frames: int, upstream: Upstream) -> RateLimiter:
    """Charge one ask, read `frames` frames of stream, then hang up."""
    from vidtheque_mcp.public.ask import SSE_STREAM, Billing, OpenRouter, _stream

    limiter = RateLimiter({"ask_global": (5, 86_400.0)}, budget=FakeBudget())
    day = ratelimit.utc_day()
    assert limiter.check("ask_global", "@global", day)[0] is True
    # The shape the middleware leaves behind for the handler to refund from.
    scope = {ratelimit.CHARGES_SCOPE_KEY: (limiter, (("ask_global", "@global"),), day)}

    settings = PublicSettings(enabled=True, openrouter_key="sk-or-test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        llm = OpenRouter(settings, http)
        stream = _stream(scope, None, settings, llm, "what?", SSE_STREAM, Billing())
        read = 0
        async with aclosing(stream):
            async for _frame in stream:
                read += 1
                if read >= frames:
                    break  # the tab closes here
    return limiter


def _spent(limiter: RateLimiter) -> int:
    return limiter._counters[("ask_global", "@global")].spent


async def test_a_disconnect_before_the_first_completion_gives_the_day_back() -> None:
    """Nothing was bought: the SSE preamble goes out before a single request
    to the model does, so a client that leaves there costs nothing."""
    limiter = await _disconnect_after(1, Upstream(_completion("never reached")))
    assert _spent(limiter) == 0
    assert limiter._budget.rows[("ask_global", "@global", ratelimit.utc_day())] == 0


async def test_a_disconnect_after_the_first_completion_keeps_the_charge() -> None:
    """The review's HIGH, at the seam it lives at.

    The first activity line is emitted only once a completion has come back
    with a tool call in it, so a client that waits for that line and then hangs
    up has been served a paid generation. Repeat that and the old rule handed
    the day's token back every time — free tokens, on someone else's key.
    """
    upstream = Upstream(
        _completion(tool_calls=[_tool_call("c1", "search", {"query": "kv cache"})]),
        _completion("unreached"),
    )
    limiter = await _disconnect_after(2, upstream)  # preamble, then activity/start
    assert len(upstream.requests) == 1, "one completion — the one that was paid for"
    assert _spent(limiter) == 1
    assert limiter._budget.rows[("ask_global", "@global", ratelimit.utc_day())] == 1


def test_a_completed_stream_is_charged_exactly_once(tmp_path: Path) -> None:
    """The other half: an answer that landed costs the day a token, like a POST."""
    settings = PublicSettings(
        enabled=True, openrouter_key="sk-or-test", ask_per_min=50, ask_per_day=1
    )
    with make_client(tmp_path, settings, Upstream(_completion("fine."))) as client:
        assert _events(_stream_ask(client))[-1]["event"] == "answer"
        spent = _stream_ask(client, ip="2.2.2.2")
    assert spent.status_code == 429, "a streamed answer spends the budget like any other"
    assert spent.json()["bucket"] == "ask_global"


def test_a_refused_stream_is_a_status_code_not_an_event(tmp_path: Path) -> None:
    """429 happens in the middleware, before a byte of stream exists.

    Which is what keeps the page's countdown working: `Retry-After` is a header
    on a refused request, and a refusal that arrived as an event inside a 200
    would have to reinvent all of it.
    """
    settings = PublicSettings(
        enabled=True, openrouter_key="sk-or-test", ask_per_min=1, ask_per_day=50
    )
    with make_client(tmp_path, settings, Upstream(_completion("fine."))) as client:
        assert _stream_ask(client).status_code == 200
        refused = _stream_ask(client)
    assert refused.status_code == 429
    assert refused.headers["content-type"].startswith("application/json")
    assert int(refused.headers["retry-after"]) >= 1
    assert refused.json()["bucket"] == "ask"


def test_a_failure_before_the_loop_stays_a_status_code_even_for_a_stream(
    tmp_path: Path,
) -> None:
    """Nothing is committed to a 200 until the model is actually reachable."""
    keyless = PublicSettings(enabled=True, ask_per_min=50, ask_per_day=50)
    with make_client(tmp_path / "a", keyless) as client:
        unconfigured = _stream_ask(client)
    with make_client(tmp_path / "b", PUBLIC_WITH_KEY, Upstream(_completion("unused"))) as client:
        empty = client.post("/api/ask", json={}, headers=NDJSON)
    assert unconfigured.status_code == 503
    assert unconfigured.headers["content-type"].startswith("application/json")
    assert unconfigured.json()["reason"] == "not_configured"
    assert empty.status_code == 400
    assert empty.json()["error"] == "E_BAD_PARAM"


def test_the_stream_survives_a_corpus_string_with_a_newline_in_it(
    tmp_path: Path,
) -> None:
    """A line break in a title must not split one event into two frames."""
    from vidtheque_mcp.public.ask import _ndjson

    line = _ndjson({"event": "activity", "text": "a title\nwith a break"})
    assert line.count(b"\n") == 1 and line.endswith(b"\n")
    assert json.loads(line)["text"] == "a title\nwith a break"


def test_the_second_page_can_be_refused_while_the_first_stands(tmp_path: Path) -> None:
    """Finding 9/2's server half: "More results" is a request, and it can 429.

    What the page does with that — a notice under the rows it already has,
    rather than a wipe — needs a DOM-level harness and is not asserted here.
    """
    tight = PublicSettings(enabled=True, search_per_min=1)
    with make_client(tmp_path, tight) as client:
        first = client.get("/api/search?q=cache&limit=2")
        assert first.status_code == 200
        assert first.json()["pagination"]["has_more"] is True, "a second page to refuse"
        second = client.get("/api/search?q=cache&limit=2&offset=2")
    assert second.status_code == 429
    assert second.json()["bucket"] == "search"
    assert int(second.headers["retry-after"]) >= 1


# ------------------------------------ 9. SSE framing (§3.5), the same events


SSE = {"Accept": "text/event-stream"}


def _sse_events(response: Any) -> list[dict[str, Any]]:
    """The SSE stream, parsed — and the framing asserted while parsing it.

    The framing is the entire point of this variant: a CDN keys on the media
    type, and a block that is not `event:` / `data:` / blank line is not SSE
    however valid its JSON is.
    """
    assert response.headers["content-type"].startswith("text/event-stream")
    events: list[dict[str, Any]] = []
    for block in response.text.split("\n\n"):
        if not block.strip():
            continue
        lines = block.split("\n")
        if lines[0].startswith(":"):  # the opening flush comment
            continue
        assert lines[0].startswith("event: "), block
        assert len(lines) == 2, "a payload must never span two data lines"
        assert lines[1].startswith("data: "), block
        event = json.loads(lines[1][len("data: ") :])
        assert event["event"] == lines[0][len("event: ") :], "the name names the payload"
        events.append(event)
    return events


def _sse_ask(client: TestClient, q: str = "what?", ip: str = "1.1.1.1") -> Any:
    return client.post("/api/ask", json={"q": q}, headers={**SSE, "CF-Connecting-IP": ip})


def test_sse_carries_the_same_events_as_the_ndjson_stream(tmp_path: Path) -> None:
    """One vocabulary, two framings. A second event language would be a second
    contract to keep in step with §3.5, and it would drift."""
    with make_client(tmp_path / "a", PUBLIC_WITH_KEY, _two_tool_script()) as client:
        framed = _sse_events(_sse_ask(client))
    with make_client(tmp_path / "b", PUBLIC_WITH_KEY, _two_tool_script()) as client:
        lines = _events(_stream_ask(client))
    assert framed == lines


def test_the_sse_stream_opens_with_something_to_forward(tmp_path: Path) -> None:
    """A comment line before the first completion, so a proxy deciding whether
    to buffer has bytes in hand rather than an idle socket."""
    with make_client(tmp_path, PUBLIC_WITH_KEY, _two_tool_script()) as client:
        body = _sse_ask(client).text
    assert body.startswith(": ok\n\n")


@pytest.mark.parametrize(
    "accept, expected",
    [
        ("text/event-stream", "text/event-stream"),
        ("application/x-ndjson", "application/x-ndjson"),
        # Both offered: SSE wins, because it is the one that survives a CDN.
        ("text/event-stream, application/x-ndjson;q=0.9", "text/event-stream"),
        ("application/x-ndjson, text/event-stream;q=0.9", "text/event-stream"),
        # The page's real header, and the two shapes that must stay untouched.
        (
            "text/event-stream, application/x-ndjson;q=0.9, application/json;q=0.8",
            "text/event-stream",
        ),
        ("application/json", "application/json"),
        ("*/*", "application/json"),
    ],
)
def test_the_accept_header_picks_the_framing(
    tmp_path: Path, accept: str, expected: str
) -> None:
    """`/api/ask` stays one route with one contract: `Accept` is the whole
    difference, and a client that asks for neither stream is byte for byte
    where it was before either existed."""
    with make_client(tmp_path, PUBLIC_WITH_KEY, _two_tool_script()) as client:
        response = client.post("/api/ask", json={"q": "what?"}, headers={"Accept": accept})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(expected)


def test_an_sse_answer_is_the_same_payload_as_the_json_one(tmp_path: Path) -> None:
    """The page must not get a second-class answer for choosing a framing."""
    with make_client(tmp_path / "a", PUBLIC_WITH_KEY, _two_tool_script()) as client:
        framed = _sse_events(_sse_ask(client))[-1]["payload"]
    with make_client(tmp_path / "b", PUBLIC_WITH_KEY, _two_tool_script()) as client:
        plain = client.post("/api/ask", json={"q": "what?"}).json()
    assert framed == plain


def test_an_sse_stream_that_dies_mid_way_ends_in_a_terminal_error_event(
    tmp_path: Path,
) -> None:
    """No partial answer in this framing either, and no leak in the frames."""
    upstream = Upstream(
        _completion(tool_calls=[_tool_call("c1", "search", {"query": "kv cache"})]),
        httpx.Response(429, json={"error": {"message": "quota for key sk-or-REALKEY"}}),
    )
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        response = _sse_ask(client)
    events = _sse_events(response)
    assert [e["event"] for e in events] == ["activity", "activity", "error"]
    assert events[-1]["payload"]["reason"] == "upstream_rate_limited"
    assert "sk-or" not in response.text and "quota" not in response.text


def test_an_sse_stream_that_dies_before_the_model_gives_the_day_back(
    tmp_path: Path,
) -> None:
    """§4.4's rule is about the cost, not the framing: nothing bought, no charge.

    The NDJSON mirror is
    `test_a_stream_that_dies_before_the_model_gives_the_day_back`. Both exist
    because the refund lives in a `finally` inside the generator, and a framing
    that grew its own copy of that generator would grow its own copy of the bug
    — so the framing is a parameter and this proves it stayed one.
    """
    upstream = Upstream(httpx.Response(503, json={"error": {"message": "down"}}))
    settings = PublicSettings(
        enabled=True, openrouter_key="sk-or-test", ask_per_min=50, ask_per_day=2
    )
    with make_client(tmp_path, settings, upstream) as client:
        for _ in range(3):
            assert _sse_events(_sse_ask(client))[-1]["event"] == "error"
        # Both of the day's asks are still there.
        upstream.scripted = [_completion("fine.")]
        assert _sse_events(_sse_ask(client))[-1]["event"] == "answer"
        assert _sse_events(_sse_ask(client, ip="2.2.2.2"))[-1]["event"] == "answer"
        spent = _sse_ask(client, ip="3.3.3.3")
    assert spent.status_code == 429
    assert spent.json()["bucket"] == "ask_global"


def test_a_completed_sse_stream_is_charged_exactly_once(tmp_path: Path) -> None:
    settings = PublicSettings(
        enabled=True, openrouter_key="sk-or-test", ask_per_min=50, ask_per_day=1
    )
    with make_client(tmp_path, settings, Upstream(_completion("fine."))) as client:
        assert _sse_events(_sse_ask(client))[-1]["event"] == "answer"
        spent = _sse_ask(client, ip="2.2.2.2")
    assert spent.status_code == 429, "a framing is not a way to get a free ask"


def test_a_refused_sse_request_is_a_status_code_not_an_event(tmp_path: Path) -> None:
    """A 429 is a header and a JSON body in every framing: it happens in the
    middleware, before a byte of stream exists."""
    settings = PublicSettings(
        enabled=True, openrouter_key="sk-or-test", ask_per_min=1, ask_per_day=50
    )
    with make_client(tmp_path, settings, Upstream(_completion("fine."))) as client:
        assert _sse_ask(client).status_code == 200
        refused = _sse_ask(client)
    assert refused.status_code == 429
    assert refused.headers["content-type"].startswith("application/json")
    assert int(refused.headers["retry-after"]) >= 1


def test_an_sse_frame_cannot_be_split_by_a_newline_in_the_corpus() -> None:
    """SSE reads a bare newline as the end of a field: a corpus title with a
    line break in it would arrive as two `data:` lines and one broken frame."""
    from vidtheque_mcp.public.ask import _sse

    frame = _sse({"event": "activity", "text": "a title\nwith a break"})
    assert frame.endswith(b"\n\n")
    head, data, blank, tail = frame.decode().split("\n")
    assert (head, blank, tail) == ("event: activity", "", "")
    assert json.loads(data[len("data: ") :])["text"] == "a title\nwith a break"


def test_an_sse_event_name_can_never_frame_itself() -> None:
    """Defence in depth: the vocabulary is three literals in `ask.py`, and a
    name carrying a newline would still be header injection in miniature."""
    from vidtheque_mcp.public.ask import _sse

    frame = _sse({"event": "activity\ndata: {}", "x": 1}).decode()
    assert frame.startswith("event: activitydata: {}\n")
    assert len(frame.rstrip("\n").split("\n")) == 2, "still exactly two lines"


# --------------------------- 10. the daily budget survives a restart (§4.2)


def _budget_rows(tmp_path: Path) -> dict[tuple[str, str, str], int]:
    """The `ask_budget` table, read the way an operator would."""
    conn = sqlite3.connect(tmp_path / "data" / "vidtheque.db")
    try:
        return {
            (row[0], row[1], row[2]): row[3]
            for row in conn.execute("SELECT bucket, client, day, spent FROM ask_budget")
        }
    finally:
        conn.close()


def _today() -> str:
    return ratelimit.utc_day()


PAID = PublicSettings(
    enabled=True, openrouter_key="sk-or-test", ask_per_min=50, ask_per_day=2
)


def test_the_days_budget_is_written_down_as_it_is_spent(tmp_path: Path) -> None:
    """One row per (bucket, client, UTC day), counting up. `ask_global` is
    charged to the literal '@global' because that is the limiter's own key."""
    with make_client(tmp_path, PAID, Upstream(_completion("fine."))) as client:
        assert _ask(client).status_code == 200
    assert _budget_rows(tmp_path) == {("ask_global", "@global", _today()): 1}


def test_the_days_budget_resumes_after_a_restart(tmp_path: Path) -> None:
    """The whole point (Tom, 2026-08-09): the model is paid, and a redeploy used
    to hand the day's cap back. Two asks a day, one spent, then a restart — the
    second process must know there is exactly one left."""
    with make_client(tmp_path, PAID, Upstream(_completion("fine."))) as client:
        assert _ask(client).status_code == 200

    with make_client(tmp_path, PAID, Upstream(_completion("fine.")), fresh=False) as client:
        assert _ask(client, "2.2.2.2").status_code == 200, "the second of the two"
        spent = _ask(client, "3.3.3.3")
    assert spent.status_code == 429
    assert spent.json()["bucket"] == "ask_global"
    # And Retry-After is the rollover, which is the honest answer for a budget
    # that resets by the date changing rather than by trickling back.
    assert 1 <= int(spent.headers["retry-after"]) <= 86_400
    assert _budget_rows(tmp_path) == {("ask_global", "@global", _today()): 2}


def test_a_refund_decrements_the_persisted_count_too(tmp_path: Path) -> None:
    """§4.4's refund has to reach the row, or a flap survives the restart it
    caused: three 503s that cost nothing in memory would cost three on disk."""
    upstream = Flapping(fails=3, then=_completion("fine."))
    with make_client(tmp_path, PAID, upstream) as client:
        for _ in range(3):
            assert _ask(client).status_code == 503
    assert _budget_rows(tmp_path) == {("ask_global", "@global", _today()): 0}, (
        "charged and given back, three times, and the row says so"
    )

    with make_client(tmp_path, PAID, upstream, fresh=False) as client:
        assert _ask(client, "2.2.2.2").status_code == 200
        assert _ask(client, "3.3.3.3").status_code == 200
        assert _ask(client, "4.4.4.4").status_code == 429, "still enforced, still 2/day"


@pytest.mark.parametrize("headers", [NDJSON, SSE], ids=["ndjson", "sse"])
def test_the_mid_stream_refund_reaches_the_row_in_both_framings(
    tmp_path: Path, headers: dict[str, str]
) -> None:
    """The refund that cannot key on a status code (§3.5) is also the one that
    has to survive a restart, and it fires from a `finally` inside a generator —
    so it is synchronous all the way down to the row's delta, in both framings.
    """
    upstream = Upstream(httpx.Response(503, json={"error": {"message": "down"}}))
    with make_client(tmp_path, PAID, upstream) as client:
        for _ in range(3):
            response = client.post("/api/ask", json={"q": "what?"}, headers=headers)
            assert response.status_code == 200
            assert response.text.rstrip().endswith("}"), "a terminal event, not an answer"
    assert _budget_rows(tmp_path) == {("ask_global", "@global", _today()): 0}

    with make_client(tmp_path, PAID, upstream, fresh=False) as client:
        upstream.scripted = [_completion("fine.")]
        assert client.post("/api/ask", json={"q": "?"}, headers=headers).status_code == 200
    assert _budget_rows(tmp_path) == {("ask_global", "@global", _today()): 1}


@pytest.mark.parametrize("headers", [NDJSON, SSE], ids=["ndjson", "sse"])
def test_a_paid_stream_that_never_answered_still_costs_the_row(
    tmp_path: Path, headers: dict[str, str]
) -> None:
    """The persisted half of the review's HIGH: money spent is money written
    down, in both framings, even though the stream ended in an error event."""
    upstream = Upstream(
        _completion(tool_calls=[_tool_call("c1", "search", {"query": "kv cache"})]),
        httpx.Response(503, json={"error": {"message": "upstream is down"}}),
    )
    with make_client(tmp_path, PAID, upstream) as client:
        response = client.post("/api/ask", json={"q": "what?"}, headers=headers)
        assert response.status_code == 200
        assert '"event": "error"' in response.text or '"event":"error"' in response.text
    assert _budget_rows(tmp_path) == {("ask_global", "@global", _today()): 1}


def test_a_new_utc_day_starts_the_budget_again(tmp_path: Path) -> None:
    """Rollover is the reset. A day-keyed counter has no trickle to give back,
    so the row for tomorrow is a different row and it starts empty."""
    tight = PublicSettings(
        enabled=True, openrouter_key="sk-or-test", ask_per_min=50, ask_per_day=1
    )
    with make_client(tmp_path, tight, Upstream(_completion("fine."))) as client:
        assert _ask(client).status_code == 200
        assert _ask(client, "2.2.2.2").status_code == 429
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(ratelimit, "utc_day", lambda now=None: "2099-01-01")
            assert _ask(client, "3.3.3.3").status_code == 200, "a new day, a new budget"
    assert _budget_rows(tmp_path) == {
        ("ask_global", "@global", _today()): 1,
        ("ask_global", "@global", "2099-01-01"): 1,
    }


def test_only_the_daily_bucket_is_written_down(tmp_path: Path) -> None:
    """The minute buckets stay in memory and should: they guard against
    hammering, they cost nothing, and nobody hammers across a restart."""
    with make_client(tmp_path, PublicSettings(enabled=True)) as client:
        assert client.get("/api/search?q=cache").status_code == 200
        assert client.get("/api/meta").status_code == 200
    assert _budget_rows(tmp_path) == {}


# ------------------------------------- 10.1 the counter, without a database


class FakeBudget:
    """A `BudgetStore` that is a dict, so the day maths can be tested directly."""

    def __init__(self, rows: dict[tuple[str, str, str], int] | None = None) -> None:
        self.rows = dict(rows or {})

    def spent(self, bucket: str, client: str, day: str) -> int:
        return self.rows.get((bucket, client, day), 0)

    def record(self, bucket: str, client: str, day: str, delta: int) -> None:
        key = (bucket, client, day)
        self.rows[key] = max(0, self.rows.get(key, 0) + delta)


def test_a_limiter_resumes_the_day_from_what_the_store_already_holds() -> None:
    today = ratelimit.utc_day()
    store = FakeBudget({("ask_global", "@global", today): 4})
    limiter = RateLimiter({"ask_global": (5, 86_400.0)}, budget=store)

    allowed, wait, limit, remaining = limiter.check("ask_global", "@global")
    assert (allowed, limit, remaining) == (True, 5, 0), "the fifth of five"
    allowed, wait, _, _ = limiter.check("ask_global", "@global")
    assert allowed is False
    # The wait is the rollover, not a trickle: this budget resets by date.
    assert 0 < wait <= 86_400
    assert store.rows[("ask_global", "@global", today)] == 5


def test_a_refund_credits_the_day_the_charge_was_made_on() -> None:
    """A ninety-second ask that starts at 23:59:30 must give its token back to
    the day it took it from — not to a fresh day that would then be one short."""
    store = FakeBudget()
    limiter = RateLimiter({"ask_global": (5, 86_400.0)}, budget=store)
    assert limiter.check("ask_global", "@global", "2026-08-09")[0] is True
    # Midnight passes under the in-flight request; a new day's charge lands on
    # its own row and the live counter is now a cache of *that* day.
    assert limiter.check("ask_global", "@global", "2026-08-10")[3] == 4
    limiter.refund("ask_global", "@global", "2026-08-09")
    assert store.rows[("ask_global", "@global", "2026-08-09")] == 0
    assert store.rows[("ask_global", "@global", "2026-08-10")] == 1, "today is untouched"


def test_a_persisted_refund_can_never_mint_budget() -> None:
    """The bucket caps a refill at capacity; the counter floors a refund at
    zero. Same rule, and the row is floored by the same `max(0, …)`."""
    store = FakeBudget()
    limiter = RateLimiter({"ask_global": (5, 86_400.0)}, budget=store)
    day = ratelimit.utc_day()
    limiter.check("ask_global", "@global")
    for _ in range(4):
        limiter.refund("ask_global", "@global")
    assert store.rows[("ask_global", "@global", day)] == 0
    assert limiter._counters[("ask_global", "@global")].spent == 0


def test_a_counter_for_a_stale_day_is_dropped_rather_than_rolled() -> None:
    """The live counter is a cache of today and nothing else: a new day builds a
    new one from the store, and yesterday's is forgotten instead of reused."""
    store = FakeBudget({("ask_global", "@global", "2026-08-10"): 3})
    limiter = RateLimiter({"ask_global": (5, 86_400.0)}, budget=store)
    limiter.check("ask_global", "@global", "2026-08-09")
    assert limiter._counters[("ask_global", "@global")].spent == 1
    assert limiter.check("ask_global", "@global", "2026-08-10")[3] == 1, "5 - 3 - 1"
    assert len(limiter._counters) == 1, "yesterday's counter is not kept"
