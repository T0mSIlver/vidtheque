"""The week's ranking — companion.md §3.4.

A week runs from Monday 00:00 to Sunday 24:00 in the box's local time, keyed by
its Monday as YYYY-MM-DD; a video belongs to the week it was published in.
The verdict's own score stays; the feed's order within a week, and which of
the week's 2+ verdicts show as 3, come from one model call that compares them
all against the profile. At most `WEEK_TOP` a week show as 3.

The ranking runs at the end of a verdict job, once no other verdict waits, for
each recent week whose candidates moved since it was last ranked. A week never
ranked, or whose model is off, reads in the fallback order: score, then the
profile matches, then newest; its 3s are the first `WEEK_TOP` scored 3.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Sequence

from ..llm import LLMUnavailable, Model
from ..profile import store as profile_store
from . import store

if TYPE_CHECKING:
    from ..db import Database

logger = logging.getLogger(__name__)

OWNER_ID = 1
WEEK_TOP = 5
# The prompt and the ranking both stay bounded whatever a week holds.
CANDIDATES_MAX = 40
SUMMARY_CHARS = 600
# Weeks touched by a verdict written this recently are checked for a rerank,
# and at most a year of them is ranked in one pass; the rest wait for the next.
RECENT_S = 8 * 86_400
WEEKS_PER_PASS = 52

# SQLite's week key, the same as `week_of`: forward to Sunday, back to Monday.
WEEK_SQL = "date({col}, 'unixepoch', 'localtime', 'weekday 0', '-6 days')"

RANK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["order", "top"],
    "properties": {
        "order": {"type": "array", "maxItems": CANDIDATES_MAX * 2, "items": {"type": "integer"}},
        "top": {"type": "array", "maxItems": CANDIDATES_MAX, "items": {"type": "integer"}},
    },
}

SYSTEM = f"""You rank one week of videos for one person against their interest
profile. Each video already has a verdict: a score (2 watch the moments, 3
watch it whole), a reason, a summary and its moments. Compare them with each
other, not one at a time. Answer with one JSON object and nothing else.

order: every [id] below, best first: the one this person should spend time on
  first. Prefer what is new and specific for them over what repeats another
  video in the list; of two videos making the same point, rank the stronger
  one higher and the other near the end.
top: the ids worth watching whole this week, best first, at most {WEEK_TOP}.
  Fewer is normal and none is fine: only a video clearly above the rest of the
  week for this person. A score of 3 is a hint, not a promise.

A negative weight in the profile means the person wants less of that."""


def week_of(ts: float) -> str:
    """The Monday of the local week holding `ts`, as YYYY-MM-DD."""
    day = datetime.fromtimestamp(ts).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def bounds(week: str) -> tuple[int, int]:
    """[start, end) of the week in epoch seconds, local midnights (DST-aware)."""
    start = datetime.combine(date.fromisoformat(week), datetime.min.time())
    return int(start.timestamp()), int((start + timedelta(days=7)).timestamp())


@dataclass(frozen=True)
class Candidate:
    video_id: int
    public_id: str
    title: str
    channel: str | None
    duration_s: float
    published_at: int
    score: int
    reason: str
    summary: str
    moments: list[store.Moment]
    matches: list[dict[str, Any]]
    judged_at: int
    explored: bool = False

    @property
    def pull(self) -> int:
        """The profile matches, up minus down, by strength."""
        return sum(int(m["strength"]) * (1 if m["direction"] == "up" else -1) for m in self.matches)


@dataclass(frozen=True)
class Ranked:
    candidate: Candidate
    rank: int
    top: bool

    @property
    def tier(self) -> int:
        """What the feed shows: 3 for the week's top, else 2."""
        return 3 if self.top else 2


def candidates(conn: sqlite3.Connection, week: str) -> list[Candidate]:
    """The week's 2+ verdicts, at most `CANDIDATES_MAX`, highest score and newest first."""
    start, end = bounds(week)
    rows = conn.execute(
        "SELECT v.id, v.public_id, v.title, v.channel_name, v.duration_s, v.published_at,"
        " d.score, d.reason, d.summary, d.moments, d.matches, d.created_at, d.explored FROM verdicts d"
        " JOIN videos v ON v.id = d.video_id WHERE v.owner_id = ? AND d.score >= 2"
        " AND v.published_at >= ? AND v.published_at < ?"
        " ORDER BY d.score DESC, v.published_at DESC, v.id DESC LIMIT ?",
        (OWNER_ID, start, end, CANDIDATES_MAX),
    ).fetchall()
    return [
        Candidate(
            video_id=int(r["id"]),
            public_id=str(r["public_id"]),
            title=str(r["title"] or ""),
            channel=r["channel_name"],
            duration_s=float(r["duration_s"] or 0),
            published_at=int(r["published_at"]),
            score=int(r["score"]),
            reason=str(r["reason"]),
            summary=str(r["summary"]),
            moments=store.moments_of(r),
            matches=json.loads(r["matches"]),
            judged_at=int(r["created_at"]),
            explored=bool(r["explored"]),
        )
        for r in rows
    ]


