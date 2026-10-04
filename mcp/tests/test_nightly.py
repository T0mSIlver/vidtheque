"""The nightly profile update (companion.md §2.4): its guards, and once a day.

The model is a fake that answers what each test hands it; nothing calls a real one.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx2 as httpx
import pytest

from vidtheque_mcp.app import Assembled
from vidtheque_mcp.llm import LLMUnavailable
from vidtheque_mcp.profile import nightly as nightly_mod
from vidtheque_mcp.profile import feedback, signals, store
from vidtheque_mcp.profile.github import GitHubSettings
from vidtheque_mcp.profile.nightly import Nightly

DAY = datetime(2026, 10, 3, 5, 0, tzinfo=timezone.utc)


class FakeModel:
    """Answers each of `answers` in turn; a test that expects no call passes none."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.labels: list[dict[str, Any]] = []

    async def complete(self, prompt: str, *, system=None, schema=None, **labels) -> Any:
        self.prompts.append(prompt)
        self.labels.append(labels)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def ops(*items: dict) -> dict:
    return {"ops": [{"reason": "evidence", **i} for i in items]}


def nightly(parts: Assembled, model: FakeModel, clock: Clock | None = None) -> Nightly:
    return Nightly(parts.db, model, "api:fake", hour=4, clock=clock or Clock(DAY))


async def signal(parts: Assembled, at: datetime, kind: str = "mcp_search", text: str = "eval harness") -> None:
    await parts.db.write(
        lambda c: signals.record_signal(c, kind, text=text, client="claude", now=int(at.timestamp()))
    )


async def write(parts: Assembled, actor: str, *adds: tuple[str, float]) -> list[int]:
    applied = await parts.db.write(lambda c: store.apply(c, store.Ops(add=adds), actor=actor))
    return applied.event_ids


async def live(parts: Assembled) -> dict[str, float]:
    return {r["text"]: float(r["weight"]) for r in await parts.db.read(store.entries)}


async def ids(parts: Assembled) -> dict[str, int]:
    return {r["text"]: int(r["id"]) for r in await parts.db.read(store.entries)}


async def runs(parts: Assembled) -> list[sqlite3.Row]:
    return await parts.db.read(lambda c: c.execute("SELECT * FROM nightly_runs ORDER BY id").fetchall())


# -------------------------------------------------------------- the guards


async def test_a_run_with_no_signals_calls_no_model_and_changes_nothing(assembled: Assembled) -> None:
    model = FakeModel()  # any call would pop from an empty list
    outcome = await nightly(assembled, model).run_once()
    assert outcome is not None and outcome.state == "idle"
    assert model.prompts == []
    assert [r["state"] for r in await runs(assembled)] == ["idle"]


async def test_at_most_five_ops_apply_a_night(assembled: Assembled) -> None:
    await signal(assembled, DAY - timedelta(hours=2))
    model = FakeModel(ops(*({"op": "add", "text": f"topic {i}", "weight": 0.2} for i in range(7))))

    outcome = await nightly(assembled, model).run_once()

    assert len(outcome.applied) == 5
    assert sorted(await live(assembled)) == [f"topic {i}" for i in range(5)]
    assert [r["refused"] for r in outcome.refused] == ["over 5 changes a night"] * 2
    run = (await runs(assembled))[0]
    assert (run["state"], run["n_applied"], len(json.loads(run["refused"]))) == ("done", 5, 2)


async def test_a_weight_moves_at_most_point_three_a_night(assembled: Assembled) -> None:
    await write(assembled, "agent", ("Evals", 0.2), ("Hype", -0.1))
    entry = await ids(assembled)
    await signal(assembled, DAY - timedelta(hours=2))
    model = FakeModel(
        ops(
            {"op": "reweight", "id": entry["Evals"], "weight": 0.9},
            {"op": "reweight", "id": entry["Hype"], "weight": -0.3},
            {"op": "add", "text": "GPU inference", "weight": -0.8},
        )
    )

    await nightly(assembled, model).run_once()

    assert await live(assembled) == {"Evals": 0.5, "Hype": -0.3, "GPU inference": -0.3}
    events = await assembled.db.read(
        lambda c: c.execute("SELECT actor, op, reason FROM profile_events WHERE actor = 'nightly' ORDER BY id").fetchall()
    )
    assert [(e["op"], e["reason"]) for e in events] == [
        ("reweight", "evidence (capped at ±0.3 a night)"),
        ("reweight", "evidence"),
        ("add", "evidence (capped at ±0.3 a night)"),
    ]


