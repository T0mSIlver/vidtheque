#!/usr/bin/env python3
"""Record and compare the bounded row growth of one long-VOD rehearsal."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vidtheque_mcp.db.connection import open_read_connection

COUNT_SQL = {
    "cues": "SELECT COUNT(*) FROM cues",
    "chunks": "SELECT COUNT(*) FROM chunks",
    "keyframes": "SELECT COUNT(*) FROM keyframes WHERE dup_of IS NULL",
    "ocr_lines": "SELECT COUNT(*) FROM ocr_lines",
    "vec_chunks": "SELECT COUNT(*) FROM vec_chunks",
    "vec_frames": "SELECT COUNT(*) FROM vec_frames",
}


@dataclass(frozen=True, slots=True)
class Snapshot:
    captured_at: str
    data_dir: str
    database_bytes: int
    counts: dict[str, int]


def snapshot(data_dir: Path) -> Snapshot:
    db_path = data_dir / "vidtheque.db"
    if not db_path.is_file():
        raise ValueError(f"database does not exist: {db_path}")
    conn = open_read_connection(db_path)
    try:
        counts = {name: int(conn.execute(sql).fetchone()[0]) for name, sql in COUNT_SQL.items()}
    finally:
        conn.close()
    related = [db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]
    return Snapshot(
        captured_at=datetime.now(UTC).isoformat(),
        data_dir=str(data_dir.resolve()),
        database_bytes=sum(path.stat().st_size for path in related if path.exists()),
        counts=counts,
    )


def compare(before: Snapshot, after: Snapshot) -> dict[str, Any]:
    """Two readings of one directory, before and after one rehearsal.

    A baseline from another database can only make growth look smaller than it
    was, and a shrinking count says the same thing: whatever these two files
    describe, it is not one indexing run. Both refuse here rather than print a
    clear gate over an arithmetic that means nothing.
    """
    if before.data_dir != after.data_dir:
        raise ValueError(
            f"snapshots describe different data directories: "
            f"{before.data_dir!r} then {after.data_dir!r}"
        )
    delta = {
        name: after.counts.get(name, 0) - before.counts.get(name, 0)
        for name in COUNT_SQL
    }
    bytes_delta = after.database_bytes - before.database_bytes
    shrunk = sorted(name for name, value in delta.items() if value < 0)
    if shrunk or bytes_delta < 0:
        raise ValueError(
            "the after snapshot is smaller than the before snapshot "
            f"({', '.join([*shrunk, *(['database_bytes'] if bytes_delta < 0 else [])])}); "
            "indexing only adds rows, so these two are not one run"
        )
    blocks: list[str] = []
    if delta["vec_frames"] > 600:
        blocks.append(f"vec_frames grew by {delta['vec_frames']}, above the 600-frame cap")
    if delta["vec_chunks"] > 960:
        blocks.append(
            f"vec_chunks grew by {delta['vec_chunks']}, above the expected about-960 text rows"
        )
    return {
        "before": asdict(before),
        "after": asdict(after),
        "delta": {**delta, "database_bytes": bytes_delta},
        "follow_gate": "blocked" if blocks else "clear",
        "blocks": blocks,
    }


def load_snapshot(path: Path) -> Snapshot:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Snapshot(
        captured_at=str(raw["captured_at"]),
        data_dir=str(raw["data_dir"]),
        database_bytes=int(raw["database_bytes"]),
        counts={name: int(raw["counts"][name]) for name in COUNT_SQL},
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    current = snapshot(args.data_dir)
    payload: dict[str, Any] = asdict(current)
    if args.before:
        payload = compare(load_snapshot(args.before), current)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 2 if payload.get("follow_gate") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
