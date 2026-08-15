"""Following channels: the rules, the ledger, the budget and the clock.

CPU-only, like the rest of this suite. The source is a hand-written fake that
answers `expand` and `probe` and nothing else — those are the only two methods a
check ever calls — so nothing here reaches YouTube, downloads a byte or wakes a
GPU. The database is a real one on a temp path, because every interesting
question this feature raises ("was the row updated in place?", "did the cascade
take the videos with it?") is a question about SQL.

The headline behaviour, and the reason this file exists at all, is
`test_a_budget_hold_becomes_a_queue_when_the_window_frees`: a budget that
*dropped* candidates would be indistinguishable from a filter, and the whole
point of `held_budget` is that tomorrow it is reconsidered.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Sequence

import pytest

from vidtheque_mcp.db.database import Database
from vidtheque_mcp.errors import ToolError
from vidtheque_mcp.follows import params, store
from vidtheque_mcp.follows.check import MAX_PROBES_PER_CHECK, FollowCheck, Outcome
from vidtheque_mcp.follows.rules import (
    DEFAULT_CHECK_INTERVAL_S,
    MAX_LISTING,
    Candidate,
    Rules,
    describe,
    judge,
    judge_duration,
    needs_duration,
    normalize_tabs,
    split_terms,
)
from vidtheque_mcp.follows.scheduler import CHECK_PRIORITY, enqueue_due
from vidtheque_mcp.jobs import store as jobs_store
from vidtheque_mcp.jobs.runner import ItemContext, ItemFailed
from vidtheque_mcp.pipeline.sources import PlaylistEntry, RateLimited, SourceError

CHANNEL = "https://www.youtube.com/@gpumode"


def now() -> int:
    return int(time.time())


# --------------------------------------------------------------------- fakes


def entry(
    source_id: str,
    *,
    title: str | None = "a talk",
    duration_s: float | None = None,
    published_at: int | None = None,
) -> PlaylistEntry:
    """One row of a flat listing, the way `playlist_entries` would have built it."""
    return PlaylistEntry(
        url=f"https://youtu.be/{source_id}",
        source_id=source_id,
        title=title,
        duration_s=duration_s,
        published_at=published_at,
    )


class FakeChannel:
    """A channel with canned tab listings. Two methods, because a check calls two.

    `expand` keys off the last path segment, which is exactly how the real seam
    behaves: `FollowCheck._listings` appends `/videos`, `/streams` or `/shorts`
    to the channel URL and leaves `YtDlpSource.expand` to pass it through.
    """

    def __init__(
        self,
        *,
        videos: Sequence[PlaylistEntry] = (),
        streams: Sequence[PlaylistEntry] = (),
        shorts: Sequence[PlaylistEntry] = (),
        durations: dict[str, float | None] | None = None,
        expand_raises: Exception | None = None,
        probe_raises: Exception | None = None,
    ) -> None:
        self.listings: dict[str, list[PlaylistEntry]] = {
            "videos": list(videos),
            "streams": list(streams),
            "shorts": list(shorts),
        }
        self.durations = durations or {}
        self.expand_raises = expand_raises
        self.probe_raises = probe_raises
        self.expanded: list[str] = []
        self.asked_for: list[int] = []
        self.probed: list[str] = []

    def expand(self, url: str, kind: str, max_items: int) -> list[PlaylistEntry]:
        self.expanded.append(url)
        self.asked_for.append(max_items)
        if self.expand_raises is not None:
            raise self.expand_raises
        tab = url.rsplit("/", 1)[-1]
        return list(self.listings.get(tab, ()))[:max_items]

    def probe(self, url: str) -> dict[str, Any]:
        self.probed.append(url)
        if self.probe_raises is not None:
            raise self.probe_raises
        return {"duration": self.durations.get(url)}


# ------------------------------------------------------------------ plumbing


@pytest.fixture
async def db(tmp_path: Path):
    """A migrated database and nothing else — no server, no runner, no worker."""
    database = Database(path=tmp_path / "data" / "vidtheque.db")
    await database.open()
    try:
        yield database
    finally:
        await database.close()


async def make_follow(
    db: Database,
    *,
    title: str = "GPU MODE",
    source_url: str | None = None,
    kind: str = "channel",
    **rule_fields: Any,
) -> int:
    """One follow. A distinct URL per title unless the caller names one.

    `collections_one_follow_per_source` (migration 0006) makes one follow per
    source URL a schema guarantee, so a test wanting four follows is a test
    wanting four channels — which is what it always meant, since two follows of
    one URL would each spend the shared daily budget on the same uploads.
    """
    if source_url is None:
        source_url = CHANNEL if title == "GPU MODE" else f"{CHANNEL}-{_slug(title)}"
    return await db.write(
        lambda c: store.create(
            c, title=title, source_url=source_url, kind=kind, rules=Rules(**rule_fields)
        )
    )


def _slug(title: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in title).strip("-")


async def backdate_follow(db: Database, collection_id: int, seconds: int) -> None:
    """Make the follow older than the uploads it is about to be shown.

    `_in_horizon` compares an upload's publication against the moment you
    followed, so a follow created microseconds ago rejects everything as
    published-before-you-followed unless it is aged first.
    """
    await db.write(
        lambda c: c.execute(
            "UPDATE follows SET created_at = unixepoch() - ? WHERE collection_id = ?",
            (seconds, collection_id),
        )
    )


async def one(db: Database, sql: str, args: tuple = ()) -> sqlite3.Row | None:
    return await db.read(lambda c: c.execute(sql, args).fetchone())


async def rows(db: Database, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    return await db.read(lambda c: c.execute(sql, args).fetchall())


async def ledger(db: Database, collection_id: int) -> dict[str, sqlite3.Row]:
    found = await rows(
        db, "SELECT * FROM follow_seen WHERE collection_id = ? ORDER BY id", (collection_id,)
    )
    return {str(row["source_id"]): row for row in found}


async def events(db: Database) -> list[tuple[str, str]]:
    found = await rows(db, "SELECT level, message FROM job_events ORDER BY id")
    return [(str(row["level"]), str(row["message"])) for row in found]


async def add_video(db: Database, source_id: str, title: str = "an old talk") -> int:
    return await db.write(
        lambda c: int(
            c.execute(
                "INSERT INTO videos (source_id, url, title, index_state) "
                "VALUES (?, ?, ?, 'ready')",
                (source_id, f"https://youtu.be/{source_id}", title),
            ).lastrowid
            or 0
        )
    )


async def check_context(db: Database, collection_id: int) -> ItemContext:
    """The `follow_check` job and item a claim would have handed the check."""
    follow = await one(db, "SELECT * FROM collections WHERE id = ?", (collection_id,))
    assert follow is not None
    public_id = await db.write(
        lambda c: jobs_store.create_job(
            c,
            "follow_check",
            {"follow": str(follow["slug"]), "collection_id": collection_id},
            [jobs_store.NewItem(str(follow["source_url"]))],
            priority=CHECK_PRIORITY,
            collection_id=collection_id,
        )
    )
    item = await one(
        db,
        "SELECT i.id AS item_id, i.job_id AS job_id FROM job_items i "
        "JOIN jobs j ON j.id = i.job_id WHERE j.public_id = ?",
        (public_id,),
    )
    assert item is not None
    return ItemContext(
        db=db,
        job_id=int(item["job_id"]),
        job_public_id=public_id,
        item_id=int(item["item_id"]),
        source_url=str(follow["source_url"]),
        video_id=None,
        kind="follow_check",
    )


async def run_check(
    db: Database, collection_id: int, source: FakeChannel, *, daily_budget_s: float = 0.0
) -> Outcome:
    ctx = await check_context(db, collection_id)
    return await FollowCheck(db, source, daily_budget_s=daily_budget_s).run(ctx)


# ============================================================== rules: tabs


def test_a_candidate_from_an_unwatched_tab_is_told_which_tabs_are_watched() -> None:
    """A rejection that does not name the rule is a rule the operator cannot fix."""
    rules = Rules(tabs=("videos", "streams"))
    verdict = judge(rules, Candidate(source_id="a" * 11, url="u", title="clip", tab="shorts"))
    assert verdict is not None
    assert verdict.decision == "skipped_tab"
    assert "/shorts" in verdict.reason
    assert "/videos" in verdict.reason and "/streams" in verdict.reason


def test_a_watched_tab_passes_the_tab_rule() -> None:
    watched = Candidate(source_id="a" * 11, url="u", tab="videos")
    assert judge(Rules(tabs=("videos",)), watched) is None


def test_normalize_tabs_orders_and_deduplicates_and_never_empties() -> None:
    """The stored order is canonical, so two follows with the same policy read alike."""
    assert normalize_tabs("shorts,videos,videos") == ("videos", "shorts")
    assert normalize_tabs("") == ("videos",)
    assert normalize_tabs("clips") == ("videos",)


def test_split_terms_is_the_one_parser_for_every_comma_list() -> None:
    assert split_terms(" a , ,b ") == ["a", "b"]
    assert split_terms(None) == []


# ============================================================= rules: titles


def test_an_excluded_title_loses_even_when_it_is_also_included() -> None:
    """A title the operator named twice is a title they meant to refuse.

    Exclude is asked first on purpose, so "keep the talks, never the reruns"
    does not quietly keep a rerun that happens to say "talk".
    """
    rules = Rules(title_include=("talk",), title_exclude=("rerun",))
    verdict = judge(rules, Candidate(source_id="a" * 11, url="u", title="Talk rerun"))
    assert verdict is not None
    assert verdict.decision == "skipped_title"
    assert "'rerun'" in verdict.reason


def test_a_title_matching_nothing_you_asked_for_is_refused_and_says_what_you_asked_for() -> None:
    rules = Rules(title_include=("paged", "kernel"))
    verdict = judge(rules, Candidate(source_id="a" * 11, url="u", title="Community update"))
    assert verdict is not None
    assert verdict.decision == "skipped_title"
    assert "'paged'" in verdict.reason and "'kernel'" in verdict.reason


def test_title_matching_ignores_case_in_both_directions() -> None:
    """The terms are plain substrings, and neither half of the comparison is trusted to be lower."""
    lower_rule = Rules(title_include=("paged",))
    shouted = Candidate(source_id="a" * 11, url="u", title="PAGED ATTENTION")
    assert judge(lower_rule, shouted) is None
    upper_rule = Rules(title_include=("PAGED",))
    quiet = Candidate(source_id="a" * 11, url="u", title="paged attention")
    assert judge(upper_rule, quiet) is None
    excluding = Rules(title_exclude=("RERUN",))
    verdict = judge(excluding, Candidate(source_id="a" * 11, url="u", title="a rerun"))
    assert verdict is not None and verdict.decision == "skipped_title"


def test_a_missing_title_is_not_a_match_for_an_include_rule() -> None:
    """A listing that withheld the title must not smuggle a video past the filter."""
    untitled = Candidate(source_id="a" * 11, url="u", title=None)
    verdict = judge(Rules(title_include=("paged",)), untitled)
    assert verdict is not None and verdict.decision == "skipped_title"


# =========================================================== rules: duration


def test_a_video_under_the_floor_is_refused_with_both_numbers_in_the_sentence() -> None:
    """The ledger is receipts, not opinions: "too short" is unarguable and useless."""
    verdict = judge_duration(Rules(min_duration_s=480), 252.0)
    assert verdict is not None
    assert verdict.decision == "skipped_duration"
    assert "0:04:12" in verdict.reason  # what it was
    assert "0:08:00" in verdict.reason  # what you asked for
    assert "floor" in verdict.reason


def test_a_video_over_the_ceiling_is_refused_with_both_numbers_in_the_sentence() -> None:
    verdict = judge_duration(Rules(max_duration_s=3600), 7200.0)
    assert verdict is not None
    assert verdict.decision == "skipped_duration"
    assert "2:00:00" in verdict.reason and "1:00:00" in verdict.reason
    assert "ceiling" in verdict.reason


def test_a_video_inside_the_window_passes() -> None:
    assert judge_duration(Rules(min_duration_s=480, max_duration_s=7200), 3600.0) is None
    # The bounds are inclusive: exactly your floor is not shorter than your floor.
    assert judge_duration(Rules(min_duration_s=480), 480.0) is None
    assert judge_duration(Rules(max_duration_s=480), 480.0) is None


def test_no_length_rule_means_no_opinion_about_length() -> None:
    assert judge_duration(Rules(), 1.0) is None
    assert judge_duration(Rules(), None) is None


def test_an_unmeasurable_video_is_held_for_you_rather_than_guessed_at() -> None:
    """The deliberate call: guessing "too short" loses a talk, guessing "fine" spends the GPU.

    So an unknown duration under a length rule is `held_review` — a decision
    that costs a human ten seconds — and never `skipped_duration`, which costs
    them a video they never hear about.
    """
    verdict = judge_duration(Rules(min_duration_s=480), None)
    assert verdict is not None
    assert verdict.decision == "held_review"
    assert not verdict.decision.startswith("skipped")
    assert "duration" in verdict.reason


def test_a_probe_is_only_worth_making_when_it_could_change_the_answer() -> None:
    """One probe is one request against a source that blocks boxes for asking too fast."""
    assert needs_duration(Rules(min_duration_s=480), Candidate("a" * 11, "u")) is True
    measured = Candidate("a" * 11, "u", duration_s=90.0)
    assert needs_duration(Rules(min_duration_s=480), measured) is False
    assert needs_duration(Rules(), Candidate("a" * 11, "u")) is False


# ============================================================ rules: describe


def test_the_default_follow_reads_as_one_plain_sentence() -> None:
    """The detail page states a policy, because a policy is what is being checked."""
    assert describe(Rules(), name="GPU MODE") == (
        "Every 6 hours, take up to 5 new uploads from GPU MODE on /videos, "
        "index all three channels."
    )


def test_a_length_rule_and_a_title_rule_read_as_clauses_of_that_sentence() -> None:
    sentence = describe(
        Rules(
            tabs=("videos", "streams"),
            min_duration_s=480,
            max_duration_s=7200,
            title_include=("kernel",),
            title_exclude=("rerun",),
            channels="transcript",
            tags=("topic:gpu",),
            max_per_check=1,
        ),
        name="GPU MODE",
    )
    assert sentence.startswith(
        "Every 6 hours, take up to 1 new upload from GPU MODE on /videos, /streams"
    )
    assert "between 0:08:00 and 2:00:00 long" in sentence
    assert "titled like 'kernel'" in sentence
    assert "never 'rerun'" in sentence
    assert "index transcript only" in sentence
    assert "tag topic:gpu" in sentence
    assert sentence.endswith(".")


def test_review_mode_says_so_in_the_sentence() -> None:
    """A follow that queues nothing must not read like one that queues everything."""
    sentence = describe(Rules(mode="review", check_interval_s=86_400), name="GPU MODE")
    assert sentence.startswith("Every day,")
    assert "hold them for you rather than queueing them" in sentence


def test_a_one_sided_length_rule_reads_one_sided() -> None:
    assert "longer than 0:08:00" in describe(Rules(min_duration_s=480), name="X")
    assert "shorter than 1:00:00" in describe(Rules(max_duration_s=3600), name="X")


# ============================================================== build_rules


def test_a_duration_may_be_seconds_or_a_clock_because_the_offset_axis_already_is() -> None:
    """Adding an `8m` spelling would invent a third notation for a two-notation contract."""
    assert params.build_rules(min_duration=480).min_duration_s == 480
    assert params.build_rules(min_duration="8:00").min_duration_s == 480
    assert params.build_rules(max_duration="1:30:00").max_duration_s == 5400


def test_a_floor_above_the_ceiling_is_refused_for_saying_so() -> None:
    """Clamping this one would silently change what the operator asked for."""
    with pytest.raises(ToolError) as caught:
        params.build_rules(min_duration="1:00:00", max_duration="8:00")
    assert caught.value.code == "E_BAD_PARAM"
    assert "nothing could ever match" in caught.value.message


def test_an_unknown_tab_is_refused_rather_than_dropped() -> None:
    """Silently narrowing to /videos would be a follow watching less than it was told to."""
    with pytest.raises(ToolError) as caught:
        params.build_rules(tabs="videos,clips")
    assert caught.value.code == "E_BAD_PARAM"
    assert "clips" in caught.value.message


def test_a_channels_value_outside_index_videos_vocabulary_is_refused() -> None:
    """`channels` is `index-video`'s word, verbatim; a second vocabulary is a drift."""
    with pytest.raises(ToolError) as caught:
        params.build_rules(channels="captions")
    assert "'captions'" in caught.value.message