async def test_an_entry_changes_once_a_night(assembled: Assembled) -> None:
    await write(assembled, "agent", ("Evals", 0.2))
    entry = (await ids(assembled))["Evals"]
    await signal(assembled, DAY - timedelta(hours=2))
    model = FakeModel(
        ops({"op": "reweight", "id": entry, "weight": 0.5}, {"op": "reweight", "id": entry, "weight": 0.8})
    )
    outcome = await nightly(assembled, model).run_once()
    assert await live(assembled) == {"Evals": 0.5}
    assert [r["refused"] for r in outcome.refused] == ["an entry changes once a night"]


@pytest.mark.parametrize("actor", ["owner", "app"])
async def test_entries_the_owner_wrote_are_reweighted_never_dropped(
    assembled: Assembled, actor: str
) -> None:
    await write(assembled, actor, ("Evals", 0.6), ("Hype", -0.5))
    await write(assembled, "agent", ("Rust", 0.4))
    entry = await ids(assembled)
    await signal(assembled, DAY - timedelta(hours=2))
    model = FakeModel(
        ops(
            {"op": "drop", "id": entry["Evals"]},
            {"op": "reweight", "id": entry["Hype"], "weight": -0.3},
            {"op": "drop", "id": entry["Rust"]},
        )
    )
    outcome = await nightly(assembled, model).run_once()
    assert await live(assembled) == {"Evals": 0.6, "Hype": -0.3}
    assert len(outcome.refused) == 1 and "only the owner can drop it" in outcome.refused[0]["refused"]


async def test_the_profile_stays_at_forty_live_entries(assembled: Assembled) -> None:
    await write(assembled, "owner", *((f"interest {i}", 0.1) for i in range(store.MAX_LIVE - 1)))
    await signal(assembled, DAY - timedelta(hours=2))
    model = FakeModel(
        ops({"op": "add", "text": "one more", "weight": 0.2}, {"op": "add", "text": "too many", "weight": 0.2})
    )
    outcome = await nightly(assembled, model).run_once()
    assert len(await live(assembled)) == store.MAX_LIVE
    assert "one more" in await live(assembled)
    assert len(outcome.refused) == 1 and "capped at 40" in outcome.refused[0]["refused"]


async def test_a_nightly_op_is_revertible_like_any_other(assembled: Assembled) -> None:
    await write(assembled, "agent", ("Evals", 0.2))
    before = await assembled.db.read(store.revision)
    await signal(assembled, DAY - timedelta(hours=2))
    entry = (await ids(assembled))["Evals"]
    await nightly(assembled, FakeModel(ops({"op": "reweight", "id": entry, "weight": 0.4}))).run_once()
    await assembled.db.write(lambda c: store.revert_to(c, before, actor="owner"))
    assert await live(assembled) == {"Evals": 0.2}


# ---------------------------------------------------------- once a day


async def test_a_day_runs_once_across_restarts_and_the_next_reads_only_newer_signals(
    assembled: Assembled,
) -> None:
    await signal(assembled, DAY - timedelta(hours=2), text="first day query")
    first = FakeModel(ops({"op": "add", "text": "Evals", "weight": 0.2}))
    assert (await nightly(assembled, first).run_once()).state == "done"

    # A restart is a new object over the same database, later the same day.
    again = FakeModel()
    later = Clock(DAY + timedelta(hours=10))
    assert await nightly(assembled, again, later).run_once() is None
    assert again.prompts == []
    assert await live(assembled) == {"Evals": 0.2}

    await signal(assembled, DAY + timedelta(hours=12), text="second day query")
    second = FakeModel(ops())
    tomorrow = Clock(DAY + timedelta(days=1))
    assert (await nightly(assembled, second, tomorrow).run_once()).state == "done"
    assert "second day query" in second.prompts[0]
    assert "first day query" not in second.prompts[0]
    assert [r["day"] for r in await runs(assembled)] == ["2026-10-03", "2026-10-04"]


