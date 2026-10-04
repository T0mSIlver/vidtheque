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

from ..config import _bool_env, _int_env
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


# Enough newest talks to fill a credible week or two of the feed, at a cost a
# public demo can carry; new talks join as they arrive (demo-site.md §8.1).
DEFAULT_VIDEOS = 40
MAX_VIDEOS = 1_000


@dataclass(frozen=True)
class SampleFeedSettings:
    enabled: bool = False
    videos: int = DEFAULT_VIDEOS

    @classmethod
    def from_env(cls) -> "SampleFeedSettings":
        videos = _int_env("VIDTHEQUE_SAMPLE_FEED_VIDEOS", DEFAULT_VIDEOS)
        return cls(
            enabled=_bool_env("VIDTHEQUE_SAMPLE_FEED", False),
            videos=max(1, min(MAX_VIDEOS, videos)),
        )


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


def queue_stale(conn: sqlite3.Connection, videos: int = DEFAULT_VIDEOS) -> int:
    """Among the newest `videos` queryable videos, queue a verdict for each with
    none, or one from an older profile revision."""
    rev = profile_store.revision(conn)
    marks = ",".join("?" for _ in QUERYABLE_INDEX_STATES)
    ids = [
        int(r[0])
        for r in conn.execute(
            f"SELECT n.id FROM (SELECT id FROM videos WHERE index_state IN ({marks})"
            " ORDER BY published_at DESC, id DESC LIMIT ?) n"
            " LEFT JOIN verdicts d ON d.video_id = n.id"
            " WHERE d.video_id IS NULL OR d.profile_rev != ?",
            (*QUERYABLE_INDEX_STATES, videos, rev),
        )
    ]
    return sum(1 for video_id in ids if verdicts_store.queue(conn, video_id) is not None)