def test_a_tag_outside_the_known_namespaces_is_refused() -> None:
    with pytest.raises(ToolError) as caught:
        params.build_rules(tags="bogus:thing")
    assert caught.value.code == "E_BAD_PARAM"


def test_a_mode_that_is_neither_auto_nor_review_is_refused() -> None:
    with pytest.raises(ToolError) as caught:
        params.build_rules(mode="silent")
    assert "auto" in caught.value.message and "review" in caught.value.message


def test_a_check_interval_under_fifteen_minutes_is_refused_not_clamped() -> None:
    """The floor protects the box's IP, so it is a refusal the caller has to see."""
    with pytest.raises(ToolError) as caught:
        params.build_rules(check_interval_s=899)
    assert caught.value.code == "E_BAD_PARAM"
    assert "900" in caught.value.message


def test_a_check_interval_past_a_week_is_clamped_because_the_intent_is_legible() -> None:
    """Too rare is not dangerous — it is just not a follow any more."""
    assert params.build_rules(check_interval_s=30 * 86_400).check_interval_s == 7 * 86_400
    assert params.build_rules(check_interval_s=900).check_interval_s == 900
    assert params.build_rules().check_interval_s == DEFAULT_CHECK_INTERVAL_S


def test_backfill_and_max_per_check_are_clamped_by_the_server_never_by_the_prompt() -> None:
    """A limit a model can talk its way past is not a limit."""
    rules = params.build_rules(backfill=999, max_per_check=999)
    assert (rules.backfill, rules.max_per_check) == (25, 25)
    floors = params.build_rules(backfill=-5, max_per_check=0)
    assert (floors.backfill, floors.max_per_check) == (0, 1)


