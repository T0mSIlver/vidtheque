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
        # Everything below is a shape the old flattening lexer never emitted,
        # so every one of them is newly reachable syntax.
        "cache AND NOT CVE",
        "cache (CVE OR bug)",
        "(cache OR paged) AND (CVE OR bug)",
        "((cache))",
        "cache (",
        "cache )",
        "(cache AND) OR bug",
        '"foo"*',
        '"foo"* AND NOT bar',
        "cache AND NOT (CVE OR bug)",
        "缓存",
        "缓存 AND NOT bug",
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
    plain = sanitize_fts(query)
    assert plain, query
    conn.execute("SELECT count(*) FROM t WHERE t MATCH ?", (plain,)).fetchone()


# ------------------------------------------------- Boolean MEANING, not shape
#
# Parseability was the only thing the FTS tests checked, and every finding
# below parsed perfectly while meaning something else than the user typed.


def _fts(rows: list[str]):
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE VIRTUAL TABLE t USING fts5(text, tokenize=\"unicode61 tokenchars '_-./'\", prefix='2 3')"
    )
    for row in rows:
        conn.execute("INSERT INTO t VALUES (?)", (row,))
    return conn


def _matches(conn, expr: str) -> set[str]:
    return {
        r[0] for r in conn.execute("SELECT text FROM t WHERE t MATCH ? ORDER BY rowid", (expr,))
    }


def test_and_not_excludes_instead_of_requiring() -> None:
    """`cache AND NOT CVE` used to become `cache AND CVE` — the exact inverse.

    The NOT was dropped as a "doubled operator", so a query asking to EXCLUDE
    the CVE rows returned only the CVE rows.
    """
    # NB a bare `CVE` token, not `CVE-2026-22812`: the tokenchars make the
    # latter one token, which is a different lesson (prefix expansion).
    conn = _fts(["cache tuning notes", "cache and CVE detail", "unrelated"])
    for expr in (sanitize_fts("cache AND NOT CVE"), expand_prefix_fts("cache AND NOT CVE")):
        assert _matches(conn, expr) == {"cache tuning notes"}, expr


def test_bare_not_is_also_binary_exclusion() -> None:
    conn = _fts(["cache tuning notes", "cache and CVE detail"])
    assert _matches(conn, sanitize_fts("cache NOT CVE")) == {"cache tuning notes"}


def test_unary_not_is_rejected_not_smuggled_through() -> None:
    """FTS5 has no unary NOT. Dropping the operator (rather than the leg) keeps
    the query answerable; what must never happen is a parse error."""
    conn = _fts(["cache tuning notes"])
    assert _matches(conn, sanitize_fts("NOT cache")) == {"cache tuning notes"}


def test_parentheses_survive_so_or_cannot_escape_its_group() -> None:
    """`cache (CVE OR bug)` flattened to `cache CVE OR bug`.

    FTS5 precedence is NOT > AND > OR, so the flattened form parses as
    `(cache AND CVE) OR bug` and happily returns a row that contains only
    `bug` — a document the user's query excluded.
    """
    conn = _fts(["cache and CVE detail", "cache and bug report", "a bug, nothing else"])
    for expr in (
        sanitize_fts("cache (CVE OR bug)"),
        expand_prefix_fts("cache (CVE OR bug)"),
    ):
        assert "a bug, nothing else" not in _matches(conn, expr), expr
    assert _matches(conn, sanitize_fts("cache (CVE OR bug)")) == {
        "cache and CVE detail",
        "cache and bug report",
    }


def test_quoted_phrase_keeps_its_suffix_star() -> None:
    """`"foo"*` lost the star and stopped matching `foobar`."""
    conn = _fts(["foobar baz", "totally other"])
    assert sanitize_fts('"foo"*') == '"foo"*'
    assert expand_prefix_fts('"foo"*') == '"foo"*'
    assert _matches(conn, sanitize_fts('"foo"*')) == {"foobar baz"}


def test_control_characters_cannot_reach_fts5() -> None:
    """A query of nothing but NUL sanitised to the nonempty `"\\x00"`, passed
    the leg gate, and reached FTS5 as `unterminated string`."""
    conn = _fts(["hello world", "hello alone"])
    for hostile in ("\x00", "\x00\x00", "\x07", "\x1f"):
        assert sanitize_fts(hostile) == "", repr(hostile)
        assert expand_prefix_fts(hostile) == "", repr(hostile)
        # The gate and the bind agree, so an ungated leg cannot reach FTS5.
        assert not sanitize_fts(hostile), repr(hostile)
    # ...and a control character embedded in real text is a separator, not a
    # character quoted into the expression.
    expr = sanitize_fts("hello\x00world")
    assert "\x00" not in expr
    assert expr == '"hello" AND "world"'
    assert _matches(conn, expr) == {"hello world"}
    # The live failure, end to end: this used to raise OperationalError.
    import sqlite3

    for hostile in ("\x00", "hello\x00world", "\x00cache\x00"):
        for expr in (sanitize_fts(hostile), expand_prefix_fts(hostile)):
            if expr:
                conn.execute("SELECT count(*) FROM t WHERE t MATCH ?", (expr,)).fetchone()
    assert isinstance(conn, sqlite3.Connection)


def test_two_character_han_terms_are_prefix_expanded() -> None:
    """The OCR tokenchars make `缓存-管理` ONE token; `缓存` must still reach it.

    The threshold was a flat `len >= 3`, which is a Latin-word assumption: in a
    script that does not space its words a 2-character term is a whole word.
    """
    conn = _fts(["缓存-管理 dashboard"])
    assert expand_prefix_fts("缓存") == '("缓存" OR "缓存"*)'
    assert _matches(conn, expand_prefix_fts("缓存")) == {"缓存-管理 dashboard"}
    # Latin two-letter terms still do not expand — that is a candidate explosion.
    assert expand_prefix_fts("go") == '"go"'


def test_grouping_and_negation_compose() -> None:
    conn = _fts(["cache with CVE", "cache with bug", "cache alone", "nothing here"])
    expr = sanitize_fts("cache AND NOT (CVE OR bug)")
    assert _matches(conn, expr) == {"cache alone"}


def test_unbalanced_and_empty_groups_are_repaired_not_dropped() -> None:
    conn = _fts(["cache alone"])
    assert _matches(conn, sanitize_fts("cache (")) == {"cache alone"}
    assert _matches(conn, sanitize_fts("cache )")) == {"cache alone"}
    assert _matches(conn, sanitize_fts("cache ()")) == {"cache alone"}
    assert _matches(conn, sanitize_fts("((cache))")) == {"cache alone"}


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
    # Adjacency is spelled AND explicitly (identical to FTS5's implicit AND,
    # but valid after a `)` too, where implicit AND is a syntax error).
    assert transcript_q == '"opencode" AND "CVE"'
    # And the OCR bind actually uses the expanded form.
    params = queries.SearchParams(q="opencode CVE", video_ids=[], limit=5)
    assert queries._ocr_bind(params)["q"] == ocr_q
