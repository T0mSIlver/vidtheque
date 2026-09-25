"""The context-bias builder over a validated edition (aie-paris-2026.md §7).

The list the worker biases with is derived from the schedule the facade already
validated, so there is never a second speaker list in worker code, bench code or
an environment value. This module reads four fields of that object —
`context_bias.fixed` and `.exclude` and, per session, `title` and `speakers[].{name, company}` —
and nothing about how it was loaded.

`load_edition` is imported from the package body rather than the other way
round: `__init__` re-exports these two functions from its last lines, after the
loader exists, so the cycle never closes during module definition.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from . import KNOWN_EDITIONS, load_edition

MAX_CONTEXT_BIAS_TERMS = 100

_TITLE_TOKEN = re.compile(r"[^\W_]+(?:[.'’-][^\W_]+)*", re.UNICODE)
_STOP_WORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "against",
        "also",
        "among",
        "because",
        "before",
        "being",
        "between",
        "both",
        "build",
        "building",
        "from",
        "have",
        "into",
        "more",
        "next",
        "only",
        "over",
        "should",
        "than",
        "that",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "through",
        "using",
        "what",
        "when",
        "where",
        "which",
        "while",
        "with",
        "without",
        "your",
    }
)


def _key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _append_unique(out: list[str], seen: set[str], values: Iterable[str]) -> None:
    # The cap is checked before the append, not after it: a tier that finds the
    # list already full must add nothing. Equality after the fact let the next
    # tier write term 101, and the worker rejects a list of 101 with a 400.
    for value in values:
        if len(out) >= MAX_CONTEXT_BIAS_TERMS:
            return
        value = value.strip()
        key = _key(value)
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)


def build_context_bias(edition: Mapping[str, Any]) -> list[str]:
    """Return the edition's speaker, company, fixed, then rare-title terms."""
    sessions: Sequence[Mapping[str, Any]] = edition["sessions"]
    out: list[str] = []
    # An excluded term counts as already seen, so no tier can add it.
    seen: set[str] = {_key(term) for term in edition["context_bias"].get("exclude", ())}

    _append_unique(
        out,
        seen,
        (speaker["name"] for session in sessions for speaker in session["speakers"]),
    )
    _append_unique(
        out,
        seen,
        (speaker["company"] for session in sessions for speaker in session["speakers"]),
    )
    _append_unique(out, seen, edition["context_bias"]["fixed"])
    if len(out) >= MAX_CONTEXT_BIAS_TERMS:
        return out

    title_tokens = [
        [
            (match.group(0), _key(match.group(0)))
            for match in _TITLE_TOKEN.finditer(session["title"])
        ]
        for session in sessions
    ]
    title_frequency = Counter(
        key for tokens in title_tokens for key in {key for _source, key in tokens}
    )
    distinctive = (
        source
        for tokens in title_tokens
        for source, key in tokens
        if len(source) >= 4 and key not in _STOP_WORDS and title_frequency[key] == 1
    )
    _append_unique(out, seen, distinctive)
    return out


def context_bias_for_tags(tags: Iterable[str]) -> list[str]:
    """Return bias only when the video's own tags name a committed edition.

    The video decides, so the pipeline never has to learn which backend or which
    conference it is running for: a tag an operator applied is the whole input.
    """
    applied = set(tags)
    for slug in KNOWN_EDITIONS:
        edition = load_edition(slug)
        if edition is not None and edition["tags"]["edition"] in applied:
            return build_context_bias(edition)
    return []