def test_a_non_numeric_clamp_is_an_error_because_it_is_a_typo_not_an_extreme() -> None:
    with pytest.raises(ToolError):
        params.build_rules(backfill="lots")


def test_rule_columns_round_trips_through_a_follow_row() -> None:
    """The tool writes columns and the check reads a row; they must mean one thing."""
    rules = params.build_rules(
        tabs="shorts,videos",
        min_duration="8:00",
        title_exclude="rerun, teaser",
        tags="topic:gpu",
        mode="review",
    )
    columns = params.rule_columns(rules)
    assert columns["tabs"] == "videos,shorts"
    assert columns["title_exclude"] == "rerun, teaser"
    assert Rules.from_row(columns | {"max_duration_s": None, "next_check_at": 0}) == rules


# =================================================================== store


async def test_following_a_channel_writes_the_collection_and_the_rules_together(db) -> None:
    collection_id = await make_follow(db, tabs=("videos", "streams"), min_duration_s=480)
    row = await one(db, "SELECT * FROM collections WHERE id = ?", (collection_id,))
    assert row is not None
    assert (row["kind"], row["slug"], row["source_url"]) == ("channel", "gpu-mode", CHANNEL)
    # `sync_cron` stays NULL: the interval lives in `check_interval_s`.
    assert row["sync_cron"] is None

    follow = await one(db, "SELECT * FROM follows WHERE collection_id = ?", (collection_id,))
    assert follow is not None
    assert follow["state"] == "active"
    assert follow["tabs"] == "videos,streams"
    assert follow["min_duration_s"] == 480
    # Due immediately: a follow you just made should tell you what it found.
    assert abs(int(follow["next_check_at"]) - now()) <= 5


async def test_two_channels_with_one_name_do_not_collide_on_the_slug(db) -> None:
    """`collections.slug` is NOT NULL UNIQUE and predates this feature — it bites."""
    first = await make_follow(db, title="GPU MODE", source_url=CHANNEL)
    second = await make_follow(db, title="GPU MODE", source_url="https://www.youtube.com/@gpumode2")
    slugs = [str(row["slug"]) for row in await rows(db, "SELECT slug FROM collections ORDER BY id")]
    assert slugs == ["gpu-mode", "gpu-mode-2"]
    assert first != second


async def test_re_deciding_a_candidate_keeps_when_you_first_saw_it(db) -> None:
    """"How long did the budget hold this up" is only answerable if one row says both.

    `first_seen_at` is the arrival and `decided_at` is the verdict, and a
    re-decision moves exactly one of them.
    """
    collection_id = await make_follow(db)
    await record(db, collection_id, "vid00000001", decision="held_budget", reason="waiting")
    await db.write(
        lambda c: c.execute(
            "UPDATE follow_seen SET first_seen_at = unixepoch() - 900, "
            "decided_at = unixepoch() - 900"
        )
    )
    before = (await ledger(db, collection_id))["vid00000001"]

    await record(db, collection_id, "vid00000001", decision="queued", reason="queued as job_x")

    after = (await ledger(db, collection_id))["vid00000001"]
    assert len(await rows(db, "SELECT id FROM follow_seen")) == 1
    assert after["id"] == before["id"]
    assert after["decision"] == "queued"
    assert after["reason"] == "queued as job_x"
    assert after["first_seen_at"] == before["first_seen_at"]
    assert after["decided_at"] > before["decided_at"]


async def test_a_re_decision_never_forgets_a_number_it_already_had(db) -> None:
    """A probe that answered once must not be paid for twice."""
    collection_id = await make_follow(db)
    await record(db, collection_id, "vid00000001", decision="held_budget", duration_s=3600.0)
    await record(db, collection_id, "vid00000001", decision="queued", duration_s=None)
    row = (await ledger(db, collection_id))["vid00000001"]
    assert row["duration_s"] == 3600.0


async def test_the_daily_budget_counts_accepted_video_and_only_inside_the_window(db) -> None:
    """A budget that counted holds would spend itself on videos it never took."""
    collection_id = await make_follow(db)
    await record(db, collection_id, "vid00000001", decision="queued", duration_s=3600.0)
    await record(db, collection_id, "vid00000002", decision="held_budget", duration_s=7200.0)
    await record(db, collection_id, "vid00000003", decision="skipped_duration", duration_s=60.0)
    await record(db, collection_id, "vid00000004", decision="queued", duration_s=1800.0)
    await db.write(
        lambda c: c.execute(
            "UPDATE follow_seen SET decided_at = unixepoch() - 90000 WHERE source_id = ?",
            ("vid00000004",),
        )
    )
    assert await db.read(store.budget_spent_s) == 3600.0


async def test_only_active_follows_whose_clock_has_come_round_are_due_oldest_first(db) -> None:
    """Oldest first is what makes one shared budget fair between many follows."""
    waited_longest = await make_follow(db, title="A")
    waited_less = await make_follow(db, title="B")
    not_yet = await make_follow(db, title="C")
    paused = await make_follow(db, title="D")
    await arm(db, waited_longest, -600)
    await arm(db, waited_less, -60)
    await arm(db, not_yet, +600)
    await arm(db, paused, -9000)
    await db.write(lambda c: store.set_state(c, paused, "paused"))

    due = await db.read(lambda c: store.due(c, 10))
    assert [int(row["collection_id"]) for row in due] == [waited_longest, waited_less]


async def test_a_paused_follow_does_not_owe_a_week_of_checks(db) -> None:
    """Resuming re-arms to now: the catch-up burst is what the budget exists to prevent."""
    collection_id = await make_follow(db)
    await arm(db, collection_id, -7 * 86_400)
    await db.write(lambda c: store.set_state(c, collection_id, "paused"))
    await db.write(
        lambda c: store.record_error(c, collection_id, "E_UNSUPPORTED_SOURCE", "gone")
    )

    await db.write(lambda c: store.set_state(c, collection_id, "active"))

    follow = await one(db, "SELECT * FROM follows WHERE collection_id = ?", (collection_id,))
    assert follow is not None
    assert follow["state"] == "active"
    assert abs(int(follow["next_check_at"]) - now()) <= 5
    # Resuming also clears the last complaint; a resumed follow is not failing.
    assert follow["last_error_code"] is None and follow["last_error_message"] is None


