from __future__ import annotations

from pathlib import Path

from bench.aie_paris_eval import (
    ARMS,
    LIMITATION,
    Manifest,
    Speaker,
    Talk,
    format_report,
    preflight,
    proper_noun_terms,
    score_transcript,
)
from bench.long_vod_measure import Snapshot, compare, snapshot


def manifest() -> Manifest:
    return Manifest(
        talks=(
            Talk(
                id="talk-one",
                source_id="private-one",
                audio_path=Path("private.flac"),
                start_s=10.0,
                end_s=70.0,
                title="Voxtral for Crème Retrieval",
                speakers=(Speaker("Lélio Lavaud", "Mistral"),),
            ),
            Talk(
                id="talk-two",
                source_id="private-two",
                audio_path=Path("private.flac"),
                start_s=80.0,
                end_s=140.0,
                title="Reliable Agent Evaluation",
                speakers=(Speaker("Ada Example", "Example Labs"),),
            ),
        ),
        fixed_context_bias=("Voxtral", "RRF"),
    )


def test_metric_uses_nfkc_casefold_whitespace_and_exact_phrase_boundaries() -> None:
    terms = ["Lélio Lavaud", "Mistral", "Crème", "RRF"]
    score = score_transcript(
        "talk-one",
        ARMS[2],
        "LÉLIO   LAVAUD spoke at mistral about crème. RRFish is different.",
        terms,
    )
    assert score.matched == ("Lélio Lavaud", "Mistral", "Crème")
    assert score.missed == ("RRF",)
    assert score.accuracy == 0.75


def test_terms_and_report_format_are_pinned_with_recorded_transcripts() -> None:
    fixture = manifest()
    terms = proper_noun_terms(fixture.talks[0], fixture)
    assert terms[:2] == ["Lélio Lavaud", "Mistral"]
    assert "Voxtral" in terms and "Retrieval" in terms
    scores = [
        score_transcript("talk-one", arm, "Lélio Lavaud Mistral Voxtral", terms)
        for arm in ARMS
    ]
    report = format_report(scores)
    assert report.startswith(LIMITATION)
    assert "matched: Lélio Lavaud, Mistral, Voxtral" in report
    assert "missed: Crème, Retrieval" in report
    assert "score: 3/5 (60.0%)" in report
    assert "indexed whisperX: 60.0%" in report


def test_cost_preflight_counts_both_paid_arms() -> None:
    text = preflight(manifest(), 0.01, "2026-09-16T00:00:00+00:00")
    assert "audio minutes per arm: 2.00" in text
    assert "request count: 4" in text
    assert "Voxtral arms: 2" in text
    assert "projected cost: 0.04" in text


def test_long_vod_snapshot_reads_the_seeded_database(seeded) -> None:
    measured = snapshot(seeded.data_dir)
    assert measured.database_bytes > 0
    assert measured.counts["cues"] > 0
    assert measured.counts["chunks"] > 0
    assert measured.counts["keyframes"] > 0
    assert measured.counts["ocr_lines"] > 0
    assert measured.counts["vec_chunks"] > 0
    assert measured.counts["vec_frames"] > 0


def test_long_vod_comparison_enforces_both_follow_gates() -> None:
    names = ("cues", "chunks", "keyframes", "ocr_lines", "vec_chunks", "vec_frames")
    zero = dict.fromkeys(names, 0)
    before = Snapshot("before", "/data", 100, zero)
    after_counts = dict(zero, vec_chunks=961, vec_frames=601)
    result = compare(before, Snapshot("after", "/data", 200, after_counts))
    assert result["follow_gate"] == "blocked"
    assert len(result["blocks"]) == 2
    assert result["delta"]["database_bytes"] == 100
