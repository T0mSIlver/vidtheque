"""OCR-leg prefix expansion — the CVE-2026-22812 lesson.

Found live: OCR indexed `CVE-2026-22812 Detail` from an NVD screenshot on a
slide, the model searched "opencode CVE", got zero hits (the OCR tokenizer's
tokenchars make the whole id ONE token), and told the user the corpus had no
CVE mentions. The data was there; matching couldn't reach it. The fix is
screenpipe's: query-side prefix expansion on the OCR leg only.
"""

from __future__ import annotations

import pytest

from vidtheque_mcp.db.queries import expand_prefix_fts, sanitize_fts
from vidtheque_mcp.tools import search

from .conftest import Assembled


# ------------------------------------------------------------------ unit


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("CVE", '("CVE" OR "CVE"*)'),
        # Adjacent groups need an EXPLICIT AND — implicit AND between
        # parenthesised expressions is an FTS5 syntax error (found live).
        ("opencode CVE", '("opencode" OR "opencode"*) AND ("CVE" OR "CVE"*)'),
        # Phrases are exact — never expanded.
        ('"kv cache"', '"kv cache"'),
        ('"kv cache" CVE', '"kv cache" AND ("CVE" OR "CVE"*)'),
        # An explicit star is already a prefix query; don't double-wrap.
        ("nvidia*", '"nvidia"*'),
        # Short terms don't expand (candidate explosion), but are still quoted.
        ("go", '"go"'),
        # Operators survive with a group on each side.
        ("cache OR CVE", '("cache" OR "cache"*) OR ("CVE" OR "CVE"*)'),
        # Dangling operators are dropped, same as sanitize_fts.
        ("AND CVE", '("CVE" OR "CVE"*)'),
        ("", ""),
    ],
)
def test_expand_prefix_fts(query: str, expected: str) -> None:
    assert expand_prefix_fts(query) == expected


def test_expansion_empty_exactly_when_sanitize_is_empty() -> None:
    # The do-fts/skip-leg decision uses sanitize_fts; the two must agree on
    # emptiness or a leg could bind an empty MATCH.
    for q in ("", "()", '""', "AND OR", "* "):
        assert bool(expand_prefix_fts(q)) == bool(sanitize_fts(q)), q


@pytest.mark.parametrize(
    "query",
    [
        "CVE",
        "opencode CVE",
        "opencode CVE http server",
        '"kv cache" CVE',
        "cache OR CVE",
        "nvidia-smi",
        "torch.compile latency",
        "a b CVE",  # short terms interleaved with an expanded one
        "NOT cache CVE",
    ],
)
def test_expanded_output_actually_parses(query: str) -> None:
    """The lesson of the live syntax error: string-shape tests are not
    parseability tests. Every expansion must survive a real FTS5 MATCH."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(text, tokenize=\"unicode61 tokenchars '_-./'\")")
    conn.execute("INSERT INTO t VALUES ('CVE-2026-22812 opencode http server')")
    expanded = expand_prefix_fts(query)
    assert expanded, query
    conn.execute("SELECT count(*) FROM t WHERE t MATCH ?", (expanded,)).fetchone()


# ------------------------------------------------------------- integration


async def test_ocr_hit_on_token_fragment(assembled: Assembled) -> None:
    """`nvidia` must find the fixture's `nvidia-smi 18304MiB` OCR line."""
    result = await search.run(assembled.deps, q="nvidia", content_type="ocr", limit=5)
    assert not result.is_error
    hits = (result.structured_content or {})["results"]
    assert any("nvidia-smi" in h["text"] for h in hits), hits


def test_transcript_leg_is_not_prefix_expanded() -> None:
    """Porter handles the transcript leg; expansion is an OCR-tokenizer
    compensation, not a general fuzzy. Assert on the bind strings — a
    behavioral probe can't distinguish prefix matching from porter stemming
    (`cach` matches `caching` via the stemmer, correctly)."""
    from vidtheque_mcp.db import queries

    ocr_q = queries.expand_prefix_fts("opencode CVE")
    transcript_q = queries.sanitize_fts("opencode CVE")
    assert ocr_q == '("opencode" OR "opencode"*) AND ("CVE" OR "CVE"*)'
    assert transcript_q == '"opencode" "CVE"'
    # And the OCR bind actually uses the expanded form.
    params = queries.SearchParams(q="opencode CVE", video_ids=[], limit=5)
    assert queries._ocr_bind(params)["q"] == ocr_q