async def test_a_failed_model_call_applies_nothing_and_retries_that_day_a_few_times(
    assembled: Assembled,
) -> None:
    await signal(assembled, DAY - timedelta(hours=2))
    clock = Clock(DAY)
    down = LLMUnavailable("upstream_unavailable")
    model = FakeModel(down, down, down)
    job = nightly(assembled, model, clock)

    assert (await job.run_once()).state == "failed"
    clock.now += timedelta(minutes=30)
    assert await job.run_once() is None  # not yet: an hour between tries
    for _ in range(nightly_mod.MAX_ATTEMPTS - 1):
        clock.now += timedelta(hours=1)
        assert (await job.run_once()).state == "failed"
    clock.now += timedelta(hours=1)
    assert await job.run_once() is None  # out of tries for today
    assert len(model.prompts) == nightly_mod.MAX_ATTEMPTS
    assert await live(assembled) == {}
    run = (await runs(assembled))[0]
    assert (run["state"], run["attempts"], run["error"]) == ("failed", 3, "upstream_unavailable")


async def test_a_run_a_crash_left_claimed_is_retried_after_an_hour(assembled: Assembled) -> None:
    await signal(assembled, DAY - timedelta(hours=2))
    # The process died after the claim, before the model answered.
    await assembled.db.write(
        lambda c: nightly_mod.claim_day(c, "2026-10-03", int(DAY.timestamp()))
    )
    model = FakeModel(ops({"op": "add", "text": "Evals", "weight": 0.2}))
    assert await nightly(assembled, model).run_once() is None
    assert (
        await nightly(assembled, model, Clock(DAY + timedelta(hours=1))).run_once()
    ).state == "done"
    assert await live(assembled) == {"Evals": 0.2}


async def test_the_tick_starts_the_run_only_from_the_configured_hour(assembled: Assembled) -> None:
    await signal(assembled, DAY - timedelta(hours=2))
    clock = Clock(DAY.replace(hour=3))
    job = nightly(assembled, FakeModel(ops()), clock)
    await job.tick()
    assert job._task is None and await runs(assembled) == []

    clock.now = DAY
    await job.tick()
    assert job._task is not None
    await asyncio.wait_for(job._task, 5)
    assert [r["state"] for r in await runs(assembled)] == ["done"]


# ------------------------------------------------------- thumbs and mutes


async def video(parts: Assembled) -> tuple[str, str]:
    """A video the fixture's corpus holds: (public id, title)."""
    row = await parts.db.read(lambda c: c.execute("SELECT public_id, title FROM videos ORDER BY id").fetchone())
    return row[0], row[1]


async def tap(parts: Assembled, video_id: str, state: str, at: datetime) -> None:
    await parts.db.write(lambda c: feedback.set_state(c, video_id, state, now=int(at.timestamp())))


async def test_a_thumb_taken_back_before_the_night_never_reaches_it(assembled: Assembled) -> None:
    vid, _ = await video(assembled)
    await tap(assembled, vid, "up", DAY - timedelta(hours=3))
    await tap(assembled, vid, "none", DAY - timedelta(hours=2))
    model = FakeModel()
    assert (await nightly(assembled, model).run_once()).state == "idle"
    assert model.prompts == []


async def test_a_night_reads_each_state_once_and_then_its_taking_back(assembled: Assembled) -> None:
    vid, title = await video(assembled)
    await tap(assembled, vid, "down", DAY - timedelta(hours=3))
    await tap(assembled, vid, "muted", DAY - timedelta(hours=2))
    first = FakeModel(ops())
    assert (await nightly(assembled, first).run_once()).state == "done"
    assert f'mute\t"{title}"' in first.prompts[0]
    assert "thumb_down" not in first.prompts[0]

    await tap(assembled, vid, "none", DAY + timedelta(hours=12))
    second = FakeModel(ops())
    assert (await nightly(assembled, second, Clock(DAY + timedelta(days=1))).run_once()).state == "done"
    assert f'took back mute\t"{title}"' in second.prompts[0]

    third = FakeModel()
    assert (await nightly(assembled, third, Clock(DAY + timedelta(days=2))).run_once()).state == "idle"
    assert await assembled.db.read(lambda c: c.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]) == 0


async def test_a_first_tap_taken_back_while_the_model_answers_is_read_the_next_night(
    assembled: Assembled,
) -> None:
    vid, title = await video(assembled)
    await tap(assembled, vid, "up", DAY - timedelta(hours=2))

    class TakesBack(FakeModel):
        async def complete(self, prompt: str, **options: Any) -> Any:
            await tap(assembled, vid, "none", DAY - timedelta(minutes=1))
            return await super().complete(prompt, **options)

    first = TakesBack(ops())
    assert (await nightly(assembled, first).run_once()).state == "done"
    assert f'thumb_up\t"{title}"' in first.prompts[0]
    second = FakeModel(ops())
    assert (await nightly(assembled, second, Clock(DAY + timedelta(days=1))).run_once()).state == "done"
    assert f'took back thumb_up\t"{title}"' in second.prompts[0]


