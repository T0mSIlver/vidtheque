#!/usr/bin/env python3
"""Private three-arm proper-noun evaluation for the Paris dress rehearsal.

The paid arms go through a worker that is already configured for Voxtral, over
HTTP, so `MISTRAL_API_KEY` belongs to that worker's environment and to nothing
here: this process never reads it, and the worker's boot check is the one that
refuses a run without it (`aie-paris-2026.md` §6.1 and §8).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from .harness import post_multipart
except ImportError:  # direct `python bench/aie_paris_eval.py` execution
    from harness import post_multipart
from vidtheque_mcp.db.connection import open_read_connection
from vidtheque_mcp.editions import build_context_bias

LIMITATION = (
    "This measures schedule-derived proper-noun coverage, not general word error.\n"
    "A missing term may not have been spoken, and a present term may occur outside\n"
    "the intended mention."
)

ARMS = ("indexed whisperX", "Voxtral without bias", "Voxtral with bias")
_TOKEN = re.compile(r"[^\W_]+(?:[.'’-][^\W_]+)*", re.UNICODE)
_STOP_WORDS = frozenset(
    {
        "about",
        "after",
        "before",
        "being",
        "between",
        "building",
        "from",
        "have",
        "into",
        "that",
        "their",
        "this",
        "through",
        "using",
        "what",
        "when",
        "where",
        "which",
        "while",
        "with",
        "your",
    }
)


@dataclass(frozen=True, slots=True)
class Speaker:
    name: str
    company: str


@dataclass(frozen=True, slots=True)
class Talk:
    id: str
    source_id: str
    audio_path: Path
    start_s: float
    end_s: float
    title: str
    speakers: tuple[Speaker, ...]


@dataclass(frozen=True, slots=True)
class Manifest:
    talks: tuple[Talk, ...]
    fixed_context_bias: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Score:
    talk_id: str
    arm: str
    matched: tuple[str, ...]
    missed: tuple[str, ...]

    @property
    def numerator(self) -> int:
        return len(self.matched)

    @property
    def denominator(self) -> int:
        return self.numerator + len(self.missed)

    @property
    def accuracy(self) -> float:
        return self.numerator / self.denominator if self.denominator else 0.0


def load_manifest(path: Path) -> Manifest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    raw_talks = raw.get("talks")
    if not isinstance(raw_talks, list) or not raw_talks:
        raise ValueError("manifest talks must be a non-empty array")
    root = path.parent
    talks: list[Talk] = []
    ids: set[str] = set()
    for index, item in enumerate(raw_talks):
        if not isinstance(item, dict):
            raise ValueError(f"talks[{index}] must be an object")
        talk_id = _required(item, "id", index)
        if talk_id in ids:
            raise ValueError(f"duplicate talk id {talk_id!r}")
        ids.add(talk_id)
        start_s = float(item.get("start_s", -1))
        end_s = float(item.get("end_s", -1))
        if start_s < 0 or end_s <= start_s:
            raise ValueError(f"talks[{index}] needs end_s > start_s >= 0")
        raw_speakers = item.get("speakers")
        if not isinstance(raw_speakers, list) or not raw_speakers:
            raise ValueError(f"talks[{index}].speakers must be a non-empty array")
        talks.append(
            Talk(
                id=talk_id,
                source_id=_required(item, "source_id", index),
                audio_path=(root / _required(item, "audio_path", index)).resolve(),
                start_s=start_s,
                end_s=end_s,
                title=_required(item, "title", index),
                speakers=tuple(
                    Speaker(
                        name=_required(speaker, "name", index),
                        company=_required(speaker, "company", index),
                    )
                    for speaker in raw_speakers
                    if isinstance(speaker, dict)
                ),
            )
        )
    raw_fixed = raw.get("fixed_context_bias", [])
    if not isinstance(raw_fixed, list) or not all(isinstance(term, str) for term in raw_fixed):
        raise ValueError("fixed_context_bias must be a string array")
    return Manifest(tuple(talks), tuple(term.strip() for term in raw_fixed if term.strip()))


def _required(item: dict[str, Any], name: str, index: int) -> str:
    value = item.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"talks[{index}].{name} must be a non-empty string")
    return value.strip()


def _normal(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def proper_noun_terms(talk: Talk, manifest: Manifest) -> list[str]:
    terms = [speaker.name for speaker in talk.speakers]
    terms.extend(speaker.company for speaker in talk.speakers)
    frequencies: dict[str, int] = {}
    for item in manifest.talks:
        for key in {_normal(match.group(0)) for match in _TOKEN.finditer(item.title)}:
            frequencies[key] = frequencies.get(key, 0) + 1
    terms.extend(
        source
        for source in (match.group(0) for match in _TOKEN.finditer(talk.title))
        if len(source) >= 4
        and _normal(source) not in _STOP_WORDS
        and frequencies[_normal(source)] == 1
    )
    out: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = _normal(term)
        if key not in seen:
            seen.add(key)
            out.append(term)
    return out


def score_transcript(talk_id: str, arm: str, transcript: str, terms: list[str]) -> Score:
    normalized = _normal(transcript)
    matched: list[str] = []
    missed: list[str] = []
    for term in terms:
        key = _normal(term)
        found = re.search(rf"(?<!\w){re.escape(key)}(?!\w)", normalized) is not None
        (matched if found else missed).append(term)
    return Score(talk_id, arm, tuple(matched), tuple(missed))


def format_report(scores: list[Score]) -> str:
    lines = [LIMITATION, "", "# Proper-noun term accuracy", ""]
    for score in scores:
        lines.extend(
            [
                f"## {score.talk_id} / {score.arm}",
                "",
                f"matched: {', '.join(score.matched) or '(none)'}",
                f"missed: {', '.join(score.missed) or '(none)'}",
                f"score: {score.numerator}/{score.denominator} ({score.accuracy:.1%})",
                "",
            ]
        )
    lines.append("## Macro average")
    lines.append("")
    for arm in ARMS:
        arm_scores = [score.accuracy for score in scores if score.arm == arm]
        average = sum(arm_scores) / len(arm_scores) if arm_scores else 0.0
        lines.append(f"{arm}: {average:.1%}")
    return "\n".join(lines).rstrip() + "\n"


def _edition(manifest: Manifest) -> dict[str, Any]:
    """The private manifest, shaped like the edition object the builder reads.

    The eval runs on audio the repository does not ship, so there is no fixture
    to load here — but the builder stays the one in `mcp`, because an arm scored
    against a second bias algorithm would not be comparable to the first.
    """
    return {
        "slug": "aie-paris-2025-private-eval",
        "tags": {"edition": "series:aie-paris-2025-private-eval"},
        "context_bias": {"fixed": list(manifest.fixed_context_bias)},
        "sessions": [
            {
                "title": talk.title,
                "speakers": [
                    {"name": speaker.name, "company": speaker.company}
                    for speaker in talk.speakers
                ],
            }
            for talk in manifest.talks
        ],
    }


def indexed_transcript(db_path: Path, talk: Talk) -> str:
    conn = open_read_connection(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM videos WHERE source = 'youtube' AND source_id = ?",
            (talk.source_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"{talk.id}: source {talk.source_id!r} is not indexed")
        cues = conn.execute(
            "SELECT text FROM cues WHERE video_id = ? AND end_s > ? AND start_s < ? "
            "ORDER BY start_s, seq",
            (int(row["id"]), talk.start_s, talk.end_s),
        ).fetchall()
        return " ".join(str(cue["text"]) for cue in cues)
    finally:
        conn.close()


def _clip(talk: Talk, destination: Path) -> None:
    if not talk.audio_path.is_file():
        raise ValueError(f"{talk.id}: audio file does not exist: {talk.audio_path}")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{talk.start_s:.3f}",
            "-t",
            f"{talk.end_s - talk.start_s:.3f}",
            "-i",
            str(talk.audio_path),
            "-vn",
            "-c:a",
            "flac",
            str(destination),
        ],
        check=True,
    )


def worker_transcript(worker_url: str, clip: Path, context_bias: list[str]) -> str:
    result = post_multipart(
        f"{worker_url.rstrip('/')}/v1/audio/transcriptions",
        files=[("file", clip)],
        fields={
            "response_format": "verbose_json",
            "temperature": "0",
            "timestamp_granularities[]": "word",
            "context_bias": json.dumps(context_bias, ensure_ascii=False, separators=(",", ":")),
        },
    )
    if result.status != 200 or not isinstance(result.body, dict):
        raise RuntimeError(f"worker transcription failed with HTTP {result.status}: {result.body}")
    return str(result.body.get("text") or "")


def run_eval(manifest: Manifest, db_path: Path, worker_url: str) -> list[Score]:
    bias = build_context_bias(_edition(manifest))
    scores: list[Score] = []
    with tempfile.TemporaryDirectory(prefix="vidtheque-aie-eval-") as scratch:
        root = Path(scratch)
        for index, talk in enumerate(manifest.talks):
            terms = proper_noun_terms(talk, manifest)
            scores.append(
                score_transcript(
                    talk.id, ARMS[0], indexed_transcript(db_path, talk), terms
                )
            )
            clip = root / f"{index:02d}.flac"
            _clip(talk, clip)
            scores.append(
                score_transcript(
                    talk.id, ARMS[1], worker_transcript(worker_url, clip, []), terms
                )
            )
            scores.append(
                score_transcript(
                    talk.id, ARMS[2], worker_transcript(worker_url, clip, bias), terms
                )
            )
    return scores


def preflight(manifest: Manifest, price_per_minute: float, checked_at: str) -> str:
    minutes = sum(talk.end_s - talk.start_s for talk in manifest.talks) / 60.0
    requests = len(manifest.talks) * 2
    projected = minutes * 2 * price_per_minute
    return "\n".join(
        [
            f"price source: operator --price-per-minute ({price_per_minute:g} per minute)",
            f"price entered at: {checked_at}",
            f"audio minutes per arm: {minutes:.2f}",
            f"request count: {requests}",
            "Voxtral arms: 2",
            f"projected cost: {projected:.2f}",
        ]
    )


def price_per_minute(value: str) -> float:
    """A price the preflight can actually put in front of the operator.

    `float` alone accepts `-1`, `nan` and `inf`, and the preflight exists to
    show a real projected cost before paid calls: `projected cost: nan` is not
    a figure anyone can approve.
    """
    try:
        price = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from exc
    if not math.isfinite(price) or price <= 0:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a finite price above zero"
        )
    return price


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--worker-url", default="http://127.0.0.1:8081")
    parser.add_argument("--price-per-minute", type=price_per_minute, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_manifest(args.manifest)
    checked_at = datetime.now(UTC).isoformat()
    print(LIMITATION)
    print()
    print(preflight(manifest, args.price_per_minute, checked_at))
    if not args.execute:
        print("dry run: no audio was submitted; pass --execute to run both paid arms")
        return 0
    scores = run_eval(manifest, args.data_dir / "vidtheque.db", args.worker_url)
    report = format_report(scores)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
