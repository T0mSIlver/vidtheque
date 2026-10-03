"""What a `verdict` job executes — companion.md §3.2.

The input is bounded here, whatever an agent might pass elsewhere:
`video-summary`'s own assembly (title, channel, chapters, speakers, key texts,
each under that tool's clamps), the transcript middle-truncated to
`TRANSCRIPT_CHARS`, the live profile entries, and the seen videos this one
overlaps (`novelty`). The model answers JSON against `VERDICT_SCHEMA`; the
moments then go through the receipt check.

Exploration: a verdict scored 0 or 1 by a profile with negative entries is,
with probability `EXPLORE_RATE`, scored again without them. A rescore of 2 or
more is stored instead, flagged `explored`; anything lower keeps the first.
"""

from __future__ import annotations

import logging
import random
import sqlite3
from dataclasses import dataclass
from typing import Any

import httpx2 as httpx
from mcp_types import TextContent

from ..config import _bool_env
from ..jobs.runner import ItemContext, ItemFailed, ItemSkipped
from ..llm import LLMSettings, LLMUnavailable, Model, build_model
from ..profile import store as profile_store
from ..tools.base import CALL_CONTEXT, CallContext, Deps
from ..tools.library import video_summary
from . import novelty, store

logger = logging.getLogger(__name__)

# About 10k tokens: a one-hour talk whole, the two ends of anything longer.
TRANSCRIPT_CHARS = 40_000

# About one low verdict in ten is rescored without the negative entries.
EXPLORE_RATE = 0.1

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["score", "reason", "summary", "moments"],
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 3},
        "reason": {"type": "string", "minLength": 1, "maxLength": 300},
        "summary": {"type": "string", "minLength": 1, "maxLength": 2000},
        "moments": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["cue_id", "offset_s", "why"],
                "properties": {
                    "cue_id": {"type": "integer"},
                    "offset_s": {"type": "number", "minimum": 0},
                    "why": {"type": "string", "minLength": 1, "maxLength": 300},
                },
            },
        },
    },
}

SYSTEM = """You triage videos for one person, against their interest profile.
Answer with one JSON object and nothing else:
- score: 0 skip, 1 the summary is enough, 2 watch the moments, 3 watch it whole.
- reason: one line naming the profile entries it matched or hit, e.g. "evals ↑, launch hype ↓".
- summary: one paragraph on what the video says.
- moments: up to three, each {cue_id, offset_s, why}. cue_id is a number from the
  transcript's [cue …] markers, and offset_s must lie inside that cue's start–end.
  Use only cues you were shown; give fewer moments rather than guessing.
A negative weight means the person wants less of that.
Lines starting "already seen in" name videos the person opened or asked about
that cover the same ground; score what is new here, not what they have seen."""

# The triage agent's corpus reads are never the owner's signals (§2.3).
TRIAGE = CallContext(client="triage", signals=False)


@dataclass(frozen=True)
class VerdictSettings:
    enabled: bool = True

    @classmethod
    def from_env(cls) -> "VerdictSettings":
        return cls(enabled=_bool_env("VIDTHEQUE_VERDICTS", True))


def model_label(settings: LLMSettings) -> str:
    return f"{settings.backend}:{settings.model or 'default'}"


def configured() -> LLMSettings | None:
    """The model settings when verdicts are on, else None."""
    if not VerdictSettings.from_env().enabled:
        return None
    settings = LLMSettings.from_env()
    if settings.backend == "api" and not (settings.base_url and settings.model):
        return None
    return settings


def build_verdicts(deps: Deps) -> tuple["VerdictStage | None", httpx.AsyncClient | None]:
    """The stage and the HTTP client it owns, or (None, None) when verdicts are off.

    Off unless the companion model is configured, and off when
    `VIDTHEQUE_VERDICTS=0` even then.
    """
    settings = configured()
    if settings is None:
        return None, None
    # Only `api` talks HTTP; the CLI backends never touch the client.
    http = httpx.AsyncClient() if settings.backend == "api" else None
    model = build_model(settings, http)  # type: ignore[arg-type]
    assert model is not None
    return VerdictStage(deps, model, model_label(settings)), http


