"""The public box's sample feed: verdicts against one published profile — demo-site.md §8.

A public deployment has no owner to triage for, so `VIDTHEQUE_SAMPLE_FEED=1`
gives it one: the profile below, written into the database at boot, and a
verdict queued for every video that has none or was judged under an older
revision of it. The profile lives here, in the repo, because the feed is only
honest if anyone can read what it was scored against.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..config import _bool_env
from ..db.queries import QUERYABLE_INDEX_STATES
from ..profile import store as profile_store
from ..verdicts import store as verdicts_store

NAME = "A builder shipping coding agents"
ENTRIES: tuple[tuple[str, float], ...] = (
    ("Coding agent evals", 0.9),
    ("Agent harness design", 0.8),
    ("Context engineering", 0.7),
    ("RL environments", 0.6),
    ("Local inference", 0.5),
    ("Product launch pitches", -0.6),
    ("Funding and hiring", -0.4),
)
REASON = "sample profile (public/sample.py)"


@dataclass(frozen=True)
class SampleFeedSettings:
    enabled: bool = False

    @classmethod
    def from_env(cls) -> "SampleFeedSettings":
        return cls(enabled=_bool_env("VIDTHEQUE_SAMPLE_FEED", False))


def sync_profile(conn: sqlite3.Connection) -> int:
    """Make the live entries exactly `ENTRIES`; returns the events written.

    An unchanged profile writes nothing, so its revision, and every verdict
    written under it, stays current across restarts.
    """
    wanted = {text.casefold(): (text, weight) for text, weight in ENTRIES}
    live = {str(r["text"]).casefold(): r for r in profile_store.entries(conn)}
    ops = profile_store.Ops(
        add=[wanted[key] for key in wanted if key not in live],
        drop=[int(r["id"]) for key, r in live.items() if key not in wanted],
        reweight=[
            (int(r["id"]), wanted[key][1])
            for key, r in live.items()
            if key in wanted and float(r["weight"]) != wanted[key][1]
        ],
        reason=REASON,
    )
    if not (ops.add or ops.drop or ops.reweight):
        return 0
    return len(profile_store.apply(conn, ops, "owner").event_ids)


def queue_stale(conn: sqlite3.Connection) -> int:
    """Queue a verdict for each queryable video with none, or one from an older revision."""
    rev = profile_store.revision(conn)
    marks = ",".join("?" for _ in QUERYABLE_INDEX_STATES)
    ids = [
        int(r[0])
        for r in conn.execute(
            f"SELECT v.id FROM videos v LEFT JOIN verdicts d ON d.video_id = v.id"
            f" WHERE v.index_state IN ({marks}) AND (d.video_id IS NULL OR d.profile_rev != ?)"
            " ORDER BY v.published_at DESC, v.id DESC",
            (*QUERYABLE_INDEX_STATES, rev),
        )
    ]
    return sum(1 for video_id in ids if verdicts_store.queue(conn, video_id) is not None)