async def test_check_now_wakes_an_active_follow_and_leaves_every_other_state_alone(db) -> None:
    """`failing` is here for the same reason as `paused`, and is easier to get wrong.

    `due()` enqueues active follows only, so arming anything else sets a clock
    nobody reads — and the caller then prints "due now" about a check that can
    never be queued. That is a false receipt, on the feature whose whole design
    is receipts that are true. A channel that 404'd once leaves the follow
    `failing`, and "check now" is exactly the gesture an operator makes after
    fixing the URL, so it has to say that resume is what re-arms it.
    """
    active = await make_follow(db, title="A")
    paused = await make_follow(db, title="B")
    failing = await make_follow(db, title="C")
    for collection_id in (active, paused, failing):
        await arm(db, collection_id, +9000)
    await db.write(lambda c: store.set_state(c, paused, "paused"))
    await db.write(lambda c: store.set_state(c, failing, "failing"))

    for collection_id in (active, paused, failing):
        await db.write(lambda c: store.check_now(c, collection_id))

    due = await db.read(lambda c: store.due(c, 10))
    assert [int(row["collection_id"]) for row in due] == [active]
    # And the clock of the two it left alone was not touched at all.
    for collection_id in (paused, failing):
        row = await db.read(lambda c: store.get(c, collection_id))
        assert int(row["next_check_at"]) > 0


async def test_a_landed_video_is_joined_to_the_ledger_row_it_came_from(db) -> None:
    """A candidate is queued before the video row exists, so the loop closes afterwards."""
    collection_id = await make_follow(db)
    await record(db, collection_id, "vid00000001", decision="queued", duration_s=3600.0)
    await record(db, collection_id, "vid00000002", decision="skipped_duration", duration_s=60.0)
    assert await db.write(store.link_landed) == 0  # nothing has landed yet

    video_id = await add_video(db, "vid00000001")

    assert await db.write(store.link_landed) == 1
    row = (await ledger(db, collection_id))["vid00000001"]
    assert row["video_id"] == video_id
    members = await rows(db, "SELECT * FROM collection_videos")
    assert [(int(m["collection_id"]), int(m["video_id"])) for m in members] == [
        (collection_id, video_id)
    ]
    # Idempotent, because the check simply runs it every time.
    assert await db.write(store.link_landed) == 0
    assert len(await rows(db, "SELECT * FROM collection_videos")) == 1


async def test_unfollowing_takes_the_membership_and_keeps_the_corpus(db) -> None:
    """The videos a follow brought in are corpus, not membership — they stay."""
    collection_id = await make_follow(db)
    await record(db, collection_id, "vid00000001", decision="queued", duration_s=3600.0)
    video_id = await add_video(db, "vid00000001")
    await db.write(store.link_landed)
    job_id = await db.write(
        lambda c: jobs_store.create_job(
            c, "index", {}, [jobs_store.NewItem("https://youtu.be/vid00000001")],
            collection_id=collection_id,
        )
    )

    await db.write(lambda c: store.delete(c, collection_id))

    assert await rows(db, "SELECT * FROM follows") == []
    assert await rows(db, "SELECT * FROM follow_seen") == []
    assert await rows(db, "SELECT * FROM collection_videos") == []
    assert len(await rows(db, "SELECT id FROM videos WHERE id = ?", (video_id,))) == 1
    job = await one(db, "SELECT * FROM jobs WHERE public_id = ?", (job_id,))
    assert job is not None and job["collection_id"] is None


async def test_membership_can_be_written_directly_and_twice_is_still_once(db) -> None:
    """Attaching what a follow already owns must never be an error the caller handles."""
    collection_id = await make_follow(db)
    video_id = await add_video(db, "vid00000001")
    await db.write(lambda c: store.attach_videos(c, collection_id, [video_id]))
    await db.write(lambda c: store.attach_videos(c, collection_id, [video_id]))
    assert len(await rows(db, "SELECT * FROM collection_videos")) == 1


async def test_the_following_page_puts_what_is_broken_at_the_top(db) -> None:
    """A failing follow is the only row that needs the operator, so it goes first."""
    quiet = await make_follow(db, title="Quiet", source_url=f"{CHANNEL}1")
    busy = await make_follow(db, title="Busy", source_url=f"{CHANNEL}2")
    broken = await make_follow(db, title="Broken", source_url=f"{CHANNEL}3")
    await db.write(lambda c: store.set_state(c, broken, "failing"))
    await db.write(lambda c: store.schedule_next(c, busy, 60))

    listed = await db.read(lambda c: store.list_follows(c, limit=10))
    assert [str(row["title"]) for row in listed] == ["Broken", "Busy", "Quiet"]
    # `limit + 1` rows, because these surfaces answer `has_more`, not a total.
    assert len(await db.read(lambda c: store.list_follows(c, limit=2))) == 3


async def test_a_follow_is_found_by_slug_by_url_or_by_what_a_human_typed(db) -> None:
    collection_id = await make_follow(db, title="GPU MODE")
    for needle in ("gpu-mode", CHANNEL, "gpu mode", "GPU"):
        row = await db.read(lambda c, n=needle: store.find(c, n))
        assert row is not None and int(row["collection_id"]) == collection_id
    assert await db.read(lambda c: store.find(c, "nobody")) is None


async def test_the_ledger_and_the_header_band_count_the_same_rows(db) -> None:
    collection_id = await make_follow(db)
    await record(db, collection_id, "vid00000001", decision="queued", duration_s=3600.0)
    await record(db, collection_id, "vid00000002", decision="held_budget", duration_s=7200.0)
    await record(db, collection_id, "vid00000003", decision="held_review", duration_s=None)
    await record(db, collection_id, "vid00000004", decision="skipped_title")

    assert await db.read(lambda c: store.counts(c, collection_id)) == {
        "queued": 1,
        "held_budget": 1,
        "held_review": 1,
        "skipped_title": 1,
    }
    totals = await db.read(store.totals)
    assert totals["follows"] == 1 and totals["active"] == 1
    assert totals["brought_in"] == 1
    assert totals["held"] == 2  # both kinds of hold, because both wait on something

    waiting = await db.read(lambda c: store.held(c, 10))
    assert [str(row["source_id"]) for row in waiting] == ["vid00000003"]
    assert str(waiting[0]["follow_slug"]) == "gpu-mode"
    budgeted = await db.read(lambda c: store.held_for_budget(c, 10))
    assert [str(row["source_id"]) for row in budgeted] == ["vid00000002"]

    page = await db.read(lambda c: store.seen_page(c, collection_id, decisions=("queued",)))
    assert [str(row["source_id"]) for row in page] == ["vid00000001"]
    assert set(await db.read(lambda c: store.seen_ids(c, collection_id))) == {
        "vid00000001",
        "vid00000002",
        "vid00000003",
        "vid00000004",
    }


async def test_the_ledger_page_asks_for_one_more_row_than_it_shows(db) -> None:
    """`has_more` over an exact total, like every other list on these surfaces."""
    collection_id = await make_follow(db)
    for n in range(4):
        await record(db, collection_id, f"vid0000000{n}", decision="queued")
    assert len(await db.read(lambda c: store.seen_page(c, collection_id, limit=2))) == 3


async def test_updating_the_rules_touches_only_the_named_columns(db) -> None:
    collection_id = await make_follow(db, max_per_check=5)
    await db.write(
        lambda c: store.update_rules(c, collection_id, {"max_per_check": 2, "mode": "review"})
    )
    follow = await one(db, "SELECT * FROM follows WHERE collection_id = ?", (collection_id,))
    assert follow is not None
    assert (follow["max_per_check"], follow["mode"]) == (2, "review")
    assert follow["tabs"] == "videos"
    with pytest.raises(ValueError):
        await db.write(lambda c: store.update_rules(c, collection_id, {"state": "paused"}))