class VerdictStage:
    """Implements `jobs.runner.Pipeline` for the `verdict` kind."""

    def __init__(
        self, deps: Deps, model: Model, label: str, rng: random.Random | None = None
    ) -> None:
        self.deps = deps
        self.model = model
        self.label = label
        # Injected so tests decide which verdicts explore.
        self.rng = rng or random.Random()

    async def run_item(self, ctx: ItemContext) -> None:
        db = self.deps.db
        video_id = await db.read(lambda c: _video_arg(c, ctx.job_id))
        row = await db.read(
            lambda c: c.execute(
                "SELECT public_id, index_state FROM videos WHERE id = ?", (video_id,)
            ).fetchone()
        )
        if row is None:
            raise ItemSkipped("the video is no longer in the corpus.", "E_UNKNOWN_VIDEO")
        if row["index_state"] not in ("ready", "stale"):
            raise ItemSkipped(
                f"the video is {row['index_state']}, not indexed; no verdict.", "E_NOT_INDEXED"
            )

        inputs = await self._inputs(video_id, str(row["public_id"]))
        answer = await self._ask(inputs.prompt(with_negatives=True))
        explored = False
        if (
            int(answer["score"]) <= 1
            and inputs.has_negatives
            and self.rng.random() < EXPLORE_RATE
        ):
            rescored = await self._ask(inputs.prompt(with_negatives=False))
            if int(rescored["score"]) >= 2:
                answer, explored = rescored, True
            await ctx.log(
                f"explored: rescored {rescored['score']} without the negative entries; "
                + ("kept, shown as outside the profile" if explored else "first verdict kept")
            )

        moments = [
            store.Moment(int(m["cue_id"]), float(m["offset_s"]), str(m["why"]))
            for m in answer["moments"]
        ]

        def write(c: sqlite3.Connection) -> list[store.Moment] | None:
            # The model call is long; the video may have been deleted meanwhile.
            if c.execute("SELECT 1 FROM videos WHERE id = ?", (video_id,)).fetchone() is None:
                return None
            kept, dropped = store.check_receipts(c, video_id, moments)
            store.save(
                c,
                video_id,
                score=int(answer["score"]),
                reason=str(answer["reason"]),
                summary=str(answer["summary"]),
                moments=kept,
                profile_rev=inputs.rev,
                model=self.label,
                explored=explored,
            )
            return dropped

        dropped = await db.write(write)
        if dropped is None:
            raise ItemSkipped("the video was deleted while it was judged.", "E_UNKNOWN_VIDEO")
        if dropped:
            await ctx.log(
                f"dropped {len(dropped)} moment(s) that failed the receipt check: "
                + ", ".join(f"cue {m.cue_id} @ {m.offset_s:g}s" for m in dropped),
                "warn",
            )

    async def _ask(self, prompt: str) -> dict[str, Any]:
        try:
            return await self.model.complete(prompt, system=SYSTEM, schema=VERDICT_SCHEMA)
        except LLMUnavailable as exc:
            raise _as_failure(exc) from None

    async def _inputs(self, video_id: int, public_id: str) -> "Inputs":
        token = CALL_CONTEXT.set(TRIAGE)
        try:
            summary = await video_summary(
                self.deps, public_id, include_links=False, include_guidance=False
            )
        finally:
            CALL_CONTEXT.reset(token)
        if summary.is_error:
            raise ItemSkipped("video-summary refused this video; no verdict.", "E_NOT_INDEXED")
        summary_text = "\n".join(b.text for b in summary.content if isinstance(b, TextContent))

        def read(c: sqlite3.Connection) -> Inputs:
            cues = c.execute(
                "SELECT id, start_s, end_s, text FROM cues WHERE video_id = ? ORDER BY seq",
                (video_id,),
            ).fetchall()
            transcript = middle_lines(
                [
                    f"[cue {q['id']} {float(q['start_s']):.1f}–{float(q['end_s']):.1f}] {q['text']}"
                    for q in cues
                ],
                TRANSCRIPT_CHARS,
            )
            return Inputs(
                entries=[(float(e["weight"]), str(e["text"])) for e in profile_store.entries(c)],
                rev=profile_store.revision(c),
                summary=summary_text,
                seen=novelty.prompt_lines(novelty.seen_overlap(c, video_id)),
                transcript=transcript,
            )

        return await self.deps.db.read(read)


@dataclass(frozen=True)
class Inputs:
    """Everything one verdict's prompt is built from, read once."""

    entries: list[tuple[float, str]]
    rev: int
    summary: str
    seen: str
    transcript: str

    @property
    def has_negatives(self) -> bool:
        return any(weight < 0 for weight, _ in self.entries)

    def prompt(self, *, with_negatives: bool) -> str:
        kept = [(w, t) for w, t in self.entries if with_negatives or w >= 0]
        profile = (
            "\n".join(f"{w:+.1f}  {t}" for w, t in kept)
            or "(empty: score on general interest and say so in the reason)"
        )
        seen = f"Already seen:\n{self.seen}\n\n" if self.seen else ""
        return (
            f"Interest profile (weight, entry):\n{profile}\n\n"
            f"Video:\n{self.summary}\n\n"
            f"{seen}"
            f"Transcript:\n{self.transcript or '(no transcript)'}"
        )


def middle_lines(lines: list[str], budget: int) -> str:
    """Whole lines from both ends within `budget` chars, the middle elided."""
    if sum(len(line) + 1 for line in lines) <= budget:
        return "\n".join(lines)
    half = budget // 2
    head: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > half:
            break
        head.append(line)
        used += len(line) + 1
    tail: list[str] = []
    used = 0
    for line in reversed(lines[len(head):]):
        if used + len(line) + 1 > half:
            break
        tail.append(line)
        used += len(line) + 1
    tail.reverse()
    omitted = len(lines) - len(head) - len(tail)
    return "\n".join([*head, f"[… {omitted} cues omitted …]", *tail])


def _video_arg(conn: sqlite3.Connection, job_id: int) -> int:
    row = conn.execute(
        "SELECT json_extract(args_json, '$.video_id') FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None or row[0] is None:
        raise ItemSkipped("this verdict job names no video.", "E_INVALID_JOB")
    return int(row[0])


def _as_failure(exc: LLMUnavailable) -> ItemFailed:
    if exc.reason == "invalid_output":
        return ItemFailed(
            "E_VERDICT_INVALID", "the model's answer was not a valid verdict.", retryable=False
        )
    if exc.reason == "not_configured":
        return ItemFailed(
            "E_LLM_NOT_CONFIGURED", "the companion model cannot be started.", retryable=False
        )
    return ItemFailed(
        "E_LLM_UNAVAILABLE",
        f"the companion model is unavailable ({exc.reason}).",
        retryable=True,
        retry_after_s=exc.retry_after_s,
    )
