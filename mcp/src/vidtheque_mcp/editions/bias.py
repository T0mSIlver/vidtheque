"""Build bounded context bias from validated edition data."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

from .loader import Edition, load_edition

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
    for value in values:
        value = value.strip()
        key = _key(value)
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) == MAX_CONTEXT_BIAS_TERMS:
            return


def build_context_bias(edition: Edition) -> list[str]:
    """Return the edition's speaker, company, fixed, then rare-title terms."""
    out: list[str] = []
    seen: set[str] = set()

    _append_unique(
        out,
        seen,
        (speaker.name for session in edition.sessions for speaker in session.speakers),
    )
    _append_unique(
        out,
        seen,
        (speaker.company for session in edition.sessions for speaker in session.speakers),
    )
    _append_unique(out, seen, edition.fixed_context_bias)
    if len(out) == MAX_CONTEXT_BIAS_TERMS:
        return out

    title_tokens = [
        [(match.group(0), _key(match.group(0))) for match in _TITLE_TOKEN.finditer(session.title)]
        for session in edition.sessions
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
    """Return bias only when the video's tags select the packaged edition."""
    edition = load_edition()
    return build_context_bias(edition) if edition.edition_tag in set(tags) else []
