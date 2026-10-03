"""Verdicts (companion.md §3): queued after indexing, receipts checked, failures contained.

The model is a fake that answers what each test hands it; nothing calls a real one.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from vidtheque_mcp.app import Assembled
from vidtheque_mcp.config import Settings
from vidtheque_mcp.jobs import store as jobs_store
from vidtheque_mcp.llm import LLMUnavailable
from vidtheque_mcp.profile import store as profile_store
from vidtheque_mcp.verdicts import store
from vidtheque_mcp.verdicts.stage import (
    VerdictStage,
    build_verdicts,
    middle_lines,
)

from .pipeline_fakes import VIDEO_URL
from .test_pipeline_e2e import harness


class FakeModel:
    """Answers `answer` every time, or each of `answers` in turn."""

    def __init__(self, answer: Any = None, *, answers: list[Any] | None = None) -> None:
        self.answers = list(answers) if answers is not None else None
        self.answer = answer
        self.prompts: list[str] = []
        self.labels: list[dict[str, Any]] = []

    async def complete(self, prompt: str, *, system=None, schema=None, **labels) -> Any:
        self.prompts.append(prompt)
        self.labels.append(labels)
        answer = self.answers.pop(0) if self.answers is not None else self.answer
        if isinstance(answer, Exception):
            raise answer
        return answer


class FixedRoll:
    """An RNG whose every roll is `value`."""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value


def verdict(*moments: dict, score: int = 2) -> dict:
    return {
        "score": score,
        "reason": "On evals.",
        "summary": "A talk.",
        "moments": list(moments),
        "matches": [],
    }


async def rows(db, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return await db.read(lambda c: c.execute(sql, params).fetchall())


async def video_id(db, source_id: str) -> int:
    return int((await rows(db, "SELECT id FROM videos WHERE source_id = ?", (source_id,)))[0][0])


async def run_verdict(
    parts: Assembled, vid: int, model: FakeModel, rng: FixedRoll | None = None
) -> sqlite3.Row:
    # Never explores unless the test hands it a roll.
    parts.runner.handlers["verdict"] = VerdictStage(
        parts.deps, model, "api:fake", rng=rng or FixedRoll(1.0)
    )
    job = await parts.db.write(lambda c: store.queue(c, vid))
    assert job is not None
    assert await parts.runner.run_once() is True
    return (await rows(parts.db, "SELECT * FROM jobs WHERE public_id = ?", (job,)))[0]


# ------------------------------------------------------------------ receipts


async def test_only_moments_inside_a_cue_of_this_video_are_stored(assembled: Assembled) -> None:
    db = assembled.db
    vid = await video_id(db, "kCc8FmEb1nY")
    other = await video_id(db, "zduSFxRajkE")
    cue = (
        await rows(
            db, "SELECT id, start_s, end_s FROM cues WHERE video_id = ? ORDER BY seq", (vid,)
        )
    )[0]
    foreign = (await rows(db, "SELECT id, start_s FROM cues WHERE video_id = ?", (other,)))[0]
    await db.write(
        lambda c: profile_store.apply(c, profile_store.Ops(add=[("Evals", 0.9)]), actor="owner")
    )
    rev = await db.read(profile_store.revision)
    inside = (float(cue["start_s"]) + float(cue["end_s"])) / 2
    model = FakeModel(
        verdict(
            {"cue_id": cue["id"], "offset_s": inside, "why": "kept"},
            {"cue_id": cue["id"], "offset_s": float(cue["end_s"]) + 0.5, "why": "outside the cue"},
            {"cue_id": foreign["id"], "offset_s": float(foreign["start_s"]), "why": "other video"},
        )
    )

    job = await run_verdict(assembled, vid, model)

    assert job["state"] == "done"
    stored = store.moments_of(await db.read(lambda c: store.get(c, vid)))
    assert stored == [store.Moment(int(cue["id"]), inside, "kept")]
    row = await db.read(lambda c: store.get(c, vid))
    assert (row["score"], row["profile_rev"], row["model"]) == (2, rev, "api:fake")
    events = [
        r[0]
        for r in await rows(db, "SELECT message FROM job_events WHERE job_id = ?", (job["id"],))
    ]
    assert any("dropped 2 moment(s)" in e for e in events)
    # The prompt carried the profile and the cue ids the model must cite.
    assert "+0.9  Evals" in model.prompts[0]
    assert f"[cue {cue['id']} " in model.prompts[0]
    # The triage agent's reads are not the owner's signals.
    assert await rows(db, "SELECT * FROM signals") == []


async def test_a_verdict_with_no_surviving_moment_is_still_stored(assembled: Assembled) -> None:
    vid = await video_id(assembled.db, "kCc8FmEb1nY")
    await run_verdict(
        assembled, vid, FakeModel(verdict({"cue_id": 999999, "offset_s": 1.0, "why": "made up"}))
    )
    row = await assembled.db.read(lambda c: store.get(c, vid))
    assert row is not None and store.moments_of(row) == []


async def test_a_rerun_replaces_the_verdict_and_keeps_notified_at(assembled: Assembled) -> None:
    db = assembled.db
    vid = await video_id(db, "kCc8FmEb1nY")
    await run_verdict(assembled, vid, FakeModel(verdict(score=1)))
    await db.write(
        lambda c: c.execute("UPDATE verdicts SET notified_at = 123 WHERE video_id = ?", (vid,))
    )
    await run_verdict(assembled, vid, FakeModel(verdict(score=3)))
    row = await db.read(lambda c: store.get(c, vid))
    assert (row["score"], row["notified_at"]) == (3, 123)


async def test_matches_keep_only_live_entries_and_take_their_direction_from_the_weight(
    assembled: Assembled,
) -> None:
    db = assembled.db
    vid = await video_id(db, "kCc8FmEb1nY")
    await db.write(
        lambda c: profile_store.apply(
            c,
            profile_store.Ops(add=[("Evals", 0.9), ("Launch hype", -0.7), ("Rust", 0.0)]),
            actor="owner",
        )
    )
    ids = {r["text"]: int(r["id"]) for r in await db.read(profile_store.entries)}
    answer = verdict(score=1)
    answer["matches"] = [
        {"entry_id": ids["Launch hype"], "strength": 1},
        {"entry_id": 999_999, "strength": 2},  # not an entry
        {"entry_id": ids["Rust"], "strength": 2},  # weight 0 points nowhere
        {"entry_id": ids["Evals"], "strength": 2},
        {"entry_id": ids["Evals"], "strength": 1},  # once per entry
    ]
    model = FakeModel(answer)

    await run_verdict(assembled, vid, model)

    row = await db.read(lambda c: store.get(c, vid))
    assert json.loads(row["matches"]) == [
        {"entry_id": ids["Evals"], "direction": "up", "strength": 2},
        {"entry_id": ids["Launch hype"], "direction": "down", "strength": 1},
    ]
    assert f"[{ids['Evals']}] +0.9  Evals" in model.prompts[0]


# ------------------------------------------------------------------ novelty


async def _seen(db, vid: int, kind: str, days_ago: int) -> None:
    at = int(time.time()) - days_ago * 86_400
    await db.write(
        lambda c: c.execute(
            "INSERT INTO signals (at, kind, video_id, client) VALUES (?, ?, ?, 'app')",
            (at, kind, vid),
        )
    )


async def _copy_vectors(db, src: int, dst: int) -> None:
    """Give `dst`'s chunk the vector of `src`'s: the same passage, said twice."""

    def copy(c: sqlite3.Connection) -> None:
        blob = c.execute("SELECT embedding FROM vec_chunks WHERE video_id = ?", (src,)).fetchone()[
            0
        ]
        chunk, start = c.execute(
            "SELECT chunk_id, start_s FROM vec_chunks WHERE video_id = ?", (dst,)
        ).fetchone()
        c.execute("DELETE FROM vec_chunks WHERE chunk_id = ?", (chunk,))
        c.execute(
            "INSERT INTO vec_chunks (chunk_id, video_id, start_s, embedding) VALUES (?, ?, ?, ?)",
            (chunk, dst, start, blob),
        )

    await db.write(copy)


@pytest.mark.parametrize(
    ("kind", "days_ago", "same_passage", "flagged"),
    [
        ("open", 3, True, True),
        ("mcp_read", 89, True, True),
        # Outside the 90-day window.
        ("open", 91, True, False),
        # A search names no video; a swipe-away is not "seen".
        ("dismiss", 3, True, False),
        # Seen, but it says something else.
        ("open", 3, False, False),
    ],
)
async def test_a_video_overlapping_one_the_owner_saw_is_flagged_in_the_prompt(
    assembled: Assembled, kind: str, days_ago: int, same_passage: bool, flagged: bool
) -> None:
    db = assembled.db
    vid = await video_id(db, "kCc8FmEb1nY")
    seen = await video_id(db, "zduSFxRajkE")
    if same_passage:
        await _copy_vectors(db, seen, vid)
    await _seen(db, seen, kind, days_ago)

    model = FakeModel(verdict())
    await run_verdict(assembled, vid, model)

    line = 'already seen in "Making LLMs go brrr" (GPU MODE): about 100%'
    assert (line in model.prompts[0]) is flagged


async def test_the_video_itself_is_never_its_own_overlap(assembled: Assembled) -> None:
    db = assembled.db
    vid = await video_id(db, "kCc8FmEb1nY")
    await _seen(db, vid, "open", 1)
    model = FakeModel(verdict())
    await run_verdict(assembled, vid, model)
    assert "already seen" not in model.prompts[0]


# -------------------------------------------------------------- exploration


async def _profile_with_a_negative(db) -> None:
    ops = profile_store.Ops(add=[("Evals", 0.9), ("Launch hype", -0.8)])
    await db.write(lambda c: profile_store.apply(c, ops, actor="owner"))


@pytest.mark.parametrize(
    ("roll", "first", "second", "stored", "explored"),
    [
        # Rolled in, and the rescore reaches 2: shown, flagged.
        (0.05, 1, 2, 2, 1),
        (0.05, 0, 3, 3, 1),
        # Rolled in, but still low without the negatives: the first verdict stands.
        (0.05, 1, 1, 1, 0),
        # Rolled out: one model call.
        (0.5, 1, None, 1, 0),
        # A 2 is never rescored, whatever the roll.
        (0.0, 2, None, 2, 0),
    ],
)
async def test_about_one_low_verdict_in_ten_is_rescored_without_the_negative_entries(
    assembled: Assembled, roll: float, first: int, second: int | None, stored: int, explored: int
) -> None:
    db = assembled.db
    vid = await video_id(db, "kCc8FmEb1nY")
    await _profile_with_a_negative(db)
    answers = [verdict(score=first)] + ([verdict(score=second)] if second is not None else [])
    model = FakeModel(answers=answers)

    job = await run_verdict(assembled, vid, model, FixedRoll(roll))

    assert job["state"] == "done"
    row = await db.read(lambda c: store.get(c, vid))
    assert (row["score"], row["explored"]) == (stored, explored)
    assert len(model.prompts) == (2 if second is not None else 1)
    assert "-0.8  Launch hype" in model.prompts[0]
    # The call log tells the first verdict from its rescore.
    assert model.labels[0] == {"purpose": "verdict", "video_id": vid}
    if second is not None:
        assert model.labels[1] == {"purpose": "verdict_explore", "video_id": vid}
        assert "Launch hype" not in model.prompts[1]
        assert "+0.9  Evals" in model.prompts[1]


@pytest.mark.parametrize(
    "error", [LLMUnavailable("invalid_output"), LLMUnavailable("upstream_rate_limited")]
)
async def test_a_failed_rescore_keeps_the_first_verdict(
    assembled: Assembled, error: LLMUnavailable
) -> None:
    db = assembled.db
    vid = await video_id(db, "kCc8FmEb1nY")
    await _profile_with_a_negative(db)
    model = FakeModel(answers=[verdict(score=1), error])
    job = await run_verdict(assembled, vid, model, FixedRoll(0.0))
    assert job["state"] == "done"
    row = await db.read(lambda c: store.get(c, vid))
    assert (row["score"], row["explored"]) == (1, 0)


async def test_a_profile_without_negative_entries_never_explores(assembled: Assembled) -> None:
    db = assembled.db
    vid = await video_id(db, "kCc8FmEb1nY")
    await db.write(
        lambda c: profile_store.apply(c, profile_store.Ops(add=[("Evals", 0.9)]), actor="owner")
    )
    model = FakeModel(answers=[verdict(score=0)])
    await run_verdict(assembled, vid, model, FixedRoll(0.0))
    assert len(model.prompts) == 1


async def test_a_rerun_without_exploration_clears_the_flag(assembled: Assembled) -> None:
    db = assembled.db
    vid = await video_id(db, "kCc8FmEb1nY")
    await _profile_with_a_negative(db)
    await run_verdict(
        assembled, vid, FakeModel(answers=[verdict(score=1), verdict(score=2)]), FixedRoll(0.0)
    )
    await run_verdict(assembled, vid, FakeModel(verdict(score=3)))
    row = await db.read(lambda c: store.get(c, vid))
    assert (row["score"], row["explored"]) == (3, 0)


# ---------------------------------------------------------- failure isolation


@pytest.mark.parametrize(
    ("error", "job_state", "item_state", "code"),
    [
        (LLMUnavailable("invalid_output"), "failed", "failed", "E_VERDICT_INVALID"),
        # Retryable: the item goes back on the queue and the job waits out the backoff.
        (LLMUnavailable("upstream_rate_limited", retry_after_s=60), "queued", "queued", None),
    ],
)
async def test_a_model_failure_writes_no_verdict(
    assembled: Assembled, error: Exception, job_state: str, item_state: str, code: str | None
) -> None:
    vid = await video_id(assembled.db, "kCc8FmEb1nY")
    job = await run_verdict(assembled, vid, FakeModel(error))
    item = (await rows(assembled.db, "SELECT * FROM job_items WHERE job_id = ?", (job["id"],)))[0]
    assert (job["state"], item["state"], item["error_code"]) == (job_state, item_state, code)
    assert await assembled.db.read(lambda c: store.get(c, vid)) is None


async def test_a_queued_verdict_does_not_refuse_a_reindex_of_its_video(
    assembled: Assembled,
) -> None:
    db = assembled.db
    vid = await video_id(db, "kCc8FmEb1nY")
    await db.write(lambda c: store.queue(c, vid))
    job = await db.write(
        lambda c: jobs_store.create_job(c, "reindex", {}, [("https://youtu.be/kCc8FmEb1nY", vid)])
    )
    assert job.startswith("job_")


async def test_indexing_queues_a_verdict_after_ready_and_runs_before_it(
    settings: Settings, clip: Path
) -> None:
    parts = await harness(settings, clip)
    try:
        parts.parts.runner.pipeline.queue_verdicts = True
        model = FakeModel(verdict())
        parts.parts.runner.handlers["verdict"] = VerdictStage(parts.deps, model, "api:fake")
        index_job = await parts.index(url=VIDEO_URL)
        assert await parts.run() is True
        jobs = await parts.rows("SELECT public_id, kind, state, priority FROM jobs ORDER BY id")
        assert [(j["kind"], j["state"]) for j in jobs] == [("index", "done"), ("verdict", "queued")]
        assert jobs[0]["public_id"] == index_job
        assert jobs[1]["priority"] > jobs[0]["priority"]

        assert await parts.run() is True
        video = await parts.one(
            "SELECT id, index_state FROM videos WHERE source_id = 'aB3dEfG7hIj'"
        )
        assert video["index_state"] == "ready"
        assert await parts.one("SELECT score FROM verdicts WHERE video_id = ?", (video["id"],))
    finally:
        await parts.db.close()
        parts.parts.auth.close()


async def test_a_verdict_queue_failure_never_fails_the_index(
    settings: Settings, clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(conn, video_id):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store, "queue_after_ready", broken)
    parts = await harness(settings, clip)
    try:
        parts.parts.runner.pipeline.queue_verdicts = True
        await parts.index(url=VIDEO_URL)
        assert await parts.run() is True
        jobs = await parts.rows("SELECT kind, state FROM jobs")
        assert [(j["kind"], j["state"]) for j in jobs] == [("index", "done")]
        video = await parts.one("SELECT index_state FROM videos WHERE source_id = 'aB3dEfG7hIj'")
        assert video["index_state"] == "ready"
    finally:
        await parts.db.close()
        parts.parts.auth.close()


async def test_a_verdict_queued_while_on_is_skipped_once_off(
    settings: Settings, clip: Path
) -> None:
    parts = await harness(settings, clip)
    try:
        await parts.index(url=VIDEO_URL)
        await parts.run()
        vid = int((await parts.one("SELECT id FROM videos"))["id"])
        await parts.db.write(lambda c: store.queue(c, vid))
        assert await parts.run() is True
        item = await parts.one(
            "SELECT i.state, i.error_code FROM job_items i JOIN jobs j ON j.id = i.job_id "
            "WHERE j.kind = 'verdict'"
        )
        assert (item["state"], item["error_code"]) == ("skipped", "E_VERDICTS_OFF")
        # And the video was not re-indexed as if the job were an index job.
        assert len(await parts.rows("SELECT 1 FROM job_items WHERE state = 'done'")) == 1
    finally:
        await parts.db.close()
        parts.parts.auth.close()


# ---------------------------------------------------------- when to queue


async def test_ready_queues_again_only_when_the_stored_receipts_broke(assembled: Assembled) -> None:
    db = assembled.db
    vid = await video_id(db, "kCc8FmEb1nY")
    cue = (await rows(db, "SELECT id, start_s FROM cues WHERE video_id = ? ORDER BY seq", (vid,)))[
        0
    ]
    await run_verdict(
        assembled,
        vid,
        FakeModel(verdict({"cue_id": cue["id"], "offset_s": float(cue["start_s"]), "why": "w"})),
    )
    assert await db.write(lambda c: store.queue_after_ready(c, vid)) is None
    # A reindex rewrote the transcript: the stored moment's cue is gone.
    await db.write(lambda c: c.execute("DELETE FROM cues WHERE id = ?", (cue["id"],)))
    assert await db.write(lambda c: store.queue_after_ready(c, vid)) is not None
    # Queued once, not twice.
    assert await db.write(lambda c: store.queue_after_ready(c, vid)) is None


async def test_backfill_is_bounded_and_resumable(assembled: Assembled) -> None:
    db = assembled.db
    total = len(await rows(db, "SELECT id FROM videos WHERE index_state IN ('ready','stale')"))
    assert total >= 3
    first, waiting = await db.write(lambda c: store.backfill(c, 2))
    assert (len(first), waiting) == (2, total - 2)
    rest, waiting = await db.write(lambda c: store.backfill(c, 100))
    assert (len(rest), waiting) == (total - 2, 0)
    assert await db.write(lambda c: store.backfill(c, 100)) == ([], 0)
    # An explicit video is queued even once it has a verdict — after its job finished.
    vid = await video_id(db, "kCc8FmEb1nY")
    await db.write(lambda c: c.execute("UPDATE jobs SET state = 'done'"))
    await db.write(
        lambda c: store.save(
            c, vid, score=1, reason="r", summary="s", moments=[], profile_rev=0, model="m"
        )
    )
    again, _ = await db.write(lambda c: store.backfill(c, 10, [vid]))
    assert len(again) == 1


# ------------------------------------------------------------ input bounds


def test_the_transcript_keeps_whole_lines_from_both_ends_within_budget() -> None:
    lines = [f"line {i:03d} " + "x" * 40 for i in range(100)]
    out = middle_lines(lines, 1000)
    assert len(out) <= 1000 + 40
    kept = out.splitlines()
    assert kept[0] == lines[0] and kept[-1] == lines[-1]
    assert all(line in lines for line in kept if not line.startswith("[…"))
    assert middle_lines(lines[:3], 1000) == "\n".join(lines[:3])


@pytest.mark.parametrize(
    ("env", "on"),
    [
        ({}, False),
        ({"VIDTHEQUE_LLM_BASE_URL": "http://llm", "VIDTHEQUE_LLM_MODEL": "m"}, True),
        (
            {
                "VIDTHEQUE_LLM_BASE_URL": "http://llm",
                "VIDTHEQUE_LLM_MODEL": "m",
                "VIDTHEQUE_VERDICTS": "0",
            },
            False,
        ),
        ({"VIDTHEQUE_LLM_BACKEND": "claude-code"}, True),
    ],
)
async def test_verdicts_are_off_unless_the_model_is_configured(
    assembled: Assembled, monkeypatch: pytest.MonkeyPatch, env: dict, on: bool
) -> None:
    for key in (
        "VIDTHEQUE_LLM_BACKEND",
        "VIDTHEQUE_LLM_BASE_URL",
        "VIDTHEQUE_LLM_MODEL",
        "VIDTHEQUE_VERDICTS",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    stage, http = build_verdicts(assembled.deps)
    assert (stage is not None) == on
    if http is not None:
        await http.aclose()


def test_backfill_takes_a_video_id_that_starts_with_a_dash(monkeypatch, tmp_path: Path) -> None:
    from vidtheque_mcp.verdicts import cli

    db_path = tmp_path / "v.db"
    db_path.touch()
    seen: list[list[str]] = []

    async def fake_backfill(settings, limit, videos):
        seen.append(videos)
        return 0

    monkeypatch.setattr(
        cli.Settings, "from_env", staticmethod(lambda: type("S", (), {"db_path": db_path})())
    )
    monkeypatch.setattr(cli, "configured", lambda: object())
    monkeypatch.setattr(cli, "_backfill", fake_backfill)
    assert cli.main(["backfill", "--video", "-AbC123", "--video=-XyZ", "--video", "plain"]) == 0
    assert seen == [["-AbC123", "-XyZ", "plain"]]