async def test_a_follows_own_jobs_are_separable_from_the_ones_it_enqueued(db) -> None:
    """"Which check found this video" is a plain query, not a LIKE against args_json."""
    collection_id = await make_follow(db)
    check_id = await db.write(
        lambda c: jobs_store.create_job(
            c, "follow_check", {}, [jobs_store.NewItem(CHANNEL)],
            priority=CHECK_PRIORITY, collection_id=collection_id,
        )
    )
    index_id = await db.write(
        lambda c: jobs_store.create_job(
            c, "index", {}, [jobs_store.NewItem("https://youtu.be/vid00000001")],
            collection_id=collection_id,
        )
    )
    checks = await db.read(lambda c: store.recent_checks(c, collection_id))
    assert [str(row["public_id"]) for row in checks] == [check_id]
    indexes = await db.read(lambda c: store.index_jobs(c, collection_id))
    assert [str(row["public_id"]) for row in indexes] == [index_id]
    assert await db.read(lambda c: store.check_in_flight(c, collection_id)) is not None


# ------------------------------------------------------------ store helpers


async def record(db: Database, collection_id: int, source_id: str, **fields: Any) -> None:
    payload: dict[str, Any] = {
        "url": f"https://youtu.be/{source_id}",
        "title": "a talk",
        "duration_s": None,
        "published_at": None,
        "tab": "videos",
        "reason": None,
    }
    payload.update(fields)
    await db.write(
        lambda c: store.record_seen(c, collection_id, source_id=source_id, **payload)
    )


async def arm(db: Database, collection_id: int, offset_s: int) -> None:
    await db.write(
        lambda c: c.execute(
            "UPDATE follows SET next_check_at = unixepoch() + ? WHERE collection_id = ?",
            (offset_s, collection_id),
        )
    )


# ================================================================== the check


async def test_one_listing_becomes_a_ledger_that_explains_every_decision(db) -> None:
    """Nothing is dropped silently — a passed-over upload leaves a row with the number on it."""
    collection_id = await make_follow(
        db, min_duration_s=480, title_exclude=("rerun",), max_per_check=10
    )
    await backdate_follow(db, collection_id, 3600)
    await add_video(db, "vid00000003")
    day = now() - 600
    source = FakeChannel(
        videos=[
            entry("vid00000001", title="Paged attention", duration_s=3600.0, published_at=day),
            entry("vid00000002", title="Community update", duration_s=240.0, published_at=day),
            entry("vid00000003", title="Fused softmax", duration_s=3600.0, published_at=day),
            entry("vid00000004", title="Live Q&A rerun", duration_s=3600.0, published_at=day),
        ]
    )

    outcome = await run_check(db, collection_id, source)

    assert source.expanded == [f"{CHANNEL}/videos"]
    assert (outcome.seen, outcome.queued, outcome.already, outcome.skipped) == (4, 1, 1, 2)
    rows_by_id = await ledger(db, collection_id)
    assert rows_by_id["vid00000001"]["decision"] == "queued"
    assert rows_by_id["vid00000002"]["decision"] == "skipped_duration"
    assert "0:04:00" in str(rows_by_id["vid00000002"]["reason"])
    assert "0:08:00" in str(rows_by_id["vid00000002"]["reason"])
    assert rows_by_id["vid00000003"]["decision"] == "already_indexed"
    assert "vid00000003" in str(rows_by_id["vid00000003"]["reason"])
    assert rows_by_id["vid00000003"]["video_id"] is not None
    assert rows_by_id["vid00000004"]["decision"] == "skipped_title"
    assert "'rerun'" in str(rows_by_id["vid00000004"]["reason"])
    # The listing answered every length question, so no request was spent asking again.
    assert source.probed == []


async def test_an_accepted_set_becomes_one_index_job_that_names_its_follow(db) -> None:
    """One job, not one per video: the queue's ordering is the follow's ordering."""
    collection_id = await make_follow(
        db, max_per_check=10, channels="transcript", tags=("topic:gpu",)
    )
    await backdate_follow(db, collection_id, 3600)
    source = FakeChannel(
        videos=[
            entry("vid00000001", duration_s=3600.0, published_at=now() - 600),
            entry("vid00000002", duration_s=1800.0, published_at=now() - 500),
        ]
    )

    outcome = await run_check(db, collection_id, source)

    jobs = await rows(db, "SELECT * FROM jobs WHERE kind = 'index'")
    assert len(jobs) == 1
    job = jobs[0]
    assert int(job["collection_id"]) == collection_id
    assert str(job["public_id"]) == outcome.job_public_id
    args = json.loads(str(job["args_json"]))
    assert args["expand"] == "none"  # these are video URLs, already resolved
    assert args["channels"] == "transcript"
    assert args["tags"] == ["topic:gpu"]
    assert args["follow"] == "gpu-mode"
    items = await rows(
        db, "SELECT source_url FROM job_items WHERE job_id = ? ORDER BY seq", (int(job["id"]),)
    )
    assert {str(row["source_url"]) for row in items} == {
        "https://youtu.be/vid00000001",
        "https://youtu.be/vid00000002",
    }
    for row in (await ledger(db, collection_id)).values():
        assert row["decision"] == "queued"
        assert int(row["job_id"]) == int(job["id"])
        assert outcome.job_public_id in str(row["reason"])
    assert any("queued 2 new video(s)" in message for _, message in await events(db))


async def test_a_budget_hold_becomes_a_queue_when_the_window_frees(db) -> None:
    """The headline: a budget defers, a filter drops, and these are not the same thing.

    A candidate turned away by the daily ceiling keeps its ledger row, keeps its
    place in publication order, and is re-decided on the next check. If it were
    dropped, the ceiling would silently be a rule about *which* videos a follow
    brings in rather than *when* — and nobody asked for that rule.
    """
    collection_id = await make_follow(db, max_per_check=10)
    await backdate_follow(db, collection_id, 3600)
    source = FakeChannel(
        videos=[
            entry("vid00000001", title="first", duration_s=3600.0, published_at=now() - 600),
            entry("vid00000002", title="second", duration_s=3600.0, published_at=now() - 500),
        ]
    )

    first = await run_check(db, collection_id, source, daily_budget_s=3600.0)

    assert (first.queued, first.held) == (1, 1)
    before = await ledger(db, collection_id)
    assert before["vid00000001"]["decision"] == "queued"
    assert before["vid00000002"]["decision"] == "held_budget"
    reason = str(before["vid00000002"]["reason"])
    assert "1:00:00" in reason  # what it would have cost
    assert "budget" in reason and "next check" in reason
    assert len(await rows(db, "SELECT id FROM jobs WHERE kind = 'index'")) == 1

    # A day passes: yesterday's hour is no longer inside the rolling window.
    await db.write(
        lambda c: c.execute(
            "UPDATE follow_seen SET decided_at = unixepoch() - 90000 WHERE decision = 'queued'"
        )
    )
    await db.write(
        lambda c: c.execute(
            "UPDATE follows SET next_check_at = 0 WHERE collection_id = ?", (collection_id,)
        )
    )

    second = await run_check(db, collection_id, source, daily_budget_s=3600.0)

    assert second.seen == 1  # the queued one is settled and not reconsidered
    assert second.queued == 1
    after = await ledger(db, collection_id)
    assert after["vid00000002"]["decision"] == "queued"
    assert after["vid00000002"]["id"] == before["vid00000002"]["id"]  # one row, re-decided
    assert after["vid00000002"]["first_seen_at"] == before["vid00000002"]["first_seen_at"]
    assert len(await rows(db, "SELECT id FROM jobs WHERE kind = 'index'")) == 2


async def test_the_per_check_limit_holds_the_remainder_for_next_time(db) -> None:
    """The same deferral as the budget, one scale down: capped, never truncated."""
    collection_id = await make_follow(db, max_per_check=1)
    await backdate_follow(db, collection_id, 3600)
    source = FakeChannel(
        videos=[
            entry("vid00000001", duration_s=600.0, published_at=now() - 600),
            entry("vid00000002", duration_s=600.0, published_at=now() - 500),
        ]
    )

    outcome = await run_check(db, collection_id, source)

    assert (outcome.queued, outcome.held) == (1, 1)
    decided = (await ledger(db, collection_id)).values()
    held = [row for row in decided if row["decision"] == "held_budget"]
    assert len(held) == 1
    assert "first in line next time" in str(held[0]["reason"])
    # Publication order decides who goes: the oldest waiting upload wins.
    assert str(held[0]["source_id"]) == "vid00000002"


