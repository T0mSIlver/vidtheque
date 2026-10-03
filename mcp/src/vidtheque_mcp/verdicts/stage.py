"""What a `verdict` job executes — companion.md §3.2.

The input is bounded here, whatever an agent might pass elsewhere:
`video-summary`'s own assembly (title, channel, chapters, speakers, key texts,
each under that tool's clamps), the transcript middle-truncated to
`TRANSCRIPT_CHARS`, and the live profile entries. The model answers JSON
against `VERDICT_SCHEMA`; the moments then go through the receipt check.
"""

from __future__ import annotations

import logging
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
from . import store

logger = logging.getLogger(__name__)

# About 10k tokens: a one-hour talk whole, the two ends of anything longer.
TRANSCRIPT_CHARS = 40_000

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
A negative weight means the person wants less of that."""

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

    def __init__(self, deps: Deps, model: Model, label: str) -> None:
        self.deps = deps
        self.model = model
        self.label = label

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

        prompt, rev = await self._prompt(video_id, str(row["public_id"]))
        try:
            answer = await self.model.complete(prompt, system=SYSTEM, schema=VERDICT_SCHEMA)
        except LLMUnavailable as exc:
            raise _as_failure(exc) from None

        moments = [
            store.Moment(int(m["cue_id"]), float(m["offset_s"]), str(m["why"]))
            for m in answer["moments"]
        ]

        def write(c: sqlite3.Connection) -> list[store.Moment]:
            kept, dropped = store.check_receipts(c, video_id, moments)
            store.save(
                c,
                video_id,
                score=int(answer["score"]),
                reason=str(answer["reason"]),
                summary=str(answer["summary"]),
                moments=kept,
                profile_rev=rev,
                model=self.label,
            )
            return dropped

        dropped = await db.write(write)
        if dropped:
            await ctx.log(
                f"dropped {len(dropped)} moment(s) that failed the receipt check: "
                + ", ".join(f"cue {m.cue_id} @ {m.offset_s:g}s" for m in dropped),
                "warn",
            )

    async def _prompt(self, video_id: int, public_id: str) -> tuple[str, int]:
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

        def read(c: sqlite3.Connection) -> tuple[list[sqlite3.Row], int, list[sqlite3.Row]]:
            cues = c.execute(
                "SELECT id, start_s, end_s, text FROM cues WHERE video_id = ? ORDER BY seq",
                (video_id,),
            ).fetchall()
            return profile_store.entries(c), profile_store.revision(c), cues

        entries, rev, cues = await self.deps.db.read(read)
        profile = (
            "\n".join(f"{float(e['weight']):+.1f}  {e['text']}" for e in entries)
            or "(empty: score on general interest and say so in the reason)"
        )
        transcript = middle_lines(
            [f"[cue {c['id']} {float(c['start_s']):.1f}–{float(c['end_s']):.1f}] {c['text']}" for c in cues],
            TRANSCRIPT_CHARS,
        )
        prompt = (
            f"Interest profile (weight, entry):\n{profile}\n\n"
            f"Video:\n{summary_text}\n\n"
            f"Transcript:\n{transcript or '(no transcript)'}"
        )
        return prompt, rev


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
