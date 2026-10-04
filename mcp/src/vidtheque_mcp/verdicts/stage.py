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
from typing import TYPE_CHECKING, Any

import httpx2 as httpx
from mcp_types import TextContent

from ..config import _bool_env
from ..jobs.runner import ItemContext, ItemFailed, ItemSkipped
from ..llm import LLMSettings, LLMUnavailable, Model, build_model, is_configured
from ..profile import store as profile_store
from ..tools.base import CALL_CONTEXT, CallContext, Deps
from ..tools.library import video_summary
from . import novelty, store, week

if TYPE_CHECKING:
    from ..push.notify import Notifier

logger = logging.getLogger(__name__)

# About 10k tokens: a one-hour talk whole, the two ends of anything longer.
TRANSCRIPT_CHARS = 40_000
# The cheap verdict, the scout's input: title, channel, chapters and a quarter
# of the transcript (discover/scout.py). The public sample feed uses it.
CHEAP_TRANSCRIPT_CHARS = 12_000
CHEAP_CHAPTERS = 30

# About one low verdict in ten is rescored without the negative entries.
EXPLORE_RATE = 0.1

MATCHES_MAX = 4

# The word limits live in the prompt. These character bounds only catch
# runaway output, far above them (AGENTS.md: never prompt-only limits); an
# answer past one fails as invalid and is rerun with `backfill --video`.
VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["score", "reason", "summary", "moments", "matches"],
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
                "required": ["cue_id", "offset_s", "end_cue_id", "why"],
                "properties": {
                    "cue_id": {"type": "integer"},
                    "offset_s": {"type": "number", "minimum": 0},
                    "end_cue_id": {"type": "integer"},
                    "why": {"type": "string", "minLength": 1, "maxLength": 300},
                },
            },
        },
        "matches": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["entry_id", "strength"],
                "properties": {
                    "entry_id": {"type": "integer"},
                    "strength": {"type": "integer", "minimum": 1, "maximum": 2},
                },
            },
        },
    },
}