def fingerprint(cands: Sequence[Candidate]) -> str:
    return json.dumps(sorted([c.video_id, c.judged_at] for c in cands))


def fallback(cands: Sequence[Candidate]) -> list[Ranked]:
    ordered = sorted(cands, key=lambda c: (-c.score, -c.pull, -c.published_at, -c.video_id))
    keep = {c.video_id for c in [c for c in ordered if c.score == 3][:WEEK_TOP]}
    return [Ranked(c, i + 1, c.video_id in keep) for i, c in enumerate(ordered)]


def view(conn: sqlite3.Connection, week: str) -> list[Ranked]:
    """The week's candidates in rank order, the stored ranking first.

    A candidate the stored ranking does not name (judged since, or the rerank
    failed) follows the ranked ones in fallback order, and is never a 3.
    """
    cands = candidates(conn, week)
    stored = {
        int(r[0]): (int(r[1]), bool(r[2]))
        for r in conn.execute(
            "SELECT video_id, rank, top FROM week_ranks WHERE week = ?", (week,)
        )
    }
    if not stored:
        return fallback(cands)
    ranked = sorted((c for c in cands if c.video_id in stored), key=lambda c: stored[c.video_id][0])
    rest = [r.candidate for r in fallback([c for c in cands if c.video_id not in stored])]
    return [
        Ranked(c, i + 1, c.video_id in stored and stored[c.video_id][1])
        for i, c in enumerate([*ranked, *rest])
    ]


def tiers(conn: sqlite3.Connection, video_ids: Sequence[int]) -> dict[int, Ranked]:
    """Each named video's place in its week, for those that are candidates."""
    rows = conn.execute(
        f"SELECT DISTINCT {WEEK_SQL.format(col='published_at')} FROM videos"
        " WHERE published_at IS NOT NULL AND id IN (SELECT value FROM json_each(?))",
        (json.dumps(list(video_ids)),),
    ).fetchall()
    out: dict[int, Ranked] = {}
    for (week,) in rows:
        for r in view(conn, str(week)):
            out[r.candidate.video_id] = r
    return out


def pending(conn: sqlite3.Connection, now: float) -> list[str]:
    """Recent weeks whose candidates moved since they were last ranked, newest
    first, at most `WEEKS_PER_PASS`. The cap applies after the check, so weeks
    already ranked never crowd out the ones that still need it."""
    weeks = [
        str(r[0])
        for r in conn.execute(
            f"SELECT DISTINCT {WEEK_SQL.format(col='v.published_at')} AS w FROM verdicts d"
            " JOIN videos v ON v.id = d.video_id WHERE v.owner_id = ?"
            " AND v.published_at IS NOT NULL AND d.created_at >= ? ORDER BY w DESC",
            (OWNER_ID, int(now) - RECENT_S),
        )
    ]
    out: list[str] = []
    for week in weeks:
        run = conn.execute(
            "SELECT candidates, outcome FROM week_rank_runs WHERE week = ?", (week,)
        ).fetchone()
        if run is None or run["outcome"] != "ok" or run["candidates"] != fingerprint(candidates(conn, week)):
            out.append(week)
            if len(out) >= WEEKS_PER_PASS:
                break
    return out


def save(conn: sqlite3.Connection, week: str, order: Sequence[int], top: set[int], cands_fp: str, model: str) -> None:
    conn.execute("DELETE FROM week_ranks WHERE week = ?", (week,))
    conn.executemany(
        "INSERT INTO week_ranks (video_id, week, rank, top) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (video_id) DO UPDATE SET week = excluded.week, rank = excluded.rank,"
        " top = excluded.top, ranked_at = unixepoch()",
        [(vid, week, i + 1, int(vid in top)) for i, vid in enumerate(order)],
    )
    _run(conn, week, "ok", cands_fp, model)


def _run(conn: sqlite3.Connection, week: str, outcome: str, cands_fp: str, model: str) -> None:
    conn.execute(
        "INSERT INTO week_rank_runs (week, candidates, outcome, model) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (week) DO UPDATE SET candidates = excluded.candidates,"
        " outcome = excluded.outcome, model = excluded.model, ran_at = unixepoch()",
        (week, cands_fp, outcome, model),
    )


