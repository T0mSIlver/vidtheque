"""Following channels: the rules, the ledger, the clock.

A follow is a `collections` row (`kind='channel'|'playlist'`) that finally gets
used — the storage has been in the schema since 0001 and index-schema §1.8 said
why — plus a `follows` row holding its conditions and its clock, plus a
`follow_seen` row for every candidate it has ever looked at.

That last table is the design. Every other subsystem here explains itself:
`video_stages` says which model transcribed what, `job_events` says why a job
waited ninety minutes, `data_status` admits the gap. A follow that quietly drops
a four-minute video because its floor is eight would be the one place the index
goes silent, and silence is the defect. So a follow is not a setting — it is a
stage with provenance, and the rules it was given are read back as a sentence
above a ledger of what they cost.
"""

from __future__ import annotations

from .rules import Candidate, Rules, Verdict, describe, judge, judge_duration

__all__ = [
    "Candidate",
    "Rules",
    "Verdict",
    "describe",
    "judge",
    "judge_duration",
]