SYSTEM = """You triage videos for one person against their interest profile, and
write them a digest they read on a phone. Answer with one JSON object and nothing else.

score, for this person, not the video's general quality:
  0 skip: off-profile, or on a topic they want less of.
  1 the summary is enough: on-topic but ordinary. The default; most videos on
    their topics score 1.
  2 watch the moments: at least one moment they would regret missing, not
    merely relevant. Name it in moments; a 2 with no such moment is a 1.
  3 watch it whole: rare; the whole video is that strong for them.
  When torn between two scores, give the lower.

matches: the profile entries this video hits, by their [id], strongest first,
  at most 4. strength 2 = the entry is central to the video, 1 = it comes up.
  Include negative entries the video hits; they count against it. Use only ids
  from the profile. No entry fits: [].

reason: one plain sentence, never more than 15 words, saying why this score
  for this person. No arrows, no lists of entries, no weights.

summary: never more than 60 words: two sentences of at most 30 words each.
  Count the words of each sentence before answering. If the draft is longer,
  drop the least important point rather than compressing every sentence; a
  third sentence is never allowed. A digest, not a description: names of people,
  tools, models, papers, companies; numbers (benchmarks, sizes, costs,
  latencies); the specific claims and techniques. Lead with the part that
  matters given the profile, in second person where it helps ("the eval
  harness at 31:00 is the part you'd reuse"). Skip what the title already says.

moments: up to three, each {cue_id, offset_s, end_cue_id, why}. cue_id is a
  number from the transcript's [cue …] markers, and offset_s lies inside that
  cue's start–end. end_cue_id is the last cue the moment covers: the moment
  runs from offset_s to that cue's end. Stop where the part worth watching
  stops; the person sees the total ("6 of 42 min"), and a moment that runs on
  costs them time. end_cue_id may equal cue_id, never an earlier cue. Use only
  cues you were shown; give fewer moments rather than guessing.
  why: never more than 12 words, the concrete thing said there ("Kimi K2 beats GLM on
  tau-bench by 9 points"), not a label ("interesting discussion of evals").

Writing rules for reason, summary and why:
- State claims directly. Never "the speaker discusses", "the video explores",
  "this talk covers", "they talk about", "dives into".
- No throat-clearing, no hedging ("arguably", "seems to", "potentially"), no
  closing summary sentence, no verdict on the video's overall value.
- Concrete nouns over abstractions. If a sentence would fit a different video
  unchanged, cut it.
- None of these words: delve, crucial, pivotal, landscape, showcase, highlight,
  underscore, robust, comprehensive, insightful, valuable, notably, additionally.
- No em dashes, no "not just X but Y", no groups of three for rhythm.
- Plain sentences with articles and verbs, not telegraphese.

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
    return settings if is_configured(settings) else None


def build_verdicts(
    deps: Deps, *, sample: bool = False
) -> tuple["VerdictStage | None", httpx.AsyncClient | None]:
    """The stage and the HTTP client it owns, or (None, None) when verdicts are off.

    Off unless the companion model is configured, and off when
    `VIDTHEQUE_VERDICTS=0` even then. `sample` is the public box's feed
    (demo-site.md §8): the cheap input, and no push, week ranking or
    exploration, so one small model call per video.
    """
    settings = configured()
    if settings is None:
        return None, None
    # Only `api` talks HTTP; the CLI backends never touch the client.
    http = httpx.AsyncClient() if settings.backend == "api" else None
    model = build_model(settings, http, deps.db)  # type: ignore[arg-type]
    assert model is not None
    label = model_label(settings)
    if sample:
        return VerdictStage(deps, model, label, explore_rate=0.0, cheap=True), http
    # Push rides on the verdict (companion.md §6): no key, no notifier.
    from ..push.notify import PushSettings, build_notifier

    notifier = None
    if PushSettings.from_env().credentials:
        http = http or httpx.AsyncClient()
        notifier = build_notifier(deps.db, http)
    ranker = week.WeekRanker(deps.db, model, label)
    return VerdictStage(deps, model, label, push=notifier, ranker=ranker), http


class VerdictStage:
    """Implements `jobs.runner.Pipeline` for the `verdict` kind."""

    def __init__(
        self,
        deps: Deps,
        model: Model,
        label: str,
        rng: random.Random | None = None,
        push: "Notifier | None" = None,
        ranker: "week.WeekRanker | None" = None,
        explore_rate: float = EXPLORE_RATE,
        cheap: bool = False,
    ) -> None:
        self.deps = deps
        self.push = push
        self.ranker = ranker
        self.model = model
        self.label = label
        # Injected so tests decide which verdicts explore.
        self.rng = rng or random.Random()
        self.explore_rate = explore_rate
        self.cheap = cheap

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
        answer = await self._ask(inputs.prompt(with_negatives=True), video_id)
        explored = False
        if (
            int(answer["score"]) <= 1
            and inputs.has_negatives
            and self.rng.random() < self.explore_rate
        ):
            try:
                rescored = await self.model.complete(
                    inputs.prompt(with_negatives=False),
                    system=SYSTEM,
                    schema=VERDICT_SCHEMA,
                    purpose="verdict_explore",
                    video_id=video_id,
                )
            except LLMUnavailable as exc:
                # The first verdict is valid; a failed rescore only skips the exploration.
                await ctx.log(f"exploration skipped: the rescore failed ({exc.reason})", "warn")
            else:
                if int(rescored["score"]) >= 2:
                    answer, explored = rescored, True
                await ctx.log(
                    f"explored: rescored {rescored['score']} without the negative entries; "
                    + ("kept, shown as outside the profile" if explored else "first verdict kept")
                )

        answered = [
            (int(m["cue_id"]), float(m["offset_s"]), int(m["end_cue_id"]), str(m["why"]))
            for m in answer["moments"]
        ]
        matches = inputs.matches(answer["matches"])

        def write(c: sqlite3.Connection) -> list[store.Moment] | None:
            # The model call is long; the video may have been deleted meanwhile.
            if c.execute("SELECT 1 FROM videos WHERE id = ?", (video_id,)).fetchone() is None:
                return None
            moments = store.spanned(c, video_id, answered)
            kept, dropped = store.check_receipts(c, video_id, moments)
            store.save(
                c,
                video_id,
                score=int(answer["score"]),
                reason=str(answer["reason"]),
                summary=str(answer["summary"]),
                moments=kept,
                matches=matches,
                profile_rev=inputs.rev,
                model=self.label,
                explored=explored,
                overlaps=inputs.spans,
            )
            return dropped

        dropped = await db.write(write)
        if dropped is None:
            raise ItemSkipped("the video was deleted while it was judged.", "E_UNKNOWN_VIDEO")
        if dropped:
            await ctx.log(
                f"dropped {len(dropped)} moment(s) that failed the receipt check: "
                + ", ".join(f"cue {m.cue_id}–{m.end_cue_id} @ {m.offset_s:g}s" for m in dropped),
                "warn",
            )
        if self.push is not None:
            try:
                reached = await self.push.after_verdict(video_id)
            except Exception as exc:  # noqa: BLE001 - a push never fails the verdict it carries
                await ctx.log(f"push failed: {type(exc).__name__}: {exc}", "warn")
            else:
                if reached:
                    await ctx.log(f"pushed to {reached} phone(s)")
        # Once no other verdict waits, so a backfill ranks each week once (§3.4).
        if self.ranker is not None and not await db.read(_other_verdict_queued):
            try:
                await self.ranker.rank_pending(ctx.log)
            except Exception as exc:  # noqa: BLE001 - a ranking never fails the verdict
                await ctx.log(f"week ranking failed: {type(exc).__name__}: {exc}", "warn")

    async def _ask(self, prompt: str, video_id: int) -> dict[str, Any]:
        try:
            return await self.model.complete(
                prompt, system=SYSTEM, schema=VERDICT_SCHEMA, purpose="verdict", video_id=video_id
            )
        except LLMUnavailable as exc:
            raise _as_failure(exc) from None

    async def _inputs(self, video_id: int, public_id: str) -> "Inputs":
        if self.cheap:
            summary_text = await self.deps.db.read(lambda c: _cheap_summary(c, video_id))
        else:
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
        budget = CHEAP_TRANSCRIPT_CHARS if self.cheap else TRANSCRIPT_CHARS

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
                budget,
            )
            seen = novelty.seen(c, video_id)
            return Inputs(
                entries=[
                    (int(e["id"]), float(e["weight"]), str(e["text"]))
                    for e in profile_store.entries(c)
                ],
                rev=profile_store.revision(c),
                summary=summary_text,
                seen=novelty.prompt_lines(seen.overlaps),
                spans=seen.spans,
                transcript=transcript,
            )

        return await self.deps.db.read(read)


@dataclass(frozen=True)
class Inputs:
    """Everything one verdict's prompt is built from, read once."""

    entries: list[tuple[int, float, str]]
    rev: int
    summary: str
    seen: str
    spans: list[novelty.Span]
    transcript: str

    @property
    def has_negatives(self) -> bool:
        return any(weight < 0 for _, weight, _ in self.entries)

    def matches(self, answered: list[dict[str, Any]]) -> list[store.Match]:
        """The model's matches against the live entries: unknown ids and
        weight-0 entries dropped, one per entry, direction from the weight's
        sign, at most `MATCHES_MAX`, strongest first."""
        weights = {entry_id: weight for entry_id, weight, _ in self.entries}
        kept: dict[int, store.Match] = {}
        for m in answered:
            entry_id = int(m["entry_id"])
            weight = weights.get(entry_id, 0.0)
            if weight == 0 or entry_id in kept:
                continue
            kept[entry_id] = store.Match(entry_id, "up" if weight > 0 else "down", int(m["strength"]))
        ordered = sorted(kept.values(), key=lambda m: -m.strength)
        return ordered[:MATCHES_MAX]

    def prompt(self, *, with_negatives: bool) -> str:
        kept = [(i, w, t) for i, w, t in self.entries if with_negatives or w >= 0]
        profile = (
            "\n".join(f"[{i}] {w:+.1f}  {t}" for i, w, t in kept)
            or "(empty: score on general interest and say so in the reason)"
        )
        seen = f"Already seen:\n{self.seen}\n\n" if self.seen else ""
        return (
            f"Interest profile ([id] weight, entry):\n{profile}\n\n"
            f"Video:\n{self.summary}\n\n"
            f"{seen}"
            f"Transcript:\n{self.transcript or '(no transcript)'}"
        )


def _cheap_summary(conn: sqlite3.Connection, video_id: int) -> str:
    """Title, channel and chapters, as the scout reads a candidate."""
    row = conn.execute(
        "SELECT title, channel_name FROM videos WHERE id = ?", (video_id,)
    ).fetchone()
    chapters = conn.execute(
        "SELECT start_s, title FROM chapters WHERE video_id = ? ORDER BY seq LIMIT ?",
        (video_id, CHEAP_CHAPTERS),
    ).fetchall()
    text = f"Title: {row['title'] or ''}\nChannel: {row['channel_name'] or 'unknown'}"
    if chapters:
        text += "\nChapters:\n" + "\n".join(
            f"  {float(c['start_s']):.0f}s {c['title']}" for c in chapters
        )
    return text


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


def _other_verdict_queued(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM jobs WHERE kind = 'verdict' AND state = 'queued' LIMIT 1").fetchone()
        is not None
    )


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
