"""What a `follow_check` job actually executes.

One flat listing per watched tab, every candidate judged in the order that puts
the cheapest rule first, a ledger row for each decision, and one ordinary
`index` job for whatever survived. Nothing here talks to the worker and nothing
here downloads: the whole check is listing requests plus, at most, a handful of
probes.

**It is a job for one reason above the others.** YouTube pushes back — bot
checks and rate limits are ordinary operating conditions on this box — and
`jobs/runner.py` already carries the classification, the ninety-minute cool-off
that the measured block waves needed, the attempt ceiling and crash recovery. A
follow checked from a timer beside the queue would have had to reinvent all of
it, and would have been invisible on the jobs page while it did. So a check is
a job, it defers like every other job, and the index job it enqueues is an
ordinary index job that happens to name its follow.

**Publication order is the allocation rule.** Due follows are checked oldest
clock first, and within a check the candidates are judged oldest upload first,
so the shared daily budget is spent by whoever has been waiting longest rather
than by whoever sorts first.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Sequence

from ..jobs import store as jobs_store
from ..jobs.runner import ItemContext, ItemFailed
from ..pipeline.sources import (
    PlaylistEntry,
    RateLimited,
    SourceError,
    is_indexable_url,
    source_ref_of,
)
from ..text import duration_clock
from . import store
from .rules import MAX_LISTING, Candidate, Rules, judge, judge_duration, needs_duration

logger = logging.getLogger(__name__)

# How many candidates one check may probe for a duration the listing withheld.
# A probe is one request against a source that rate-limits, and the point of
# reading the listing at all is to not spend fifty of them. Anything left
# unjudged is simply not recorded, so the next check reconsiders it — and the
# count is logged, because a check that quietly looked at less than it listed
# would be the silent narrowing this codebase refuses everywhere else.
MAX_PROBES_PER_CHECK = 5


@dataclass
class Outcome:
    """What one check did, for the job log and the tests."""

    seen: int = 0
    queued: int = 0
    held: int = 0
    skipped: int = 0
    already: int = 0
    probed: int = 0
    unjudged: int = 0
    job_public_id: str | None = None

    def sentence(self) -> str:
        parts = [f"{self.seen} seen"]
        if self.queued:
            parts.append(f"{self.queued} queued")
        if self.held:
            parts.append(f"{self.held} held")
        if self.already:
            parts.append(f"{self.already} already indexed")
        if self.skipped:
            parts.append(f"{self.skipped} passed over")
        if self.probed:
            parts.append(f"{self.probed} needed a probe")
        return " · ".join(parts)


class FollowCheck:
    """The check, built once beside the indexing pipeline and given its seam."""

    def __init__(self, db: Any, source: Any, *, daily_budget_s: float) -> None:
        self.db = db
        self.source = source
        self.daily_budget_s = daily_budget_s

    async def run(self, ctx: ItemContext) -> Outcome:
        collection_id = await self.db.read(lambda c: _collection_of(c, ctx.job_id))
        if collection_id is None:
            raise ItemFailed(
                "E_BAD_PARAM",
                "this follow_check job names no follow; nothing to check.",
                retryable=False,
            )
        follow = await self.db.read(lambda c: store.get(c, collection_id))
        if follow is None:
            # Unfollowed between the enqueue and the claim. Not a failure —
            # there is simply nothing to check any more, and a job that says so
            # is better than one that fails with a foreign key.
            return Outcome()
        rules = Rules.from_row(follow)
        await ctx.record("fetch", 0.0)

        entries = await self._listings(ctx, follow, rules)
        outcome = await self._decide(ctx, follow, rules, entries)

        await self.db.write(lambda c: store.schedule_next(c, collection_id, rules.check_interval_s))
        await self.db.write(store.link_landed)
        if outcome.queued:
            await self.db.write(lambda c: store.note_arrivals(c, collection_id))
        await self.db.write(lambda c: store.record_error(c, collection_id, None, None))
        await ctx.log(outcome.sentence(), "info", stage="fetch")
        if outcome.unjudged:
            await ctx.log(
                f"{outcome.unjudged} candidate(s) left undecided — this check's probe "
                f"budget ({MAX_PROBES_PER_CHECK}) was spent. They are reconsidered on "
                "the next check, not dropped.",
                "warn",
                stage="fetch",
            )
        return outcome

    # ------------------------------------------------------------- listings

    async def _listings(
        self, ctx: ItemContext, follow: sqlite3.Row, rules: Rules
    ) -> list[tuple[str, PlaylistEntry]]:
        """One flat extraction per watched tab, tagged with the tab it came from.

        The tab is appended to the channel URL and `expand` is left to do what
        it already does: `sources.YtDlpSource.expand` only defaults a channel
        URL to `/videos` when it does not already end in a tab, so
        `.../@name/streams` passes through untouched and no new parameter has
        to cross the `Source` protocol.
        """
        base = str(follow["source_url"]).rstrip("/")
        kind = "playlist" if str(follow["kind"]) == "playlist" else "channel_recent"
        collected: list[tuple[str, PlaylistEntry]] = []
        claimed: set[str] = set()
        tabs = ("videos",) if kind == "playlist" else rules.tabs
        for tab in tabs:
            target = base if kind == "playlist" else f"{base}/{tab}"
            try:
                entries = await _to_thread(self.source.expand, target, kind, MAX_LISTING)
            except RateLimited as exc:
                raise ItemFailed(
                    "E_RATE_LIMIT",
                    f"{target}: {exc}",
                    retryable=True,
                    retry_after_s=getattr(exc, "retry_after_s", None),
                ) from exc
            except SourceError as exc:
                # The channel itself is the problem — renamed, deleted, or
                # never a channel. That is what `failing` means, and it is set
                # here rather than by the queue's backoff because no amount of
                # waiting fixes it.
                await self.db.write(
                    lambda c: store.set_state(c, int(follow["collection_id"]), "failing")
                )
                await self.db.write(
                    lambda c: store.record_error(
                        c, int(follow["collection_id"]), "E_UNSUPPORTED_SOURCE", str(exc)[:400]
                    )
                )
                raise ItemFailed("E_UNSUPPORTED_SOURCE", str(exc), retryable=False) from exc
            for entry in entries:
                # Revalidate every child, for the same reason `_maybe_expand`
                # does: these URLs arrive from a remote extractor.
                if not (entry.source_id and is_indexable_url(entry.url)):
                    continue
                # One video, one candidate, however many tabs listed it. A past
                # broadcast appears on both /videos and /streams, and
                # `tabs=videos,streams` is exactly the combination the tab rule
                # exists to serve — so without this the ledger showed one row
                # (`record_seen` upserts) while the index job carried two items
                # for the same URL. Two GPU passes, invisible from the Following
                # page. The partial unique index cannot catch it either: both
                # items have a NULL `video_id` at that point. First tab wins,
                # which is the order the operator wrote them in.
                if entry.source_id in claimed:
                    continue
                claimed.add(str(entry.source_id))
                collected.append((tab, entry))
        return collected

    # ------------------------------------------------------------ decisions

    async def _decide(
        self,
        ctx: ItemContext,
        follow: sqlite3.Row,
        rules: Rules,
        listed: Sequence[tuple[str, PlaylistEntry]],
    ) -> Outcome:
        collection_id = int(follow["collection_id"])
        followed_at = int(follow["created_at"] or 0)
        outcome = Outcome()
        seen = await self.db.read(lambda c: store.seen_ids(c, collection_id))

        candidates: list[tuple[int, Candidate]] = []
        for rank, (tab, entry) in enumerate(listed):
            source_id = str(entry.source_id)
            prior = seen.get(source_id)
            # A decision already taken is not retaken. The two exceptions are
            # the two that are explicitly provisional: budget holds are waiting
            # for the window to free, and nothing else here is waiting for
            # anything.
            if prior is not None and str(prior["decision"]) != "held_budget":
                continue
            candidates.append(
                (
                    rank,
                    Candidate(
                        source_id=source_id,
                        url=entry.url,
                        title=entry.title,
                        duration_s=entry.duration_s,
                        published_at=entry.published_at,
                        tab=tab,
                    ),
                )
            )
        outcome.seen = len(candidates)

        # Oldest upload first: the budget below is spent in publication order.
        candidates.sort(key=lambda pair: (pair[1].published_at or 0, -pair[0]))

        # `judged_from` rides with the candidate from here on. It used to be a
        # local in the loop below and was only ever passed to `_record` on the
        # paths that reject or hold — so a candidate that *cost a probe* and
        # then passed was written with the parameter default, `listing`. That
        # is a false receipt on exactly the rows an operator asks about, and
        # migration 0006 promises the opposite ("the surface prints which, so a
        # check that spent requests says so").
        accepted: list[tuple[Candidate, str]] = []
        pending: list[Candidate] = []
        for rank, candidate in candidates:
            verdict = judge(rules, candidate)
            if verdict is None and not _in_horizon(
                candidate, rank, rules, followed_at, first_check=not seen
            ):
                verdict = _horizon_verdict(rules, first_check=not seen)
            if verdict is not None:
                await self._record(collection_id, candidate, verdict.decision, verdict.reason)
                outcome.skipped += 1
                continue
            existing = await self.db.read(lambda c: _indexed(c, candidate.url))
            if existing is not None:
                await self._record(
                    collection_id,
                    candidate,
                    "already_indexed",
                    f"already in the corpus as {existing['public_id']}",
                    video_id=int(existing["id"]),
                )
                outcome.already += 1
                continue
            pending.append(candidate)

        for candidate in pending:
            judged_from = "listing"
            measured = candidate
            if needs_duration(rules, candidate):
                if outcome.probed >= MAX_PROBES_PER_CHECK:
                    outcome.unjudged += 1
                    outcome.seen -= 1
                    continue
                measured = await self._probe(candidate)
                outcome.probed += 1
                judged_from = "probe"
            verdict = judge_duration(rules, measured.duration_s)
            if verdict is not None:
                await self._record(
                    collection_id,
                    measured,
                    verdict.decision,
                    verdict.reason,
                    judged_from=judged_from,
                )
                if verdict.decision.startswith("held"):
                    outcome.held += 1
                else:
                    outcome.skipped += 1
                continue
            if len(accepted) >= rules.max_per_check:
                await self._record(
                    collection_id,
                    measured,
                    "held_budget",
                    f"this check's limit of {rules.max_per_check} was already spent; "
                    "it is first in line next time",
                    judged_from=judged_from,
                )
                outcome.held += 1
                continue
            accepted.append((measured, judged_from))

        return await self._enqueue(ctx, follow, rules, accepted, outcome)

    async def _enqueue(
        self,
        ctx: ItemContext,
        follow: sqlite3.Row,
        rules: Rules,
        accepted: list[tuple[Candidate, str]],
        outcome: Outcome,
    ) -> Outcome:
        collection_id = int(follow["collection_id"])
        if not accepted:
            return outcome

        spent = await self.db.read(store.budget_spent_s)
        queueing: list[tuple[Candidate, str]] = []
        for candidate, judged_from in accepted:
            cost = float(candidate.duration_s or 0.0)
            if rules.mode == "review":
                await self._record(
                    collection_id,
                    candidate,
                    "held_review",
                    "this follow holds its arrivals for you",
                    judged_from=judged_from,
                )
                outcome.held += 1
                continue
            if self.daily_budget_s > 0 and spent + cost > self.daily_budget_s:
                await self._record(
                    collection_id,
                    candidate,
                    "held_budget",
                    f"{duration_clock(cost)} would take today past the "
                    f"{duration_clock(self.daily_budget_s)} budget "
                    f"({duration_clock(spent)} spent) — it is reconsidered on the next check",
                    judged_from=judged_from,
                )
                outcome.held += 1
                continue
            spent += cost
            queueing.append((candidate, judged_from))

        if not queueing:
            return outcome

        args: dict[str, Any] = {
            "expand": "none",
            "max_items": len(queueing),
            "tags": list(rules.tags),
            "force_reindex": False,
            "channels": rules.channels,
            "follow": str(follow["slug"]),
        }
        urls = [candidate.url for candidate, _ in queueing]
        try:
            job_public_id = await self.db.write(
                lambda c: jobs_store.create_job(
                    c,
                    "index",
                    args,
                    [jobs_store.NewItem(url) for url in urls],
                    priority=100,
                    collection_id=collection_id,
                )
            )
        except jobs_store.DuplicateInFlight:
            # Defensive rather than reachable today: the in-flight guard is a
            # partial unique index on `job_items.video_id`, and these items are
            # URLs with no video row yet, so it cannot fire on this path. It is
            # caught anyway because the alternative to catching it is a check
            # that dies mid-ledger, leaving half its decisions recorded.
            await ctx.log(
                "another job already holds one of these videos; nothing queued this check",
                "warn",
                stage="fetch",
            )
            return outcome

        job_row_id = await self.db.read(
            lambda c: c.execute(
                "SELECT id FROM jobs WHERE public_id = ?", (job_public_id,)
            ).fetchone()
        )
        for candidate, judged_from in queueing:
            await self._record(
                collection_id,
                candidate,
                "queued",
                f"queued as {job_public_id}",
                judged_from=judged_from,
                job_id=int(job_row_id["id"]) if job_row_id else None,
            )
        outcome.queued = len(queueing)
        outcome.job_public_id = job_public_id
        await ctx.log(
            f"queued {len(queueing)} new video(s) as {job_public_id}", "info", stage="fetch"
        )
        return outcome

    # ---------------------------------------------------------------- pieces

    async def _probe(self, candidate: Candidate) -> Candidate:
        """One extraction, no download, for the duration the listing withheld."""
        try:
            info = await _to_thread(self.source.probe, candidate.url)
        except RateLimited as exc:
            raise ItemFailed(
                "E_RATE_LIMIT",
                f"{candidate.url}: {exc}",
                retryable=True,
                retry_after_s=getattr(exc, "retry_after_s", None),
            ) from exc
        except SourceError as exc:
            logger.info("probe of %s failed during a follow check: %s", candidate.url, exc)
            return candidate
        duration = info.get("duration")
        try:
            measured = float(duration) if duration is not None else None
        except (TypeError, ValueError):  # pragma: no cover - defensive
            measured = None
        return Candidate(
            source_id=candidate.source_id,
            url=candidate.url,
            title=candidate.title or (info.get("title") or None),
            duration_s=measured if (measured or 0) > 0 else None,
            published_at=candidate.published_at,
            tab=candidate.tab,
        )

    async def _record(
        self,
        collection_id: int,
        candidate: Candidate,
        decision: str,
        reason: str | None,
        *,
        judged_from: str = "listing",
        video_id: int | None = None,
        job_id: int | None = None,
    ) -> None:
        await self.db.write(
            lambda c: store.record_seen(
                c,
                collection_id,
                source_id=candidate.source_id,
                url=candidate.url,
                title=candidate.title,
                duration_s=candidate.duration_s,
                published_at=candidate.published_at,
                tab=candidate.tab,
                decision=decision,
                reason=reason,
                judged_from=judged_from,
                video_id=video_id,
                job_id=job_id,
            )
        )


def _in_horizon(
    candidate: Candidate,
    rank: int,
    rules: Rules,
    followed_at: int,
    *,
    first_check: bool,
) -> bool:
    """Is this upload new since the follow, or inside its backfill allowance?

    The horizon exists for one reason: the first check of a follow must not be
    able to queue two hundred videos of GPU time. It is not a general date
    filter, and after that first check it has no work left to do — a candidate
    the ledger has never seen was not in the previous listing, which is what
    "new" means for a feed.

    That distinction is load-bearing rather than tidy. The rule used to be
    `published_at >= followed_at or rank < backfill`, and a flat listing does
    not always date its entries: on a channel whose extractor returns no
    `timestamp`, a follow at the default `backfill=0` queued **nothing, ever**,
    and filled its ledger with rows claiming the uploads were "published before
    you followed" — which nobody had established. A false receipt, on the one
    table in this schema whose entire purpose is true ones.

    So: a dated upload is judged by its date, an undated one by whether this is
    the first look, and either can still come in on position through `backfill`.
    """
    if candidate.published_at is not None:
        return candidate.published_at >= followed_at or rank < rules.backfill
    return (not first_check) or rank < rules.backfill


def _horizon_verdict(rules: Rules, *, first_check: bool):
    """Why it was passed over, said in terms of what was actually known.

    On a first check the honest claim is about the shelf, not the calendar: the
    upload was already there when the follow was made. Only a dated upload on a
    later check earns the word "published".
    """
    from .rules import Verdict

    spent = (
        f", and your backfill of {rules.backfill} is already spent on newer uploads"
        if rules.backfill
        else " — this follow starts from the day you made it (set a backfill to "
        "reach back)"
    )
    if first_check:
        return Verdict("skipped_horizon", f"already on the channel when you followed{spent}")
    return Verdict("skipped_horizon", f"published before you followed{spent}")


def _collection_of(conn: sqlite3.Connection, job_id: int) -> int | None:
    row = conn.execute(
        "SELECT collection_id, args_json FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:  # pragma: no cover - the claim just happened
        return None
    if row["collection_id"] is not None:
        return int(row["collection_id"])
    try:  # a job written before the column existed, or by hand
        return int(json.loads(row["args_json"])["collection_id"])
    except (ValueError, KeyError, TypeError):
        return None


def _indexed(conn: sqlite3.Connection, url: str) -> sqlite3.Row | None:
    ref = source_ref_of(url)
    if ref is None:  # pragma: no cover - the URL was validated upstream
        return None
    return conn.execute(
        "SELECT id, public_id FROM videos WHERE source = ? AND source_id = ?", ref
    ).fetchone()


async def _to_thread(fn: Any, *args: Any) -> Any:
    import asyncio

    return await asyncio.to_thread(fn, *args)
