"""Registration smoke test through the actual MCP app.

Nine tools with the contract's names and annotations, three resources, and one
real `tools/call` round trip over streamable HTTP — the surface a client sees,
not the Python functions behind it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vidtheque_mcp.app import build_app
from vidtheque_mcp.config import Settings
from vidtheque_mcp.tools.descriptions import ANNOTATIONS, DESCRIPTIONS

from .conftest import FakeEmbeddings, rpc, rpc_headers, seed

EXPECTED_TOOLS = {
    "search",
    "list-videos",
    "corpus-summary",
    "video-summary",
    "get-segment-context",
    "get-frames",
    "index-video",
    "job-status",
    "tag-video",
}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    data = tmp_path / "data"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    settings = Settings(
        data_dir=data,
        public_url="http://localhost:8080",
        worker_url="http://worker:8081",
        auth_mode="none",
        secret="test-secret",
    )
    app = build_app(settings, embeddings=FakeEmbeddings(), run_pipeline=False)
    with TestClient(app, base_url="http://localhost:8080") as c:
        yield c


def call(client: TestClient, method: str, params: dict | None = None) -> dict:
    name = (params or {}).get("name") or (params or {}).get("uri")
    response = client.post(
        "/mcp", json=rpc(method, params), headers=rpc_headers(method, name=name)
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload, payload["error"]
    return payload["result"]


def test_all_nine_tools_are_registered(client: TestClient) -> None:
    tools = {t["name"]: t for t in call(client, "tools/list")["tools"]}
    assert set(tools) == EXPECTED_TOOLS


def test_tool_annotations_match_the_contract(client: TestClient) -> None:
    tools = {t["name"]: t for t in call(client, "tools/list")["tools"]}
    for name, expected in ANNOTATIONS.items():
        got = tools[name]["annotations"]
        assert got["title"] == expected.title
        assert got["readOnlyHint"] is expected.read_only_hint
        assert got["idempotentHint"] is expected.idempotent_hint
        assert got["openWorldHint"] is expected.open_world_hint

    # openWorldHint is true only for the tool that reaches the internet.
    assert tools["index-video"]["annotations"]["openWorldHint"] is True
    # job-status is the one read tool that must not be cached.
    assert tools["job-status"]["annotations"]["idempotentHint"] is False


def test_descriptions_ship_and_stay_inside_the_budget(client: TestClient) -> None:
    tools = {t["name"]: t for t in call(client, "tools/list")["tools"]}
    for name, description in DESCRIPTIONS.items():
        assert tools[name]["description"] == description
        # DECISIONS.md: <= ~120 words each, shared rules in the guide resource.
        assert len(description.split()) <= 120, name
        assert "USE WHEN" in description
        assert "DO NOT USE" in description


def test_get_frames_exposes_the_contract_parameter_name(client: TestClient) -> None:
    """`return` is a Python keyword; the wire name must still be `return`."""
    tools = {t["name"]: t for t in call(client, "tools/list")["tools"]}
    properties = tools["get-frames"]["inputSchema"]["properties"]
    assert "return" in properties
    assert "return_" not in properties


def test_param_names_follow_the_decision_record(client: TestClient) -> None:
    """t_start/t_end intra-video, published_* corpus, offset for pagination."""
    tools = {t["name"]: t for t in call(client, "tools/list")["tools"]}
    search_params = tools["search"]["inputSchema"]["properties"]
    assert {"t_start", "t_end", "published_after", "published_before", "offset"} <= set(
        search_params
    )
    assert "offset_start" not in search_params
    assert "t_start" in tools["get-frames"]["inputSchema"]["properties"]


def test_three_resources_with_the_right_mime_types(client: TestClient) -> None:
    resources = {r["uri"]: r for r in call(client, "resources/list")["resources"]}
    assert set(resources) == {"vidtheque://corpus", "vidtheque://context", "vidtheque://guide"}
    assert resources["vidtheque://corpus"]["mimeType"] == "text/tab-separated-values"
    assert resources["vidtheque://context"]["mimeType"] == "application/json"
    assert resources["vidtheque://guide"]["mimeType"] == "text/markdown"


def test_reading_the_guide_resource(client: TestClient) -> None:
    result = call(client, "resources/read", {"uri": "vidtheque://guide"})
    text = result["contents"][0]["text"]
    assert "# Using vidtheque" in text
    assert "Never fabricate ids" in text


def test_calling_search_over_streamable_http(client: TestClient) -> None:
    result = call(client, "tools/call", {"name": "search", "arguments": {"q": "cache", "limit": 3}})
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert "Results:" in text
    assert "https://youtu.be/" in text
    assert result["structuredContent"]["pagination"]["limit"] == 3


def test_calling_get_frames_with_the_return_argument(client: TestClient) -> None:
    result = call(
        client,
        "tools/call",
        {
            "name": "get-frames",
            "arguments": {"frame_ids": ["kCc8FmEb1nY-00000"], "return": "image"},
        },
    )
    kinds = [block["type"] for block in result["content"]]
    assert "image" in kinds
    image = next(b for b in result["content"] if b["type"] == "image")
    assert image["mimeType"] == "image/jpeg"


def test_a_typed_error_comes_back_as_is_error(client: TestClient) -> None:
    result = call(
        client, "tools/call", {"name": "video-summary", "arguments": {"video_id": "nope"}}
    )
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "E_UNKNOWN_VIDEO"
    assert result["content"][0]["text"].startswith("error: E_UNKNOWN_VIDEO")


def test_an_unknown_parameter_name_is_a_typed_error(client: TestClient) -> None:
    """terra eval §4.5: `tag=` and `sort_by=` were dropped, and the call 200'd.

    The same tool has always been strict about the two neighbouring classes
    (`order="nonesuch"`, `fields="…,not_a_column"`); one tool, three standards
    is what let a caller believe it had filtered and sorted.
    """
    result = call(
        client,
        "tools/call",
        {
            "name": "search",
            "arguments": {"q": "cache", "tag": "topic:test", "sort_by": "recency", "limit": 2},
        },
    )
    assert result["isError"] is True
    payload = result["structuredContent"]
    assert payload["code"] == "E_BAD_PARAM"
    assert "tag=" in payload["message"] and "sort_by=" in payload["message"]
    # …and it names the right parameter for each, rather than only refusing.
    assert "tag= → tags=" in payload["next"]
    assert "sort_by= → order=" in payload["next"]
    assert "content_type" in payload["next"]  # the full domain, as `fields` does


def test_the_wrong_time_axis_is_rejected_by_name(client: TestClient) -> None:
    """§3.2: `t_start` on the corpus-shaped tool is the axis confusion, not a typo."""
    result = call(
        client, "tools/call", {"name": "list-videos", "arguments": {"t_start": 2019, "limit": 1}}
    )
    assert result["structuredContent"]["code"] == "E_BAD_PARAM"
    assert "published_after" in result["structuredContent"]["next"]


def test_the_return_alias_is_a_known_parameter_name(client: TestClient) -> None:
    """`return` is the wire name of `return_`; the guard must not reject it."""
    ok = call(
        client,
        "tools/call",
        {"name": "get-frames", "arguments": {"frame_ids": ["kCc8FmEb1nY-00000"], "return": "url"}},
    )
    assert ok["isError"] is False
    # …and the Python spelling is not a second, undocumented name for it.
    rejected = call(
        client,
        "tools/call",
        {"name": "get-frames", "arguments": {"frame_ids": ["kCc8FmEb1nY-00000"], "return_": "url"}},
    )
    assert rejected["structuredContent"]["code"] == "E_BAD_PARAM"


def test_protocol_metadata_keys_are_left_alone(client: TestClient) -> None:
    """`_`-prefixed keys belong to the protocol and to client vendors."""
    result = call(
        client,
        "tools/call",
        {"name": "search", "arguments": {"q": "cache", "limit": 1, "_meta": {"trace": "x"}}},
    )
    assert result["isError"] is False


def test_healthz_is_public_and_reports_the_mode(client: TestClient) -> None:
    payload = client.get("/healthz").json()
    assert payload["status"] == "ok"
    assert payload["auth"] == "none"
    assert payload["vector_legs"] is True