async def test_a_review_follow_queues_nothing_and_says_why_for_each_one(db) -> None:
    """Review mode is a held decision, not a disabled follow: the ledger still fills."""
    collection_id = await make_follow(db, mode="review", max_per_check=10)
    await backdate_follow(db, collection_id, 3600)
    source = FakeChannel(
        videos=[
            entry("vid00000001", duration_s=3600.0, published_at=now() - 600),
            entry("vid00000002", duration_s=1800.0, published_at=now() - 500),
        ]
    )

    outcome = await run_check(db, collection_id, source)

    assert (outcome.queued, outcome.held) == (0, 2)
    assert await rows(db, "SELECT id FROM jobs WHERE kind = 'index'") == []
    for row in (await ledger(db, collection_id)).values():
        assert row["decision"] == "held_review"
        assert "holds its arrivals for you" in str(row["reason"])
    assert len(await db.read(lambda c: store.held(c, 10))) == 2


async def test_a_new_follow_starts_from_the_day_you_made_it(db) -> None:
    """The alternative default queues two hundred videos of GPU time on the first tick."""
    collection_id = await make_follow(db, backfill=0, max_per_check=10)
    source = FakeChannel(
        videos=[entry("vid00000001", title="old", duration_s=3600.0, published_at=now() - 86_400)]
    )

    outcome = await run_check(db, collection_id, source)

    assert (outcome.queued, outcome.skipped) == (0, 1)
    row = (await ledger(db, collection_id))["vid00000001"]
    assert row["decision"] == "skipped_horizon"
    assert "starts from the day you made it" in str(row["reason"])
    assert "backfill" in str(row["reason"])


async def test_a_backfill_reaches_back_exactly_that_many_uploads(db) -> None:
    """By position, because a flat listing does not always date its entries."""
    collection_id = await make_follow(db, backfill=3, max_per_check=10)
    old = now() - 86_400
    source = FakeChannel(
        videos=[
            # Newest first, the order a channel tab comes in.
            entry(f"vid0000000{n}", title=f"talk {n}", duration_s=3600.0, published_at=old - n)
            for n in range(5)
        ]
    )

    outcome = await run_check(db, collection_id, source)

    seen = (await ledger(db, collection_id)).items()
    decisions = {sid: str(row["decision"]) for sid, row in seen}
    assert sorted(sid for sid, d in decisions.items() if d == "queued") == [
        "vid00000000",
        "vid00000001",
        "vid00000002",
    ]
    assert decisions["vid00000003"] == "skipped_horizon"
    assert decisions["vid00000004"] == "skipped_horizon"
    assert outcome.queued == 3
    reason = str((await ledger(db, collection_id))["vid00000004"]["reason"])
    assert "backfill of 3" in reason and "already spent on newer uploads" in reason


async def test_an_undated_upload_needs_a_backfill_to_get_in_at_all(db) -> None:
    """On the *first* check an undated upload is the shelf; after it, it is news.

    A flat listing does not always carry `timestamp`, and the horizon exists for
    one reason only — the first check must not queue two hundred videos. So an
    undated upload is judged by whether this is the first look, and the reason
    string says what was actually established: it was already on the channel,
    not that it was "published before you followed", which nobody knew.

    The version of this rule that only had the date door and the position door
    turned an undated channel at the default `backfill=0` into a follow that
    queued nothing, forever, while writing a false receipt for every upload.
    """
    zero = await make_follow(db, title="A", source_url=CHANNEL, backfill=0, max_per_check=10)
    source = FakeChannel(videos=[entry("vid00000001", duration_s=3600.0, published_at=None)])
    await run_check(db, zero, source)
    first = (await ledger(db, zero))["vid00000001"]
    assert first["decision"] == "skipped_horizon"
    assert "already on the channel" in first["reason"]
    assert "published before" not in first["reason"]

    # The next check, and an upload the ledger has never seen. It was not in the
    # previous listing, which is the whole of what "new" means for a feed.
    source.listings["videos"] = [
        entry("vid00000001", duration_s=3600.0, published_at=None),
        entry("vid00000002", duration_s=3600.0, published_at=None),
    ]
    await run_check(db, zero, source)
    assert (await ledger(db, zero))["vid00000002"]["decision"] == "queued"

    reaching = await make_follow(
        db, title="B", source_url=CHANNEL + "2", backfill=1, max_per_check=10
    )
    await run_check(db, reaching, FakeChannel(videos=[entry("vid00000003", duration_s=3600.0)]))
    assert (await ledger(db, reaching))["vid00000003"]["decision"] == "queued"


async def test_a_settled_decision_is_never_retaken(db) -> None:
    """Otherwise a follow re-judges the same upload every six hours for a year.

    The exception is `held_budget`, which is the one decision that is explicitly
    waiting for something — and that exception is
    `test_a_budget_hold_becomes_a_queue_when_the_window_frees`.
    """
    collection_id = await make_follow(db, min_duration_s=480, max_per_check=10)
    await backdate_follow(db, collection_id, 3600)
    short = entry("vid00000001", title="a clip", duration_s=240.0, published_at=now() - 600)
    source = FakeChannel(videos=[short])

    first = await run_check(db, collection_id, source)
    assert first.seen == 1
    before = (await ledger(db, collection_id))["vid00000001"]
    assert before["decision"] == "skipped_duration"

    # The listing now claims a longer video under the same id. It is not asked again.
    source.listings["videos"] = [
        entry("vid00000001", title="a clip", duration_s=3600.0, published_at=now() - 600)
    ]
    second = await run_check(db, collection_id, source)

    assert second.seen == 0
    after = (await ledger(db, collection_id))["vid00000001"]
    assert after["decision"] == "skipped_duration"
    assert after["decided_at"] == before["decided_at"]
    assert await rows(db, "SELECT id FROM jobs WHERE kind = 'index'") == []


async def test_a_check_spends_a_bounded_number_of_probes_and_admits_it(db) -> None:
    """Reading the listing at all is how a check avoids fifty requests.

    What it cannot judge inside the budget is left with no ledger row — which is
    the only shape that makes the next check reconsider it — and the check says
    so in the job log, because a check that quietly looked at less than it
    listed would be the silent narrowing this codebase refuses everywhere else.
    """
    collection_id = await make_follow(db, min_duration_s=480, max_per_check=10)
    await backdate_follow(db, collection_id, 3600)
    listing = [
        entry(f"vid0000000{n}", title=f"talk {n}", duration_s=None, published_at=now() - 600 - n)
        for n in range(7)
    ]
    source = FakeChannel(
        videos=listing,
        durations={item.url: 3600.0 for item in listing},
    )

    first = await run_check(db, collection_id, source)

    assert first.probed == MAX_PROBES_PER_CHECK == 5
    assert first.unjudged == 2
    assert len(source.probed) == 5
    decided = await ledger(db, collection_id)
    assert len(decided) == 5
    assert all(row["decision"] == "queued" for row in decided.values())
    warnings = [msg for level, msg in await events(db) if level == "warn"]
    assert any("2 candidate(s) left undecided" in msg for msg in warnings)
    assert any("not dropped" in msg and str(MAX_PROBES_PER_CHECK) in msg for msg in warnings)

    second = await run_check(db, collection_id, source)

    assert (second.seen, second.probed, second.unjudged) == (2, 2, 0)
    assert len(await ledger(db, collection_id)) == 7


async def test_a_queued_candidate_remembers_that_its_duration_cost_a_probe(db) -> None:
    """`judged_from` is the receipt for a request; a check that spent one says so.

    Migration 0006 on `follow_seen.judged_from`: "The surface prints which, so a
    check that spent requests says so." A queued row that claims the listing
    answered is that promise broken in the one direction that matters — the
    videos that were accepted are the ones an operator asks about.
    """
    collection_id = await make_follow(db, min_duration_s=480, max_per_check=10)
    await backdate_follow(db, collection_id, 3600)
    listed = entry("vid00000001", duration_s=None, published_at=now() - 600)
    source = FakeChannel(videos=[listed], durations={listed.url: 3600.0})

    outcome = await run_check(db, collection_id, source)

    assert (outcome.queued, outcome.probed) == (1, 1)
    assert str((await ledger(db, collection_id))["vid00000001"]["judged_from"]) == "probe"


