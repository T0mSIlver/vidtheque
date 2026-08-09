"""The database facade: boot, the anti-drift assertion, and access handles.

The single most important table in the file is ``config``. Index-time and
query-time must use the same models; if they drift, retrieval degrades silently
and looks like a bad corpus rather than a bug.

A mismatch is **fatal at boot for writes and degrades reads**: the server
refuses to index (it would mix embedding spaces) but still serves FTS-only
search, with a ``note:`` on every response saying the vector legs are disabled
and why (index-schema §1.1).
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

from .connection import ReadPool, Writer, open_write_connection
from .migrations import migrate

T = TypeVar("T")

_DIM_RE = re.compile(r"FLOAT\[(\d+)\]", re.I)


@dataclass
class VectorState:
    """Whether the vector legs may run, and why not when they may not."""

    enabled: bool = True
    reason: str | None = None

    def disable(self, reason: str) -> None:
        self.enabled = False
        self.reason = reason

    def note(self) -> str | None:
        if self.enabled:
            return None
        return f"note: vector legs are disabled — {self.reason} Results are FTS-only."


class ConfigDriftError(RuntimeError):
    """Declared vector dimensions and the config table disagree."""


@dataclass
class Database:
    """Owns the write connection, the read pool and the boot-time config."""

    path: Path
    read_pool_size: int = 4
    query_budget_s: float = 30.0
    # Mirrors jobs.store.DEFAULT_STALE_CLAIM_S — imported lazily, because
    # `jobs` imports `db` and the cycle is not worth a constants module.
    stale_claim_s: int = 300

    config: dict[str, str] = field(default_factory=dict)
    vectors: VectorState = field(default_factory=VectorState)
    writes_allowed: bool = True

    _pool: ReadPool | None = field(default=None, repr=False)
    _writer: Writer | None = field(default=None, repr=False)

    # ------------------------------------------------------------ lifecycle

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._bootstrap)
        self._writer = Writer(self.path)
        await self._writer.open()
        self._pool = ReadPool(self.path, self.read_pool_size, self.query_budget_s)
        await self._pool.open()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        if self._writer is not None:
            await self._writer.close()
            self._writer = None

    def _bootstrap(self) -> None:
        conn = open_write_connection(self.path)
        try:
            migrate(conn)
            self.config = {
                row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM config")
            }
            self._assert_dimensions(conn)
            self._recover_crashed_jobs(conn)
        finally:
            conn.close()

    # ------------------------------------------------------ boot assertion

    def _assert_dimensions(self, conn: sqlite3.Connection) -> None:
        """Compare `config` against the dimensions declared on the vec tables.

        The declared dimension lives in the DDL where the query planner needs
        it and in `config` where the indexer needs it; they must agree or the
        two halves of the system mean different things by "a vector".
        """
        declared = {
            name: _declared_dim(conn, name) for name in ("vec_chunks", "vec_frames")
        }
        for key, table in (("text_embed.dim", "vec_chunks"), ("frame_embed.dim", "vec_frames")):
            want = self.config.get(key)
            got = declared[table]
            if want is None or got is None:  # pragma: no cover - defensive
                continue
            if int(want) != got:
                self.writes_allowed = False
                self.vectors.disable(
                    f"config[{key}]={want} but {table} declares FLOAT[{got}]; "
                    "indexing is refused so embedding spaces cannot be mixed."
                )

    def _recover_crashed_jobs(self, conn: sqlite3.Connection) -> None:
        """Crash recovery at boot (index-schema §1.9).

        Boot is the *first* sweep, not the only one — that was the bug behind
        the zombie ``job_5ac6f2ee2b29``: a process killed mid-`keyframe` and
        restarted inside the staleness window left a claim that boot was too
        early to see and that nothing ever looked at again. The runner sweeps
        before every claim now (``PipelineRunner.reclaim_stale``); this one
        still runs, because a build with the pipeline disabled must not leave
        `running` rows lying around either.
        """
        from ..jobs import store as jobs_store

        conn.execute("BEGIN IMMEDIATE")
        try:
            jobs_store.reclaim_stale(conn, int(self.stale_claim_s))
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    # ------------------------------------------------------------- access

    async def read(self, fn: Callable[[sqlite3.Connection], T], budget_s: float | None = None) -> T:
        if self._pool is None:  # pragma: no cover - misuse
            raise RuntimeError("Database.open() was never awaited")
        return await self._pool.run(fn, budget_s)

    async def write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        if self._writer is None:  # pragma: no cover - misuse
            raise RuntimeError("Database.open() was never awaited")
        return await self._writer.run(fn)

    # ------------------------------------------------------------- config

    def config_int(self, key: str, default: int) -> int:
        raw = self.config.get(key)
        try:
            return int(raw) if raw is not None else default
        except ValueError:  # pragma: no cover - corrupt config
            return default

    @property
    def text_dim(self) -> int:
        return self.config_int("text_embed.dim", 2048)

    @property
    def frame_dim(self) -> int:
        return self.config_int("frame_embed.dim", 2048)

    @property
    def unified_embedding(self) -> bool:
        """Do both legs name one checkpoint?

        Not a shortcut anything takes — the two legs stay two legs, two stages,
        two `config` records and two dimension assertions whatever this says.
        It exists because one thing genuinely differs: a frame-space model
        mismatch *is* a text-space model mismatch when there is one model, so
        the drift check has to cover both spaces instead of only the one it was
        written for (memo §5.4).
        """
        text = (self.config.get("text_embed.model") or "").lower()
        frame = (self.config.get("frame_embed.model") or "").lower()
        return bool(text) and text == frame

    @property
    def query_prefix(self) -> str:
        """What indexing assumed for the *text* leg's queries.

        A record, not an input: the worker applies the instruction (the query
        layer sends `input_type=query` and never prepends this, or it would be
        applied twice). Compare it against `instructions.query` on the worker's
        `GET /status` — that is the reconciliation, and it is needed because
        this key shipped wrong from 0001 and nothing read it loudly enough to
        notice (pipeline-perf-2026-08-09.md §5).
        """
        return self.config.get("text_embed.query_prefix", "")

    @property
    def frame_query_prefix(self) -> str:
        """The same record for the frame leg, added by migration 0004.

        Empty before it: SigLIP 2's text tower takes no instruction, so there
        was nothing to record. An instruction-aware unified model has a
        *different* instruction per leg, and the two are what make one space
        answer two retrieval tasks."""
        return self.config.get("frame_embed.query_prefix", "")

    @property
    def diarization_enabled(self) -> bool:
        return self.config.get("diarization.enabled", "0") == "1"

    def note_worker_drift(
        self, model: str | None, dimensions: int | None, space: str = "text"
    ) -> None:
        """Called with what the worker actually returned for an embedding.

        The EmbeddingsResponse carries `model` and `dimensions`; that is the
        authoritative drift check, because it describes the vector we are about
        to compare against stored ones.

        `space` names which leg answered. It used to be checked for the text
        leg only, and the asymmetry was deliberate and correct while the two
        spaces came from two checkpoints: a SigLIP model id said nothing about
        whether the transcript index was still coherent, so a frame-space
        mismatch could not be allowed to disable the transcript leg.

        With one model serving both, a frame-space model mismatch *is* a
        text-space model mismatch (memo §5.4), so both spaces are checked now —
        against their own recorded model and their own recorded width, which is
        still two independent records even when they happen to be equal. When
        the two legs name two different checkpoints, disabling both legs on a
        frame-space mismatch would be the old over-reach; so the *disable* stays
        whole-index only when the corpus is unified, and otherwise a frame-space
        mismatch is reported by the frame leg's own dimension check in
        `Deps.embed_query` and costs the frame leg alone.
        """
        if space == "frame" and not self.unified_embedding:
            return
        want_dim = self.frame_dim if space == "frame" else self.text_dim
        if dimensions is not None and dimensions != want_dim:
            self.vectors.disable(
                f"the worker returned {dimensions}-d embeddings for the {space} "
                f"leg but the corpus stores {want_dim}-d vectors."
            )
            return
        want = self.config.get(f"{space}_embed.model")
        if model and want and model.lower() != want.lower():
            self.vectors.disable(
                f"the worker is serving {model!r} but the corpus was embedded "
                f"with {want!r}."
            )


def _declared_dim(conn: sqlite3.Connection, table: str) -> int | None:
    sql: Any = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = ?", (table,)
    ).fetchone()
    if sql is None or sql[0] is None:  # pragma: no cover - defensive
        return None
    match = _DIM_RE.search(sql[0])
    return int(match.group(1)) if match else None
