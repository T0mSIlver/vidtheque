"""The interest profile and the signals it learns from (companion.md §2.1–2.3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from mcp.server import MCPServer
from mcp_types import TextContent
from starlette.testclient import TestClient

from vidtheque_mcp.app import Assembled, build_app
from vidtheque_mcp.config import Settings
from vidtheque_mcp.profile import signals, store
from vidtheque_mcp.public.settings import PublicSettings
from vidtheque_mcp.tools import CALL_CONTEXT, CallContext, register
from vidtheque_mcp.tools import profile as profile_tool
from vidtheque_mcp.tools.profile import NewEntry, Reweight

from .conftest import FakeEmbeddings, rpc, rpc_headers, seed

VIDEO = "kCc8FmEb1nY"


async def profile(deps, **kwargs):
    return await profile_tool.profile(deps, **kwargs)


def structured(result) -> dict:
    assert result.structured_content is not None
    return result.structured_content


def entries(result) -> dict[str, float]:
    return {e["text"]: e["weight"] for e in structured(result)["entries"]}


async def owner_writes(deps, *adds: tuple[str, float]) -> None:
    await deps.db.write(lambda c: store.apply(c, store.Ops(add=adds), actor="owner"))


async def live_state(deps) -> dict[str, float]:
    rows = await deps.db.read(store.entries)
    return {r["text"]: float(r["weight"]) for r in rows}


# ---------------------------------------------------------------- the tool ops


async def test_add_reweight_drop_round_trip(assembled: Assembled) -> None:
    deps = assembled.deps
    bare = await profile(deps)
    assert structured(bare) == {
        "revision": 0,
        "entries": [],
        "applied_events": [],
        "duplicates": [],
    }

    added = await profile(
        deps,
        add=[NewEntry(text="Eval harnesses  for coding agents", weight=0.9),
             NewEntry(text="Model launch hype", weight=-0.8)],
        reason="asked about evals four times",
    )
    assert entries(added) == {"Eval harnesses for coding agents": 0.9, "Model launch hype": -0.8}
    ids = {e["text"]: e["id"] for e in structured(added)["entries"]}
    rev_after_add = structured(added)["revision"]
    assert rev_after_add > 0
    assert all(e["source"] == "agent" for e in structured(added)["entries"])

    changed = await profile(
        deps,
        reweight=[Reweight(id=ids["Eval harnesses for coding agents"], weight=0.4)],
        drop=[ids["Model launch hype"]],
    )
    assert entries(changed) == {"Eval harnesses for coding agents": 0.4}
    assert structured(changed)["revision"] > rev_after_add

    # A case-different repeat of a live entry is noted, not added twice.
    again = await profile(deps, add=[NewEntry(text="eval harnesses for coding agents", weight=1)])
    assert structured(again)["duplicates"] == ["eval harnesses for coding agents"]
    assert entries(again) == {"Eval harnesses for coding agents": 0.4}
    text = "\n".join(b.text for b in again.content if isinstance(b, TextContent))
    assert "already an entry" in text


async def test_a_refused_batch_writes_nothing(assembled: Assembled) -> None:
    deps = assembled.deps
    result = await profile(deps, add=[NewEntry(text="Local inference", weight=0.5)], drop=[999])
    assert result.is_error
    assert structured(result)["code"] == "E_UNKNOWN_ENTRY"
    assert await live_state(deps) == {}
    assert structured(await profile(deps))["revision"] == 0


@pytest.mark.parametrize("weight", [1.01, -1.5])
async def test_a_weight_outside_the_range_is_refused(assembled: Assembled, weight: float) -> None:
    result = await profile(assembled.deps, add=[NewEntry(text="x", weight=weight)])
    assert structured(result)["code"] == "E_BAD_PARAM"
    assert await live_state(assembled.deps) == {}


@pytest.mark.parametrize(
    "text",
    ["Evals and benchmarks for coding agents", "RL for agents, for search too"],
)
async def test_an_entry_longer_than_a_short_topic_is_refused(assembled: Assembled, text: str) -> None:
    result = await profile(assembled.deps, add=[NewEntry(text=text, weight=0.5)])
    assert structured(result)["code"] == "E_BAD_PARAM"
    assert "split a compound topic" in "\n".join(b.text for b in result.content if isinstance(b, TextContent))
    assert await live_state(assembled.deps) == {}
    ok = await profile(assembled.deps, add=[NewEntry(text="Coding agent evals", weight=0.5)])
    assert not ok.is_error


# ------------------------------------------------------------------ the guards


async def test_an_agent_cannot_drop_what_the_owner_wrote(assembled: Assembled) -> None:
    deps = assembled.deps
    await owner_writes(deps, ("Local inference on consumer GPUs", 0.6))
    [entry] = structured(await profile(deps))["entries"]

    refused = await profile(deps, drop=[entry["id"]])
    assert structured(refused)["code"] == "E_PROFILE_GUARD"
    assert await live_state(deps) == {"Local inference on consumer GPUs": 0.6}

    # Reweighting it is allowed.
    reweighted = await profile(deps, reweight=[Reweight(id=entry["id"], weight=0.2)])
    assert entries(reweighted) == {"Local inference on consumer GPUs": 0.2}

    # And the owner can drop it.
    await deps.db.write(
        lambda c: store.apply(c, store.Ops(drop=[entry["id"]]), actor="owner")
    )
    assert await live_state(deps) == {}


async def test_the_profile_holds_at_most_forty_live_entries(assembled: Assembled) -> None:
    deps = assembled.deps
    full = await profile(deps, add=[NewEntry(text=f"topic {i}", weight=0.1) for i in range(40)])
    assert len(structured(full)["entries"]) == 40

    over = await profile(deps, add=[NewEntry(text="topic 40", weight=0.1)])
    assert structured(over)["code"] == "E_PROFILE_GUARD"
    assert len(await live_state(deps)) == 40

    # Dropping one in the same call makes room.
    first = structured(full)["entries"][0]["id"]
    swapped = await profile(deps, drop=[first], add=[NewEntry(text="topic 40", weight=0.1)])
    assert len(structured(swapped)["entries"]) == 40
    assert "topic 40" in entries(swapped)


# ------------------------------------------------------------------ revert


async def test_reverting_one_event_restores_only_what_it_changed(assembled: Assembled) -> None:
    deps = assembled.deps
    added = await profile(deps, add=[NewEntry(text="Evals", weight=0.9)])
    entry_id = structured(added)["entries"][0]["id"]
    reweighted = await profile(deps, reweight=[Reweight(id=entry_id, weight=0.3)])
    [reweight_event] = structured(reweighted)["applied_events"]
    await profile(deps, drop=[entry_id])

    # Undoing the reweight brings the weight back but not the dropped entry.
    await deps.db.write(lambda c: store.revert_event(c, reweight_event, actor="owner"))
    assert await live_state(deps) == {}
    row = await deps.db.read(
        lambda c: c.execute("SELECT weight FROM profile_entries WHERE id = ?", (entry_id,)).fetchone()
    )
    assert row["weight"] == 0.9



async def test_reverting_an_add_retires_it_and_reverting_that_revives_it(
    assembled: Assembled,
) -> None:
    deps = assembled.deps
    added = await profile(deps, add=[NewEntry(text="Evals", weight=0.9)])
    [add_event] = structured(added)["applied_events"]
    revert_id = await deps.db.write(lambda c: store.revert_event(c, add_event, actor="owner"))
    assert await live_state(deps) == {}
    await deps.db.write(lambda c: store.revert_event(c, revert_id, actor="owner"))
    assert await live_state(deps) == {"Evals": 0.9}


async def test_revert_to_a_revision_restores_that_profile(assembled: Assembled) -> None:
    deps = assembled.deps
    first = await profile(
        deps, add=[NewEntry(text="Evals", weight=0.9), NewEntry(text="Hype", weight=-0.8)]
    )
    target = structured(first)["revision"]
    ids = {e["text"]: e["id"] for e in structured(first)["entries"]}
    await profile(deps, reweight=[Reweight(id=ids["Evals"], weight=0.1)], drop=[ids["Hype"]])
    await profile(deps, add=[NewEntry(text="Robotics", weight=0.5)])
    await profile(deps, reweight=[Reweight(id=ids["Evals"], weight=-0.2)])

    await deps.db.write(lambda c: store.revert_to(c, target, actor="owner"))
    assert await live_state(deps) == {"Evals": 0.9, "Hype": -0.8}
    # The rollback is itself history, so the revision moves forward.
    assert structured(await profile(deps))["revision"] > target


async def test_an_agent_revert_cannot_retire_an_owner_entry(assembled: Assembled) -> None:
    deps = assembled.deps
    await owner_writes(deps, ("Local inference", 0.6))
    add_event = structured(await profile(deps))["revision"]
    with pytest.raises(store.ProfileRefused):
        await deps.db.write(lambda c: store.revert_event(c, add_event, actor="agent"))
    assert await live_state(deps) == {"Local inference": 0.6}


# ------------------------------------------------------------------ signals


@pytest.fixture
def make_client(tmp_path: Path):
    def build(public: bool = False) -> TestClient:
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
        app = build_app(
            settings,
            embeddings=FakeEmbeddings(),
            run_pipeline=False,
            public=PublicSettings(enabled=public),
        )
        return TestClient(app, base_url="http://localhost:8080")

    return build


def call_tool(client: TestClient, name: str, arguments: dict, **extra: str) -> dict:
    headers = rpc_headers("tools/call", name=name) | extra
    response = client.post(
        "/mcp", json=rpc("tools/call", {"name": name, "arguments": arguments}), headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def signal_rows(tmp_path: Path) -> list[tuple]:
    conn = sqlite3.connect(tmp_path / "data" / "vidtheque.db")
    try:
        return conn.execute(
            "SELECT s.kind, v.source_id, s.offset_s, s.text FROM signals s"
            " LEFT JOIN videos v ON v.id = s.video_id ORDER BY s.id"
        ).fetchall()
    finally:
        conn.close()


def test_searches_and_reads_are_logged_as_signals(make_client, tmp_path: Path) -> None:
    with make_client() as client:
        assert call_tool(client, "search", {"q": "kv cache", "limit": 1})["isError"] is False
        call_tool(client, "video-summary", {"video_id": VIDEO})
        call_tool(client, "get-segment-context", {"video_id": VIDEO, "t": "1:05"})
        call_tool(client, "get-transcript", {"video_id": VIDEO, "t_start": 30})
        # Not signals: calls that failed, and a tool outside the list.
        assert call_tool(client, "search", {"q": "kv cache", "order": "bogus"})["isError"] is True
        call_tool(client, "video-summary", {"video_id": "nope"})
        call_tool(client, "list-videos", {})
    assert signal_rows(tmp_path) == [
        ("mcp_search", None, None, "kv cache"),
        ("mcp_read", VIDEO, None, None),
        ("mcp_read", VIDEO, 65.0, None),
        ("mcp_read", VIDEO, 30.0, None),
    ]


def test_the_opt_out_header_stops_logging_for_that_request(make_client, tmp_path: Path) -> None:
    with make_client() as client:
        call_tool(client, "search", {"q": "kv cache"}, **{"X-Vidtheque-Signals": "off"})
        call_tool(client, "video-summary", {"video_id": VIDEO}, **{"X-Vidtheque-Signals": "OFF"})
        call_tool(client, "search", {"q": "logged"})
    assert signal_rows(tmp_path) == [("mcp_search", None, None, "logged")]


def test_a_public_deployment_logs_no_signals(make_client, tmp_path: Path) -> None:
    with make_client(public=True) as client:
        assert call_tool(client, "search", {"q": "kv cache"})["isError"] is False
    assert signal_rows(tmp_path) == []


async def test_a_call_marked_quiet_is_not_a_signal(assembled: Assembled) -> None:
    """The triage agent calls tools in-process with signals off (§2.3)."""
    mcp = MCPServer("test")
    register(mcp, assembled.deps)

    async def count() -> int:
        return (await assembled.deps.db.read(
            lambda c: c.execute("SELECT COUNT(*) FROM signals").fetchone()
        ))[0]

    token = CALL_CONTEXT.set(CallContext(client="triage", signals=False))
    try:
        await mcp.call_tool("search", {"q": "kv cache"})
    finally:
        CALL_CONTEXT.reset(token)
    assert await count() == 0
    await mcp.call_tool("search", {"q": "kv cache"})
    assert await count() == 1


async def test_signals_older_than_180_days_are_deleted(assembled: Assembled) -> None:
    now = 1_800_000_000
    day = 86_400

    def write(c: sqlite3.Connection) -> list[str]:
        signals.record_signal(c, "open", video_id=VIDEO, now=now - 181 * day)
        signals.record_signal(c, "open", video_id=VIDEO, now=now - 179 * day)
        signals.record_signal(c, "thumb_up", video_id=VIDEO, now=now)
        return [r["kind"] for r in c.execute("SELECT kind FROM signals ORDER BY at")]

    assert await assembled.deps.db.write(write) == ["open", "thumb_up"]


async def test_a_signal_for_a_video_not_in_the_corpus_records_nothing(assembled: Assembled) -> None:
    got = await assembled.deps.db.write(
        lambda c: signals.record_signal(c, "thumb_up", video_id="notavideo00")
    )
    assert got is None