async def test_a_budget_hold_is_never_re_probed_for_a_number_the_ledger_holds(db) -> None:
    """A held row comes back every check; it must not cost a request every check.

    The ledger stores what a probe measured, and `held_budget` is the one
    decision that is explicitly provisional — so the candidate returns on the
    next check, and used to return as a bare listing entry with no duration and
    get re-probed. On a channel whose listing withholds durations that is one
    request per held candidate per check, forever, against a source this feature
    treats bot-checks from as an ordinary operating condition.

    The second failure is worse than the waste: re-probes are spent oldest-first
    out of `MAX_PROBES_PER_CHECK`, so five held rows would consume the whole
    probe budget and starve every candidate that had never been judged at all.
    """
    collection_id = await make_follow(db, min_duration_s=480, max_per_check=10)
    await backdate_follow(db, collection_id, 3600)
    listed = entry("vid00000001", duration_s=None, published_at=now() - 600)
    source = FakeChannel(videos=[listed], durations={listed.url: 3600.0})

    # A budget far too small for a 1:00:00 video: probed, then held.
    first = await run_check(db, collection_id, source, daily_budget_s=60.0)
    held = (await ledger(db, collection_id))["vid00000001"]
    assert (first.probed, str(held["decision"])) == (1, "held_budget")
    assert float(held["duration_s"]) == 3600.0

    # Still over budget, so still held — and not asked about a second time.
    second = await run_check(db, collection_id, source, daily_budget_s=60.0)
    assert (second.seen, second.probed) == (1, 0)
    assert len(source.probed) == 1
    assert str((await ledger(db, collection_id))["vid00000001"]["decision"]) == "held_budget"

    # And when the window frees, it is queued off the remembered number, with
    # the provenance of the probe that actually paid for it.
    third = await run_check(db, collection_id, source, daily_budget_s=7200.0)
    landed = (await ledger(db, collection_id))["vid00000001"]
    assert (third.queued, third.probed) == (1, 0)
    assert len(source.probed) == 1
    assert (str(landed["decision"]), str(landed["judged_from"])) == ("queued", "probe")


async def test_a_probe_that_cannot_answer_leaves_the_decision_to_you(db) -> None:
    """A failed probe is still an unknown duration, and unknown is not zero."""
    collection_id = await make_follow(db, min_duration_s=480, max_per_check=10)
    await backdate_follow(db, collection_id, 3600)
    source = FakeChannel(
        videos=[entry("vid00000001", duration_s=None, published_at=now() - 600)],
        probe_raises=SourceError("members only"),
    )

    outcome = await run_check(db, collection_id, source)

    assert (outcome.queued, outcome.held) == (0, 1)
    row = (await ledger(db, collection_id))["vid00000001"]
    assert row["decision"] == "held_review"
    assert "deciding it needs you" in str(row["reason"])


async def test_a_rate_limited_source_hands_the_wait_back_to_the_queue(db) -> None:
    """The ninety-minute cool-off is already built, tested and visible on the jobs page."""
    collection_id = await make_follow(db)
    source = FakeChannel(expand_raises=RateLimited("HTTP Error 429"))

    with pytest.raises(ItemFailed) as caught:
        await run_check(db, collection_id, source)

    assert caught.value.code == "E_RATE_LIMIT"
    assert caught.value.retryable is True
    # One 429 is an operating condition, not a broken channel.
    follow = await one(db, "SELECT * FROM follows WHERE collection_id = ?", (collection_id,))
    assert follow is not None and follow["state"] == "active"


async def test_a_channel_that_is_gone_marks_the_follow_failing_and_records_why(db) -> None:
    """No amount of backoff fixes a renamed channel, so the queue must not own this wait."""
    collection_id = await make_follow(db)
    source = FakeChannel(expand_raises=SourceError("This channel does not exist"))

    with pytest.raises(ItemFailed) as caught:
        await run_check(db, collection_id, source)

    assert caught.value.code == "E_UNSUPPORTED_SOURCE"
    assert caught.value.retryable is False
    follow = await one(db, "SELECT * FROM follows WHERE collection_id = ?", (collection_id,))
    assert follow is not None
    assert follow["state"] == "failing"
    assert follow["last_error_code"] == "E_UNSUPPORTED_SOURCE"
    assert "does not exist" in str(follow["last_error_message"])
    # A failing follow is not due, so it is not re-checked every tick.
    assert await db.read(lambda c: store.due(c, 10)) == []


async def test_a_check_that_found_nothing_still_moves_the_clock(db) -> None:
    """Otherwise a quiet channel is checked on every tick for as long as it is quiet."""
    collection_id = await make_follow(db, check_interval_s=21_600)
    source = FakeChannel(videos=[])

    outcome = await run_check(db, collection_id, source)

    assert (outcome.seen, outcome.queued) == (0, 0)
    follow = await one(db, "SELECT * FROM follows WHERE collection_id = ?", (collection_id,))
    assert follow is not None
    assert abs(int(follow["next_check_at"]) - (now() + 21_600)) <= 5
    assert follow["last_new_at"] is None  # nothing arrived, so nothing is claimed to have
    collection = await one(db, "SELECT * FROM collections WHERE id = ?", (collection_id,))
    assert collection is not None and abs(int(collection["last_sync_at"]) - now()) <= 5


async def test_a_check_that_found_something_stamps_when_it_last_did(db) -> None:
    collection_id = await make_follow(db, max_per_check=10)
    await backdate_follow(db, collection_id, 3600)
    source = FakeChannel(videos=[entry("vid00000001", duration_s=3600.0, published_at=now() - 600)])

    await run_check(db, collection_id, source)

    follow = await one(db, "SELECT * FROM follows WHERE collection_id = ?", (collection_id,))
    assert follow is not None and abs(int(follow["last_new_at"]) - now()) <= 5


async def test_each_watched_tab_is_one_listing_and_the_row_remembers_which(db) -> None:
    """A tab is a request; the ledger prints which one an upload came from."""
    collection_id = await make_follow(db, tabs=("videos", "streams"), max_per_check=10)
    await backdate_follow(db, collection_id, 3600)
    source = FakeChannel(
        videos=[entry("vid00000001", duration_s=3600.0, published_at=now() - 600)],
        streams=[entry("vid00000002", duration_s=7200.0, published_at=now() - 500)],
        shorts=[entry("vid00000003", duration_s=30.0, published_at=now() - 400)],
    )

    outcome = await run_check(db, collection_id, source)

    assert source.expanded == [f"{CHANNEL}/videos", f"{CHANNEL}/streams"]
    # One flat extraction each, bounded by the listing cap rather than by `limit`.
    assert source.asked_for == [MAX_LISTING, MAX_LISTING]
    assert outcome.seen == 2
    tabs = {sid: str(row["tab"]) for sid, row in (await ledger(db, collection_id)).items()}
    assert tabs == {"vid00000001": "videos", "vid00000002": "streams"}


async def test_a_video_listed_on_two_watched_tabs_is_queued_once(db) -> None:
    """One upload is one candidate however many tabs it turns up on.

    A follow that watches /videos and /streams is watching two views of one
    channel, not two channels — and the expensive half of this system is the
    indexing, so a duplicate here costs GPU minutes, not a row.
    """
    collection_id = await make_follow(db, tabs=("videos", "streams"), max_per_check=10)
    await backdate_follow(db, collection_id, 3600)
    broadcast = entry("vid00000001", title="live", duration_s=3600.0, published_at=now() - 600)
    source = FakeChannel(videos=[broadcast], streams=[broadcast])

    outcome = await run_check(db, collection_id, source)

    assert outcome.seen == 1
    assert outcome.queued == 1
    items = await rows(db, "SELECT source_url FROM job_items WHERE job_id > 1")
    assert [str(row["source_url"]) for row in items] == ["https://youtu.be/vid00000001"]


