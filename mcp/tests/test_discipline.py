"""Time normalisation, truncation maths, cancellation, admission, config drift."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vidtheque_mcp.db import Database, QueryInterrupted
from vidtheque_mcp.db.connection import ReadPool, admission
from vidtheque_mcp.errors import ToolError
from vidtheque_mcp.text import (
    clamp,
    clamp_text_chars,
    clock,
    deeplink,
    last_page_offset,
    middle_truncate,
    pagination_line,
    validate_tag,
)
from vidtheque_mcp.timeparse import parse_corpus_time, parse_offset

from .conftest import seed

NOW = datetime(2026, 8, 8, 17, 0, 0, tzinfo=UTC)

# --------------------------------------------------------------- time axes


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-03-01", datetime(2026, 3, 1, tzinfo=UTC)),
        ("2026-03-01T12:00:00Z", datetime(2026, 3, 1, 12, tzinfo=UTC)),
        ("now", NOW),
        ("today", datetime(2026, 8, 8, tzinfo=UTC)),
        ("yesterday", datetime(2026, 8, 7, tzinfo=UTC)),
    ],
)
def test_corpus_axis_formats(value: str, expected: datetime) -> None:
    assert parse_corpus_time(value, "published_after", now=NOW) == int(expected.timestamp())


@pytest.mark.parametrize(
    ("value", "delta_s"),
    [("7d ago", 7 * 86_400), ("3w ago", 21 * 86_400), ("6mo ago", 180 * 86_400), ("2y ago", 730 * 86_400)],
)
def test_relative_formats_are_actually_implemented(value: str, delta_s: int) -> None:
    """screenpipe advertised these while its server rejected them (#3124)."""
    assert parse_corpus_time(value, "published_after", now=NOW) == int(NOW.timestamp()) - delta_s


def test_unparseable_time_is_a_typed_error_not_a_silent_filter() -> None:
    with pytest.raises(ToolError) as caught:
        parse_corpus_time("last tuesday-ish", "published_after")
    assert caught.value.code == "E_BAD_TIME_FORMAT"
    assert "7d ago" in (caught.value.next_hint or "")


@pytest.mark.parametrize(
    ("value", "seconds"),
    [(723, 723.0), ("723", 723.0), ("12:03", 723.0), ("1:12:03", 4323.0), (12.5, 12.5)],
)
def test_intra_video_axis_formats(value: object, seconds: float) -> None:
    assert parse_offset(value, "t_start") == seconds


def test_negative_offsets_are_rejected() -> None:
    with pytest.raises(ToolError) as caught:
        parse_offset(-5, "t_start")
    assert caught.value.code == "E_BAD_PARAM"


# -------------------------------------------------------- token discipline


def test_clamps_are_server_side() -> None:
    assert clamp(9999, 1, 50, 10) == 50
    assert clamp(0, 1, 50, 10) == 1
    assert clamp(None, 1, 50, 10) == 10
    assert clamp("nonsense", 1, 50, 10) == 10  # type: ignore[arg-type]


def test_max_text_chars_zero_opts_out_and_small_values_clamp_up() -> None:
    assert clamp_text_chars(0, 120, 20_000, 1000) == 0
    assert clamp_text_chars(5, 120, 20_000, 1000) == 120  # smaller than the marker
    assert clamp_text_chars(999_999, 120, 20_000, 1000) == 20_000


def test_middle_truncation_keeps_both_ends() -> None:
    text = "A" * 100 + "MIDDLE" + "Z" * 100
    cut = middle_truncate(text, 40)
    assert cut.startswith("A" * 20)
    assert cut.endswith("Z" * 20)
    assert "chars truncated" in cut
    assert middle_truncate(text, 0) == text  # the tested opt-out
    assert middle_truncate("short", 1000) == "short"


def test_deep_links_lead_the_sentence() -> None:
    assert deeplink("kCc8FmEb1nY", 4323.5, 2) == "https://youtu.be/kCc8FmEb1nY?t=4321"
    assert deeplink("kCc8FmEb1nY", 1.0, 2) == "https://youtu.be/kCc8FmEb1nY?t=0"  # clamped
    assert deeplink("vimeo:12345", 10.0, 2) is None  # non-YouTube: field present, null


def test_clock_formats() -> None:
    assert clock(63) == "1:03"
    assert clock(4323) == "1:12:03"


def test_pagination_rendering() -> None:
    assert pagination_line("Results", 8, 0, 10, False, 8, False) == "Results: 8/8 (no more results)"
    assert (
        pagination_line("Results", 10, 0, 10, True, 38, False)
        == "Results: 10/38 (use offset=10 for more)"
    )
    # Probe hit its ceiling: "at least 40, we stopped counting".
    assert (
        pagination_line("Results", 10, 0, 10, True, 41, True)
        == "Results: 10/~40+ (use offset=10 for more)"
    )


def test_pagination_past_the_last_page_prints_the_probe_not_the_offset() -> None:
    """terra eval §9.1. `shown == 0` past the end used to make the offset the
    total (`Videos: 0/200` against 181 real rows). The probe is exact here — an
    empty page means the count stopped below its ceiling."""
    line = pagination_line("Videos", 0, 200, 100, False, 181, False)
    assert line.splitlines()[0] == "Videos: 0/181 (past the last page)"
    assert "the last page starts at offset=100" in line
    assert "offset=200" not in line
    assert last_page_offset(181, 100) == 100
    # An empty corpus has no page to go back to, and says so in the singular.
    assert pagination_line("Videos", 0, 5, 10, False, 1, False).endswith(
        "This call has 1 video; the last page starts at offset=0. "
        "next: re-run with offset=0, or offset=0 for the top."
    )
    # offset=0 with nothing to show is an empty result, not an over-page.
    assert pagination_line("Videos", 0, 0, 10, False, 0, False) == "Videos: 0/0 (no more results)"


def test_tag_validation_mirrors_the_schema_check() -> None:
    assert validate_tag("topic:attention") == ("topic", "attention")
    for bad in ["Topic:X", "topics:x", "topic:", "nonamespace", "topic:-leading"]:
        with pytest.raises(ToolError):
            validate_tag(bad)


def test_tag_validator_and_schema_constraint_agree(tmp_path: Path) -> None:
    """Duplicated validation is justified only if the copies cannot diverge."""
    from vidtheque_mcp.db import migrations
    from vidtheque_mcp.db.connection import open_write_connection

    conn = open_write_connection(tmp_path / "v.db")
    migrations.migrate(conn)
    try:
        for candidate in ["topic:attention", "series:gpu-mode", "lang:en"]:
            ns, name = validate_tag(candidate)
            conn.execute("INSERT INTO tags (ns, name) VALUES (?, ?)", (ns, name))
        for bad_ns, bad_name in [("topic", "Upper"), ("topic", "with space"), ("nope", "x")]:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO tags (ns, name) VALUES (?, ?)", (bad_ns, bad_name))
    finally:
        conn.close()


# --------------------------------------------------- cancellation, admission


async def test_progress_handler_interrupts_a_long_query(tmp_path: Path) -> None:
    """A timeout without cancellation is worse than neither — it hides the
    problem until the pool is gone (screenpipe #4474)."""
    data = tmp_path / "data"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")

    pool = ReadPool(data / "vidtheque.db", size=1, budget_s=0.05)
    await pool.open()
    try:
        def slow(conn: sqlite3.Connection) -> int:
            # A recursive CTE with no cheap plan: it must be *interrupted*, not
            # merely abandoned.
            return conn.execute(
                "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 200000000)"
                " SELECT COUNT(*) FROM c"
            ).fetchone()[0]

        started = time.monotonic()
        with pytest.raises(QueryInterrupted) as caught:
            await pool.run(slow)
        elapsed = time.monotonic() - started
        assert caught.value.deadline_expired is True
        assert elapsed < 5.0, "the deadline must stop real work, not just stop waiting"
    finally:
        await pool.close()


async def test_admission_control_refuses_rather_than_queueing() -> None:
    """Queueing converts a slow query into a slow *everything*."""
    sem = asyncio.Semaphore(1)
    async with admission(sem):
        with pytest.raises(ToolError) as caught:
            async with admission(sem):
                pass
        assert caught.value.code == "E_BUSY"
        assert caught.value.retry_after_s == 1
        # ...and the remedy is time, and only time. The semaphore is taken
        # before the query is built, so "narrow the query so it costs less" —
        # what this used to say — is refused exactly as fast, and a terra
        # consumer acted on that half twice and lost both searches
        # (research/mcp-eval-terra-2026-08-10.md §4.9).
        hint = caught.value.next_hint or ""
        assert "narrow" not in hint, hint
        assert "retry the IDENTICAL call in 1s" in hint
        assert "limit is on concurrent searches" in hint
        # §9.7: the text landed, the behaviour half did not — one consumer read
        # "retry the same call" as advice and re-worded its query twice. The
        # wrong move is now named as an instruction, not implied by the right
        # one.
        assert "do not reformulate the query" in hint
    # Released again afterwards.
    async with admission(sem):
        pass


# ------------------------------------------------------------ config drift


async def test_dimension_mismatch_disables_writes_and_vector_legs(tmp_path: Path) -> None:
    """Mixed embedding spaces produce plausible-looking garbage no test catches."""
    data = tmp_path / "data"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")

    conn = sqlite3.connect(data / "vidtheque.db")
    conn.execute("UPDATE config SET value = '512' WHERE key = 'text_embed.dim'")
    conn.commit()
    conn.close()

    db = Database(path=data / "vidtheque.db")
    await db.open()
    try:
        assert db.writes_allowed is False
        assert db.vectors.enabled is False
        assert "vec_chunks declares FLOAT[2048]" in (db.vectors.reason or "")
        assert "FTS-only" in (db.vectors.note() or "")
    finally:
        await db.close()


async def test_worker_model_drift_disables_the_vector_leg(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    db = Database(path=data / "vidtheque.db")
    await db.open()
    try:
        assert db.vectors.enabled is True
        db.note_worker_drift("bge-m3", 2048)
        assert db.vectors.enabled is False
        assert "bge-m3" in (db.vectors.reason or "")
    finally:
        await db.close()


async def test_the_shipped_defaults_do_not_drift_against_each_other(tmp_path: Path) -> None:
    """A fresh migration plus a worker running `deploy/.env.example` defaults has
    to leave both vector legs live. It did not: the migration seeded short names
    and the worker reports HF ids, so a default install answered FTS-only and
    blamed a model mismatch the repo itself created (smoke §4.1)."""
    from vidtheque_mcp.pipeline.runner import _dimension_mismatch

    env = dict(
        line.split("=", 1)
        for line in (Path(__file__).resolve().parents[2] / "deploy/.env.example")
        .read_text()
        .splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    data = tmp_path / "data"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    db = Database(path=data / "vidtheque.db")
    await db.open()
    try:
        assert db.writes_allowed is True
        # Query time: the text leg stays on for the model the worker serves.
        db.note_worker_drift(env["EMBED_MODEL"], db.text_dim)
        assert db.vectors.enabled is True
        assert db.vectors.note() is None
        # Index time: neither embed stage refuses to write.
        assert (
            _dimension_mismatch(
                [[0.0] * db.text_dim], db.text_dim, db.text_dim,
                env["EMBED_MODEL"], db.config["text_embed.model"],
            )
            is None
        )
        assert (
            _dimension_mismatch(
                [[0.0] * db.frame_dim], db.frame_dim, db.frame_dim,
                env["IMAGE_EMBED_MODEL"], db.config["frame_embed.model"],
            )
            is None
        )
        # And the check is still a check: a different checkpoint at the same
        # width is exactly the silent-drift case, and is still caught.
        assert _dimension_mismatch(
            [[0.0] * db.text_dim], db.text_dim, db.text_dim, "BAAI/bge-m3",
            db.config["text_embed.model"],
        )
        db.note_worker_drift("BAAI/bge-m3", db.text_dim)
        assert db.vectors.enabled is False
    finally:
        await db.close()


async def test_search_degrades_to_fts_when_vectors_are_disabled(tmp_path: Path) -> None:
    from .conftest import FakeEmbeddings
    from vidtheque_mcp.app import assemble
    from vidtheque_mcp.config import Settings
    from vidtheque_mcp.tools import search

    data = tmp_path / "data"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    conn = sqlite3.connect(data / "vidtheque.db")
    conn.execute("UPDATE config SET value = '512' WHERE key = 'text_embed.dim'")
    conn.commit()
    conn.close()

    parts = assemble(
        Settings(
            data_dir=data,
            public_url="http://localhost:8080",
            worker_url="w",
            auth_mode="none",
            secret="k",
        ),
        embeddings=FakeEmbeddings(),
        run_pipeline=False,
    )
    await parts.db.open()
    try:
        result = await search.run(parts.deps, q="cache", limit=5)
        assert result.is_error is False
        notes = result.structured_content["notes"]  # type: ignore[index]
        assert any("vector legs are disabled" in n for n in notes)
        assert result.structured_content["results"]  # type: ignore[index]
    finally:
        await parts.db.close()
        parts.auth.close()