async def test_a_lapsed_project_retires_even_on_a_night_with_no_signals(assembled: Assembled) -> None:
    month_ago = int((DAY - timedelta(days=31)).timestamp())
    await assembled.db.write(
        lambda c: store.apply(
            c, store.Ops(add=[("Android video app", 0.5, "project")]), actor="agent", now=month_ago
        )
    )
    outcome = await nightly(assembled, FakeModel()).run_once()
    assert outcome is not None and outcome.state == "idle"
    retired = await assembled.db.read(
        lambda c: c.execute("SELECT retired_at FROM profile_entries").fetchone()[0]
    )
    assert retired is not None


# ---------------------------------------------------------- GitHub projects


def repo(name: str, days_ago: float, **extra: Any) -> dict[str, Any]:
    pushed = (DAY - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {
        "name": name, "owner": {"login": "T0mSIlver"}, "fork": False, "archived": False,
        "pushed_at": pushed, "language": "Kotlin", "topics": [], "description": None, **extra,
    }


def github_api(repos: list[dict[str, Any]], seen: list[Any], status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=repos if status == 200 else {"message": "boom"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def with_github(parts: Assembled, model: FakeModel, http: httpx.AsyncClient) -> Nightly:
    return Nightly(
        parts.db, model, "api:fake", hour=4, clock=Clock(DAY),
        github_settings=GitHubSettings("T0mSIlver"), http=http,
    )


async def projects(parts: Assembled) -> dict[str, str]:
    rows = await parts.db.read(store.entries)
    return {r["text"]: r["source"] for r in rows if r["kind"] == "project"}


async def test_github_projects_come_from_active_repos_and_pass_the_checks(assembled: Assembled) -> None:
    seen: list[Any] = []
    http = github_api(
        [
            repo("vidtheque", 1, description="Timestamped knowledge from videos"),
            repo("job-search", 2, description="applications tracker"),
            repo("forked", 1, fork=True),
            repo("old-thing", 45),
            repo("theirs", 1, owner={"login": "someone"}),
        ],
        seen,
    )
    model = FakeModel(
        {"projects": [{"text": "Android app in Kotlin"}, {"text": "Cloudflare tunnels"},
                      {"text": "Interview prep tools"}]}
    )

    outcome = await with_github(assembled, model, http).run_once()

    assert outcome is not None and outcome.state == "idle"  # no signals: one model call, GitHub's
    [request] = seen
    assert request.url.path == "/users/T0mSIlver/repos" and "authorization" not in request.headers
    [prompt] = model.prompts
    assert "vidtheque" in prompt
    assert not any(name in prompt for name in ("job-search", "forked", "old-thing", "theirs"))
    assert model.labels == [{"purpose": "github_projects"}]
    assert await projects(assembled) == {"Android app in Kotlin": "nightly"}


async def test_an_active_repo_keeps_its_project_and_a_private_one_is_never_read(
    assembled: Assembled,
) -> None:
    month_ago = int((DAY - timedelta(days=29)).timestamp())
    await assembled.db.write(
        lambda c: store.apply(
            c, store.Ops(add=[("Android app in Kotlin", 0.5, "project")]), actor="nightly", now=month_ago
        )
    )
    seen: list[Any] = []
    model = FakeModel({"projects": [{"text": "Android app in Kotlin"}]})

    listing = [repo("app", 1), repo("secret-thing", 1, private=True)]
    await with_github(assembled, model, github_api(listing, seen)).run_once()

    assert "secret-thing" not in model.prompts[0]
    assert "- Android app in Kotlin" in model.prompts[0]  # offered for reuse
    [row] = await assembled.db.read(store.entries)
    assert row["expires_at"] == int(DAY.timestamp()) + store.PROJECT_TTL_S


async def test_a_github_failure_costs_only_its_pass(assembled: Assembled) -> None:
    month_ago = int((DAY - timedelta(days=31)).timestamp())
    await assembled.db.write(
        lambda c: store.apply(c, store.Ops(add=[("Old project", 0.5, "project")]), actor="agent", now=month_ago)
    )
    outcome = await with_github(assembled, FakeModel(), github_api([], [], status=500)).run_once()
    assert outcome is not None and outcome.state == "idle"
    assert await projects(assembled) == {}  # the expiry pass still ran