async def test_a_followed_playlist_is_one_listing_with_no_tab_appended(db) -> None:
    """A playlist has no /videos; appending one would ask for a URL that is not there."""
    playlist = "https://www.youtube.com/playlist?list=PL9tOrKPmQ4nAbC"
    collection_id = await make_follow(db, title="Kernels", source_url=playlist, kind="playlist")
    await backdate_follow(db, collection_id, 3600)
    source = FakeChannel()
    source.listings[playlist.rsplit("/", 1)[-1]] = [
        entry("vid00000001", duration_s=3600.0, published_at=now() - 600)
    ]

    outcome = await run_check(db, collection_id, source)

    assert source.expanded == [playlist]
    assert outcome.queued == 1


async def test_a_listing_entry_from_somewhere_else_is_not_indexed_on_a_channels_say_so(db) -> None:
    """These URLs arrive from a remote extractor, so every child is revalidated."""
    collection_id = await make_follow(db, max_per_check=10)
    await backdate_follow(db, collection_id, 3600)
    source = FakeChannel(
        videos=[
            PlaylistEntry(
                url="http://169.254.169.254/latest/meta-data/",
                source_id="vid00000001",
                title="not a video",
                duration_s=3600.0,
                published_at=now() - 600,
            ),
            entry("vid00000002", duration_s=3600.0, published_at=now() - 500),
        ]
    )

    outcome = await run_check(db, collection_id, source)

    assert outcome.seen == 1
    assert set(await ledger(db, collection_id)) == {"vid00000002"}


async def test_a_check_for_a_follow_that_was_deleted_under_it_is_not_a_failure(db) -> None:
    """Unfollowed between the enqueue and the claim: there is simply nothing to check."""
    collection_id = await make_follow(db)
    ctx = await check_context(db, collection_id)
    await db.write(lambda c: store.delete(c, collection_id))

    outcome = await FollowCheck(db, FakeChannel(), daily_budget_s=0.0).run(ctx)

    assert outcome == Outcome()


async def test_a_check_job_that_names_no_follow_fails_typed_and_unretryably(db) -> None:
    """Retrying cannot conjure a collection_id, so the queue must not spend attempts on it."""
    public_id = await db.write(
        lambda c: jobs_store.create_job(
            c, "follow_check", {}, [jobs_store.NewItem(CHANNEL)], priority=CHECK_PRIORITY
        )
    )
    item = await one(
        db,
        "SELECT i.id AS item_id, i.job_id AS job_id FROM job_items i "
        "JOIN jobs j ON j.id = i.job_id WHERE j.public_id = ?",
        (public_id,),
    )
    assert item is not None
    ctx = ItemContext(
        db=db,
        job_id=int(item["job_id"]),
        job_public_id=public_id,
        item_id=int(item["item_id"]),
        source_url=CHANNEL,
        video_id=None,
        kind="follow_check",
    )

    with pytest.raises(ItemFailed) as caught:
        await FollowCheck(db, FakeChannel(), daily_budget_s=0.0).run(ctx)

    assert caught.value.code == "E_BAD_PARAM"
    assert caught.value.retryable is False


async def test_the_videos_a_check_queued_become_members_once_they_land(db) -> None:
    """`collection_videos` answers "what did this follow bring in" with corpus rows."""
    collection_id = await make_follow(db, max_per_check=10)
    await backdate_follow(db, collection_id, 3600)
    source = FakeChannel(videos=[entry("vid00000001", duration_s=3600.0, published_at=now() - 600)])
    await run_check(db, collection_id, source)
    assert await rows(db, "SELECT * FROM collection_videos") == []

    video_id = await add_video(db, "vid00000001")
    await db.write(
        lambda c: c.execute(
            "UPDATE follows SET next_check_at = 0 WHERE collection_id = ?", (collection_id,)
        )
    )
    await run_check(db, collection_id, source)

    members = await rows(db, "SELECT * FROM collection_videos")
    assert [(int(m["collection_id"]), int(m["video_id"])) for m in members] == [
        (collection_id, video_id)
    ]


# ================================================================ the clock


async def test_a_due_follow_becomes_a_check_job_ahead_of_the_indexing_queue(db) -> None:
    """A check is a second of listing; behind an overnight batch it reports a stale clock."""
    collection_id = await make_follow(db)

    assert await enqueue_due(db) == 1

    job = await one(db, "SELECT * FROM jobs")
    assert job is not None
    assert str(job["kind"]) == "follow_check"
    assert int(job["priority"]) == CHECK_PRIORITY == 10
    assert int(job["collection_id"]) == collection_id
    args = json.loads(str(job["args_json"]))
    assert args == {"follow": "gpu-mode", "collection_id": collection_id}
    item = await one(db, "SELECT * FROM job_items WHERE job_id = ?", (int(job["id"]),))
    assert item is not None and str(item["source_url"]) == CHANNEL


async def test_the_clock_moves_when_the_check_is_queued_not_when_it_finishes(db) -> None:
    """A channel that 404s must not be re-checked on every one of the next thousand ticks."""
    collection_id = await make_follow(db, check_interval_s=21_600)

    await enqueue_due(db)

    job = await one(db, "SELECT state FROM jobs")
    assert job is not None and str(job["state"]) == "queued"  # nothing has run it
    follow = await one(db, "SELECT * FROM follows WHERE collection_id = ?", (collection_id,))
    assert follow is not None
    assert abs(int(follow["next_check_at"]) - (now() + 21_600)) <= 5
    assert await db.read(lambda c: store.due(c, 10)) == []


async def test_a_follow_already_on_the_queue_is_not_joined_by_a_second_check(db) -> None:
    """A check deferred behind a ninety-minute backoff would otherwise gain one every tick."""
    collection_id = await make_follow(db, check_interval_s=21_600)
    assert await enqueue_due(db) == 1
    await db.write(lambda c: store.check_now(c, collection_id))

    assert await enqueue_due(db) == 0

    assert len(await rows(db, "SELECT id FROM jobs")) == 1
    # …and the clock is pushed out anyway, so the due query stops re-examining it.
    follow = await one(db, "SELECT * FROM follows WHERE collection_id = ?", (collection_id,))
    assert follow is not None and int(follow["next_check_at"]) > now() + 21_000

    await db.write(
        lambda c: c.execute("UPDATE jobs SET state = 'running' WHERE kind = 'follow_check'")
    )
    await db.write(lambda c: store.check_now(c, collection_id))
    assert await enqueue_due(db) == 0
    assert len(await rows(db, "SELECT id FROM jobs")) == 1


async def test_a_finished_check_does_not_block_the_next_one(db) -> None:
    collection_id = await make_follow(db)
    assert await enqueue_due(db) == 1
    await db.write(
        lambda c: c.execute("UPDATE jobs SET state = 'done' WHERE kind = 'follow_check'")
    )
    await db.write(lambda c: store.check_now(c, collection_id))

    assert await enqueue_due(db) == 1
    assert len(await rows(db, "SELECT id FROM jobs")) == 2


async def test_the_switch_that_turns_follow_checks_off_enqueues_nothing(db) -> None:
    """`VIDTHEQUE_FOLLOW_CHECKS=0` must not leave a check nobody will ever claim."""
    await make_follow(db)

    assert await enqueue_due(db, enabled=False) == 0

    assert await rows(db, "SELECT id FROM jobs") == []


async def test_a_paused_or_failing_follow_is_never_enqueued(db) -> None:
    paused = await make_follow(db, title="A")
    failing = await make_follow(db, title="B")
    await db.write(lambda c: store.set_state(c, paused, "paused"))
    await db.write(lambda c: store.set_state(c, failing, "failing"))

    assert await enqueue_due(db) == 0
    assert await rows(db, "SELECT id FROM jobs") == []


async def test_one_tick_bounds_the_burst_after_a_long_downtime(db) -> None:
    """Every follow overdue at once is many requests at a source that blocks boxes."""
    for n in range(5):
        collection_id = await make_follow(db, title=f"Channel {n}", source_url=f"{CHANNEL}{n}")
        await arm(db, collection_id, -600 - n)

    assert await enqueue_due(db) == 3
    assert len(await rows(db, "SELECT id FROM jobs")) == 3
    # The rest stay due and go on the next tick, oldest clock first.
    assert await enqueue_due(db) == 2
    assert len(await rows(db, "SELECT id FROM jobs")) == 5
