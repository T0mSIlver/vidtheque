"""The public demo surface: masking, the `/api` facade, limits, the page.

Nothing here reaches the network. OpenRouter is faked at the same seam the
worker is — an injected client, here over ``httpx2.MockTransport`` — so the ask
loop is exercised end to end (tool calls, evidence, citations, degradation)
without a key, a model, or a request leaving the process.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import httpx2 as httpx
import pytest
from starlette.testclient import TestClient

from vidtheque_mcp.app import build_app
from vidtheque_mcp.config import Settings
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


def _settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    return Settings(
        data_dir=data,
        public_url="http://localhost:8080",
        worker_url="http://worker:8081",
        auth_mode="none",
        secret="test-secret",
    )


def make_client(
    tmp_path: Path,
    public: PublicSettings,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> TestClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler)) if handler else None
    app = build_app(
        _settings(tmp_path),
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
    assert thumb.endswith(".jpg?w=320&q=70")  # auth=none: unsigned, and honest about it
    assert public_client.get(thumb).status_code == 200


def test_search_facade_keeps_token_discipline(public_client: TestClient) -> None:
    """The facade's bounds are tighter than the tool's, and server-side."""
    payload = public_client.get("/api/search?q=cache&limit=999&max_text_chars=0").json()
    assert payload["pagination"]["limit"] == 20  # not 50, and not 999
    # There is no full-transcript opt-out on a public endpoint.
    assert all(len(hit["text"]) <= 400 + 80 for hit in payload["results"])


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
    assert by_id["kCc8FmEb1nY"]["thumb"].endswith("kCc8FmEb1nY-00000.jpg?w=320&q=70")
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
    assert "youtube" in body.lower() or "YouTube" in body


def test_the_page_assets_are_served_and_confined(public_client: TestClient) -> None:
    assert public_client.get("/static/style.css").status_code == 200
    assert public_client.get("/static/app.js").status_code == 200
    assert public_client.get("/static/../../config.py").status_code in (404, 400)
    assert public_client.get("/static/nope.css").status_code == 404


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

    assert payload["rounds"] == 1
    assert payload["model"] == "openai/gpt-oss-20b:free"
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


def test_ask_offers_exactly_two_tools(tmp_path: Path) -> None:
    upstream = Upstream(_completion("nothing to cite."))
    with make_client(tmp_path, PUBLIC_WITH_KEY, upstream) as client:
        client.post("/api/ask", json={"q": "hello"})
    names = {t["function"]["name"] for t in upstream.requests[0]["tools"]}
    assert names == {"search", "get_segment_context"}


def test_ask_forces_an_answer_when_the_rounds_run_out(tmp_path: Path) -> None:
    """A model still calling tools on the last round gets tools switched off."""
    looping = _completion(tool_calls=[_tool_call("c", "search", {"query": "cache"})])
    upstream = Upstream(looping, looping, httpx.Response(200, json=_completion("Final answer.")))
    settings = PublicSettings(enabled=True, openrouter_key="sk-or-test", ask_max_rounds=2)
    with make_client(tmp_path, settings, upstream) as client:
        payload = client.post("/api/ask", json={"q": "why?"}).json()
    assert payload["answer"] == "Final answer."
    assert payload["rounds"] == 2
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
