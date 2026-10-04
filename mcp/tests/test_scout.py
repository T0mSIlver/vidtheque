"""Discovery outside the follows (companion.md §6.2): the nightly scout, its caps
and back-off, and the weekly speaker suggestion.

The YouTube source and the model are fakes; nothing here reaches either.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from vidtheque_mcp.app import Assembled
from vidtheque_mcp.discover import picks
from vidtheque_mcp.discover import scout as scout_mod
from vidtheque_mcp.discover.scout import Scout
from vidtheque_mcp.llm import LLMUnavailable
from vidtheque_mcp.pipeline.sources import RateLimited, SearchHit, SourceError, SubtitleTrack
from vidtheque_mcp.profile import store as profile_store

NIGHT = datetime(2026, 10, 7, 5, 30, tzinfo=UTC)  # a Wednesday
WEEK = "2026-10-05"
VTT = """WEBVTT

00:00:01.000 --> 00:00:09.000
We built an eval harness for coding agents.

00:00:09.000 --> 00:00:20.000
It runs every pull request against forty tasks.

00:00:20.000 --> 00:00:31.000
The pass rate went from 31 to 58 percent.
"""


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeModel:
    """Answers in turn, and records each call in `llm_calls` as the real client does."""

    def __init__(self, db: Any, *answers: Any) -> None:
        self.db = db
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self, prompt: str, *, system=None, schema=None, purpose="unknown", **labels
    ) -> Any:
        self.calls.append({"prompt": prompt, "purpose": purpose})
        at = int(NIGHT.timestamp())
        await self.db.write(
            lambda c: c.execute(
                "INSERT INTO llm_calls (at, purpose, backend, latency_ms, outcome) VALUES (?, ?, 'api', 1, 'ok')",
                (at, purpose),
            )
        )
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeSource:
    def __init__(
        self, hits: dict[str, list[SearchHit]] | None = None, captions: bool = True
    ) -> None:
        self.hits = hits or {}
        self.captions = captions
        self.requests: list[str] = []
        self.block_on: str | None = None
        self.stale_captions = False

    def _ask(self, what: str) -> None:
        self.requests.append(what)
        if self.block_on and what.startswith(self.block_on):
            raise RateLimited("Sign in to confirm you're not a bot")

    def search(self, query: str, max_items: int, *, this_month: bool = False) -> list[SearchHit]:
        self._ask(f"search:{query}")
        return self.hits.get(query, [])[:max_items]

    def probe(self, url: str) -> dict[str, Any]:
        self._ask(f"probe:{url}")
        source_id = url.rsplit("/", 1)[-1]
        info: dict[str, Any] = {
            "id": source_id,
            "title": f"Talk {source_id}",
            "webpage_url": f"https://www.youtube.com/watch?v={source_id}",
            "channel_id": "UCoutside00000000000000",
            "channel": "Outside Channel",
            "duration": 1800,
            "timestamp": int(NIGHT.timestamp()) - 86_400,
        }
        if self.captions:
            info["automatic_captions"] = {
                "en": [{"ext": "vtt", "url": f"https://caps/{source_id}"}]
            }
        return info

    def fetch_subtitle(self, track: SubtitleTrack) -> str:
        self._ask(f"captions:{track.url}")
        if self.stale_captions:
            raise SourceError("HTTP Error 404: the timedtext URL expired")
        return VTT


def hit(
    source_id: str,
    *,
    duration: float = 1800,
    channel: str = "Outside Channel",
    channel_id: str = "UCoutside00000000000000",
) -> SearchHit:
    return SearchHit(
        source_id=source_id,
        title=f"Talk {source_id}",
        channel_id=channel_id,
        channel_name=channel,
        channel_url=f"https://www.youtube.com/@{channel.replace(' ', '').lower()}",
        duration_s=duration,
    )


def verdict(score: int = 2) -> dict[str, Any]:
    return {
        "score": score,
        "reason": "The eval harness is the part you would reuse.",
        "summary": "A harness runs forty tasks per pull request; pass rate rose from 31 to 58 percent.",
        "moments": [
            {
                "cue_id": 2,
                "offset_s": 10.0,
                "end_cue_id": 3,
                "why": "Forty tasks on every pull request",
            },
            {"cue_id": 9, "offset_s": 100.0, "end_cue_id": 9, "why": "A cue that does not exist"},
            {"cue_id": 1, "offset_s": 15.0, "end_cue_id": 1, "why": "Offset outside its cue"},
        ],
        "matches": [],
    }


async def profile(assembled: Assembled, *entries: tuple[str, float]) -> None:
    await assembled.db.write(
        lambda c: profile_store.apply(c, profile_store.Ops(add=list(entries)), "owner")
    )


def make(
    assembled: Assembled, model: FakeModel, source: FakeSource, now: datetime = NIGHT
) -> Scout:
    return Scout(assembled.db, model, "api:test", source, clock=Clock(now))


async def rows(assembled: Assembled, sql: str, *args: Any) -> list[sqlite3.Row]:
    return await assembled.db.read(lambda c: c.execute(sql, args).fetchall())


# ------------------------------------------------------------------ topics


async def test_a_night_judges_one_new_video_per_entry_on_its_captions(assembled: Assembled) -> None:
    await profile(assembled, ("Coding agent evals", 0.9), ("Launch hype", -0.8))
    corpus_id = (await rows(assembled, "SELECT source_id FROM videos LIMIT 1"))[0]["source_id"]
    source = FakeSource(
        {
            "Coding agent evals": [
                hit("shortvid000", duration=120),  # under 5 minutes
                hit(corpus_id),  # already in the corpus
                hit("newtalk0001"),
                hit("newtalk0002"),
            ]
        }
    )
    model = FakeModel(assembled.db, verdict(2))
    assert await make(assembled, model, source).run_once() == "done"

    # Three requests: the search, one metadata fetch, one caption track.
    assert source.requests == [
        "search:Coding agent evals",
        "probe:https://youtu.be/newtalk0001",
        "captions:https://caps/newtalk0001",
    ]
    assert [c["purpose"] for c in model.calls] == ["scout_verdict"]
    assert "[cue 2 9.0–20.0] It runs every pull request" in model.calls[0]["prompt"]
    assert "Launch hype" in model.calls[0]["prompt"]  # the profile as verdicts read it

    payload = await assembled.db.read(lambda c: picks.week_payload(c, WEEK))
    [pick] = payload["picks"]
    assert pick["video_id"] == "newtalk0001"
    assert pick["because"] == "Coding agent evals"
    assert pick["follow"] == {"state": "none", "until": None}
    # Receipts: the moment inside its cue survives; a missing cue and an offset outside are dropped.
    assert pick["moments"] == [
        {
            "offset_s": 10.0,
            "end_s": 31.0,
            "why": "Forty tasks on every pull request",
            "url": "https://youtu.be/newtalk0001?t=10",
        }
    ]
    [run] = await rows(assembled, "SELECT * FROM scout_runs")
    assert (run["state"], run["requests"], run["judged"], run["shown"]) == ("done", 3, 1, 1)

    # The same night is never run twice.
    assert await make(assembled, model, source).run_once() is None


async def test_a_low_score_is_kept_but_not_shown(assembled: Assembled) -> None:
    await profile(assembled, ("Coding agent evals", 0.9))
    source = FakeSource({"Coding agent evals": [hit("newtalk0001")]})
    await make(assembled, FakeModel(assembled.db, verdict(1)), source).run_once()
    assert (await assembled.db.read(lambda c: picks.week_payload(c, WEEK)))["picks"] == []
    [row] = await rows(assembled, "SELECT state, score FROM outside_picks")
    assert (row["state"], row["score"]) == ("judged", 1)


async def test_no_captions_costs_no_model_call(assembled: Assembled) -> None:
    await profile(assembled, ("Coding agent evals", 0.9))
    source = FakeSource({"Coding agent evals": [hit("newtalk0001")]}, captions=False)
    model = FakeModel(assembled.db)
    await make(assembled, model, source).run_once()
    assert model.calls == []
    [row] = await rows(assembled, "SELECT state FROM outside_picks")
    assert row["state"] == "no_captions"


async def test_a_refused_caption_track_costs_that_candidate_only(assembled: Assembled) -> None:
    await profile(assembled, ("Coding agent evals", 0.9))
    source = FakeSource({"Coding agent evals": [hit("newtalk0001")]})
    source.stale_captions = True
    model = FakeModel(assembled.db)
    assert await make(assembled, model, source).run_once() == "done"
    assert model.calls == []
    [row] = await rows(assembled, "SELECT state FROM outside_picks")
    assert row["state"] == "no_captions"


async def test_a_followed_channel_is_passed_over(assembled: Assembled) -> None:
    from vidtheque_mcp.tools import follows as follows_tool

    await profile(assembled, ("Coding agent evals", 0.9))
    await follows_tool.follow_channel(assembled.deps, url="https://www.youtube.com/@outsidechannel")
    source = FakeSource({"Coding agent evals": [hit("newtalk0001")]})
    await make(assembled, FakeModel(assembled.db), source).run_once()
    assert source.requests == ["search:Coding agent evals"]


async def test_the_week_stops_at_three_shown(assembled: Assembled) -> None:
    await profile(assembled, ("Coding agent evals", 0.9))
    await assembled.db.write(
        lambda c: [
            picks.insert(
                c,
                source_id=f"shown{i:06d}",
                week=WEEK,
                because="x",
                title="t",
                state="shown",
                score=2,
            )
            for i in range(3)
        ]
    )
    source = FakeSource({"Coding agent evals": [hit("newtalk0001")]})
    assert await make(assembled, FakeModel(assembled.db), source).run_once() == "idle"
    assert source.requests == []


async def test_the_week_stops_at_its_model_call_cap(assembled: Assembled) -> None:
    await profile(assembled, ("Coding agent evals", 0.9))
    at = int(NIGHT.timestamp()) - 3600
    await assembled.db.write(
        lambda c: c.executemany(
            "INSERT INTO llm_calls (at, purpose, backend, latency_ms, outcome) VALUES (?, 'scout_verdict', 'api', 1, 'ok')",
            [(at,)] * picks.CALLS_PER_WEEK,
        )
    )
    source = FakeSource({"Coding agent evals": [hit("newtalk0001")]})
    assert await make(assembled, FakeModel(assembled.db), source).run_once() == "idle"
    assert source.requests == []


async def test_an_invalid_answer_costs_that_candidate_only(assembled: Assembled) -> None:
    await profile(assembled, ("Coding agent evals", 0.9), ("Local inference", 0.5))
    source = FakeSource(
        {"Coding agent evals": [hit("newtalk0001")], "Local inference": [hit("newtalk0002")]}
    )
    model = FakeModel(assembled.db, LLMUnavailable("invalid_output"), verdict(3))
    assert await make(assembled, model, source).run_once() == "done"
    states = {
        r["source_id"]: r["state"]
        for r in await rows(assembled, "SELECT source_id, state FROM outside_picks")
    }
    assert states == {"newtalk0001": "judged", "newtalk0002": "shown"}


# ---------------------------------------------------------------- back-off


async def test_a_bot_check_stops_the_night_and_spaces_out_the_next(assembled: Assembled) -> None:
    await profile(assembled, ("Coding agent evals", 0.9), ("Local inference", 0.5))
    source = FakeSource({"Coding agent evals": [hit("newtalk0001")]})
    source.block_on = "probe"
    model = FakeModel(assembled.db)
    assert await make(assembled, model, source).run_once() == "blocked"
    assert source.requests == ["search:Coding agent evals", "probe:https://youtu.be/newtalk0001"]
    assert await rows(assembled, "SELECT 1 FROM outside_picks") == []

    # One blocked night skips the next one; the night after asks again.
    source.block_on = "search"
    assert await make(assembled, model, source, NIGHT + timedelta(days=1)).run_once() is None
    assert await make(assembled, model, source, NIGHT + timedelta(days=2)).run_once() == "blocked"
    # Two in a row skip two nights.
    for day in (3, 4):
        assert await make(assembled, model, source, NIGHT + timedelta(days=day)).run_once() is None
    assert await make(assembled, model, source, NIGHT + timedelta(days=5)).run_once() == "blocked"


async def test_the_scout_waits_while_indexing_waits_out_a_block(assembled: Assembled) -> None:
    await profile(assembled, ("Coding agent evals", 0.9))
    await assembled.db.write(
        lambda c: c.execute(
            "INSERT INTO jobs (public_id, kind, state, error_code, not_before, args_json)"
            " VALUES ('job_000000000001', 'index', 'queued', 'E_RATE_LIMIT', ?, '{}')",
            (int(NIGHT.timestamp()) + 3600,),
        )
    )
    source = FakeSource({"Coding agent evals": [hit("newtalk0001")]})
    assert await make(assembled, FakeModel(assembled.db), source).run_once() is None
    assert source.requests == []


# ---------------------------------------------------------------- speakers


async def like_a_talk(assembled: Assembled) -> tuple[int, str]:
    def write(c: sqlite3.Connection) -> tuple[int, str]:
        v = c.execute("SELECT id, public_id FROM videos LIMIT 1").fetchone()
        c.execute(
            "INSERT INTO feedback (owner_id, video_id, state, at) VALUES (1, ?, 'up', ?)",
            (v["id"], int(NIGHT.timestamp()) - 86_400),
        )
        return int(v["id"]), str(v["public_id"])

    return await assembled.db.write(write)


async def test_a_liked_talk_yields_one_speaker_a_week(assembled: Assembled) -> None:
    _, public_id = await like_a_talk(assembled)
    names = {
        "speakers": [
            {"name": "Ada Lovelace", "video_id": public_id},
            {"name": "Grace Hopper", "video_id": public_id},
        ]
    }
    source = FakeSource(
        {
            # Ada has no channel and one other talk: not enough.
            '"Ada Lovelace"': [hit("adatalk0001", channel="Some Conf")],
            '"Grace Hopper"': [
                hit("gracechan01", channel="Grace Hopper", channel_id="UCgrace00000000000000000")
            ],
        }
    )
    model = FakeModel(assembled.db, names)
    assert await make(assembled, model, source).run_once() == "done"
    assert [c["purpose"] for c in model.calls] == ["speaker_names"]
    assert '"on_screen"' in model.calls[0]["prompt"] and "_row" not in model.calls[0]["prompt"]
    speaker = (await assembled.db.read(lambda c: picks.week_payload(c, WEEK)))["speaker"]
    assert speaker["name"] == "Grace Hopper"
    assert speaker["channel"] == {
        "name": "Grace Hopper",
        "url": "https://www.youtube.com/@gracehopper",
    }
    assert speaker["reason"].endswith(
        "which you thumbed up; has a channel of their own, Grace Hopper."
    )
    [run] = await rows(assembled, "SELECT speaker FROM scout_runs")
    assert run["speaker"] == "suggested"

    # The next night of the same week asks no model.
    model2 = FakeModel(assembled.db)
    await make(assembled, model2, source, NIGHT + timedelta(days=1)).run_once()
    assert model2.calls == []


async def test_a_name_is_never_suggested_twice(assembled: Assembled) -> None:
    _, public_id = await like_a_talk(assembled)
    await assembled.db.write(
        lambda c: c.execute(
            "INSERT INTO speaker_suggestions (week, name, name_key, talk_title, reason, state)"
            " VALUES ('2026-09-28', 'Grace Hopper', 'gracehopper', 't', 'r', 'dismissed')"
        )
    )
    names = {"speakers": [{"name": "Grace Hopper", "video_id": public_id}]}
    source = FakeSource()
    model = FakeModel(assembled.db, names)
    await make(assembled, model, source).run_once()
    assert source.requests == []
    [run] = await rows(assembled, "SELECT speaker FROM scout_runs")
    assert run["speaker"] == "none"


def test_receipts_are_checked_against_the_fetched_cues() -> None:
    from vidtheque_mcp.pipeline.captions import cues_from_vtt

    cues = cues_from_vtt(VTT)
    kept = scout_mod.receipts(cues, verdict()["moments"])
    assert kept == [{"offset_s": 10.0, "end_s": 31.0, "why": "Forty tasks on every pull request"}]
    assert json.dumps(kept)