def settle(answer: dict[str, Any], cands: Sequence[Candidate]) -> tuple[list[int], set[int]]:
    """The model's order and top, held to the candidates: unknown ids dropped,
    the missing appended in fallback order, at most `WEEK_TOP` on top, and the
    top ranked above the rest."""
    known = {c.video_id for c in cands}
    order: list[int] = []
    for vid in answer["order"]:
        if int(vid) in known and int(vid) not in order:
            order.append(int(vid))
    order += [r.candidate.video_id for r in fallback(cands) if r.candidate.video_id not in order]
    top: list[int] = []
    for vid in answer["top"]:
        if int(vid) in known and int(vid) not in top:
            top.append(int(vid))
    top = top[:WEEK_TOP]
    picked = set(top)
    return [*top, *(v for v in order if v not in picked)], picked


def prompt(entries: Sequence[tuple[int, float, str]], cands: Sequence[Candidate]) -> str:
    profile = "\n".join(f"{w:+.1f}  {t}" for _, w, t in entries) or "(empty)"
    lines = []
    for c in cands:
        asked = store.moments_s(c.moments)
        minutes = (
            f"asks {max(1, round(asked / 60))} of {max(1, round(c.duration_s / 60))} min"
            if asked
            else f"{max(1, round(c.duration_s / 60))} min"
        )
        whys = "; ".join(m.why for m in c.moments)
        lines.append(
            f"[{c.video_id}] {c.title} · {c.channel or 'unknown channel'} · {minutes}\n"
            f"  scored {c.score}: {c.reason}\n"
            f"  {c.summary[:SUMMARY_CHARS]}" + (f"\n  moments: {whys}" if whys else "")
        )
    return f"Interest profile (weight, entry):\n{profile}\n\nThis week's videos:\n" + "\n\n".join(lines)


Log = Callable[[str, str], Awaitable[None]]


class WeekRanker:
    """Ranks the weeks a verdict job touched. A failure never fails the verdict."""

    def __init__(self, db: "Database", model: Model, label: str, clock: Callable[[], float] | None = None) -> None:
        self.db = db
        self.model = model
        self.label = label
        self.clock = clock or time.time

    async def rank_pending(self, log: Log) -> None:
        for week in await self.db.read(lambda c: pending(c, self.clock())):
            await self.rank(week, log)

    async def rank(self, week: str, log: Log) -> None:
        def read(c: sqlite3.Connection) -> tuple[list[Candidate], list[tuple[int, float, str]]]:
            entries = [(int(e["id"]), float(e["weight"]), str(e["text"])) for e in profile_store.entries(c)]
            return candidates(c, week), entries

        cands, entries = await self.db.read(read)
        fp = fingerprint(cands)
        if len(cands) <= 1:
            # Nothing to compare: a lone video keeps its own score.
            order = [c.video_id for c in cands]
            top = {c.video_id for c in cands if c.score == 3}
            await self.db.write(lambda c: save(c, week, order, top, fp, self.label))
            return
        try:
            answer = await self.model.complete(
                prompt(entries, cands), system=SYSTEM, schema=RANK_SCHEMA, purpose="week_rank"
            )
        except LLMUnavailable as exc:
            await self.db.write(lambda c: _run(c, week, "failed", fp, self.label))
            await log(f"week {week} not ranked: the model failed ({exc.reason})", "warn")
            return
        order, top = settle(answer, cands)
        await self.db.write(lambda c: save(c, week, order, top, fp, self.label))
        await log(f"ranked week {week}: {len(order)} video(s), {len(top)} on top", "info")


# ------------------------------------------------------------------ budget

BUDGET_MAX_MIN = 7 * 24 * 60


def asks_s(ranked: Ranked) -> float:
    """The seconds a candidate asks for: the whole video for a 3, else its
    moments; the whole video when the moments have no spans or there are none."""
    c = ranked.candidate
    if ranked.top:
        return c.duration_s
    spans = store.moments_s(c.moments)
    return spans if spans else c.duration_s


def fit(ranked: Sequence[Ranked], budget_s: float) -> tuple[list[Ranked], list[Ranked]]:
    """(fitted, rest): in rank order, each candidate that still fits the budget.
    One too long is passed over, and a shorter one after it may still fit."""
    fitted: list[Ranked] = []
    rest: list[Ranked] = []
    used = 0.0
    for r in ranked:
        cost = asks_s(r)
        if used + cost <= budget_s:
            fitted.append(r)
            used += cost
        else:
            rest.append(r)
    return fitted, rest


def budget_min(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT week_budget_min FROM owners WHERE id = ?", (OWNER_ID,)).fetchone()[0])


def set_budget_min(conn: sqlite3.Connection, minutes: int) -> None:
    conn.execute("UPDATE owners SET week_budget_min = ? WHERE id = ?", (minutes, OWNER_ID))


def shift(week: str, weeks: int) -> str:
    return (date.fromisoformat(week) + timedelta(days=7 * weeks)).isoformat()


def days(week: str) -> list[str]:
    monday = date.fromisoformat(week)
    return [(monday + timedelta(days=i)).isoformat() for i in range(7)]
