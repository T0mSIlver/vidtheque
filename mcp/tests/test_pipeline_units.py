"""The pure halves of the pipeline: info dicts, caption formats, chunking.

Every one of these runs on data, not on a network or a model. The canned info
dict is the shape research §5.1 verified against a real extraction; the json3
payloads are the shape §5.4 verified against real timedtext responses,
rolling-window artefacts included.
"""

from __future__ import annotations

import json

import pytest

from vidtheque_mcp.pipeline import captions, chunking, keyframes, sources

from .pipeline_fakes import (
    AUTO_JSON3,
    INFO,
    MANUAL_JSON3,
    PLAYLIST_INFO,
    VIDEO_URL,
    WHISPERX_RESPONSE,
)

# --------------------------------------------------------------------- yt-dlp


def test_parse_info_lands_every_field_the_schema_has_a_column_for() -> None:
    meta = sources.parse_info(INFO, VIDEO_URL, "yt-dlp-2026.7.4")
    assert meta.source == "youtube"
    assert meta.source_id == "aB3dEfG7hIj"
    assert meta.channel_name == "GPU MODE"
    assert meta.channel_id == "UC_gpumode"
    assert meta.published_at == 1_770_000_000
    assert meta.duration_s == 620.0
    assert meta.language == "en"
    assert len(meta.chapters) == 2
    assert meta.chapters[1].title == "the block table"


def test_heatmap_is_captured_verbatim() -> None:
    """DECISIONS.md: "most replayed" is captured at index time. Nobody else has it."""
    meta = sources.parse_info(INFO, VIDEO_URL)
    assert meta.heatmap_json is not None
    assert json.loads(meta.heatmap_json)[0]["value"] == 0.91


def test_chapters_json_is_the_payload_we_were_given() -> None:
    meta = sources.parse_info(INFO, VIDEO_URL)
    assert json.loads(meta.chapters_json or "[]") == INFO["chapters"]


def test_upload_date_is_used_when_there_is_no_timestamp() -> None:
    info = dict(INFO)
    info.pop("timestamp")
    info.pop("release_timestamp", None)
    meta = sources.parse_info(info, VIDEO_URL)
    assert meta.published_at == 1_770_681_600  # 2026-02-10T00:00:00Z


def test_live_and_private_videos_are_refused_before_anything_is_downloaded() -> None:
    for field, value in (("live_status", "is_live"), ("availability", "private")):
        info = dict(INFO)
        info[field] = value
        with pytest.raises(sources.SourceError):
            sources.parse_info(info, VIDEO_URL)


def test_a_lifecycle_state_is_a_later_and_a_private_video_is_a_never() -> None:
    """Both were `Unavailable`, so a premiere permanently failed like a takedown."""
    for status in ("is_live", "is_upcoming", "post_live"):
        info = dict(INFO) | {"live_status": status}
        with pytest.raises(sources.NotYetAvailable):
            sources.parse_info(info, VIDEO_URL)

    for availability in ("private", "needs_auth", "subscriber_only"):
        info = dict(INFO) | {"availability": availability}
        with pytest.raises(sources.Unavailable) as caught:
            sources.parse_info(info, VIDEO_URL)
        assert not isinstance(caught.value, sources.NotYetAvailable)


def test_a_premiere_carries_its_own_countdown() -> None:
    import time

    info = dict(INFO) | {
        "live_status": "is_upcoming",
        "release_timestamp": int(time.time()) + 900,
    }
    with pytest.raises(sources.NotYetAvailable) as caught:
        sources.parse_info(info, VIDEO_URL)
    assert caught.value.retry_after_s == pytest.approx(900, abs=5)

    # A release time in the past says nothing useful; the caller's default wins.
    info["release_timestamp"] = int(time.time()) - 900
    with pytest.raises(sources.NotYetAvailable) as caught:
        sources.parse_info(info, VIDEO_URL)
    assert caught.value.retry_after_s is None


def test_subtitle_inventory_prefers_the_word_timed_format() -> None:
    meta = sources.parse_info(INFO, VIDEO_URL)
    auto = meta.track(("en",), automatic=True)
    manual = meta.track(("en",), automatic=False)
    assert auto is not None and auto.ext == "json3" and auto.word_timed
    # Manual json3 exists but carries no word offsets — the flag says so.
    assert manual is not None and not manual.word_timed


def test_description_links_carry_the_timestamp_on_their_line() -> None:
    meta = sources.parse_info(INFO, VIDEO_URL)
    by_url = {link.url: link for link in meta.links}
    assert by_url["https://example.com/paper"].t_s == 15.0
    assert by_url["https://example.com/sponsor"].t_s is None


@pytest.mark.parametrize(
    "url,container",
    [
        ("https://www.youtube.com/playlist?list=PL9", True),
        ("https://www.youtube.com/@AndrejKarpathy/videos", True),
        ("https://www.youtube.com/channel/UC_x/videos", True),
        ("https://youtu.be/aB3dEfG7hIj", False),
        # A watch URL with a list= is one video someone happened to copy out of
        # a playlist; noplaylist picks the video, which is what they meant.
        ("https://www.youtube.com/watch?v=aB3dEfG7hIj&list=PL9", False),
    ],
)
def test_container_detection_is_syntactic(url: str, container: bool) -> None:
    assert sources.looks_like_container(url) is container


def test_playlist_entries_are_capped_and_deduplicated() -> None:
    entries = sources.playlist_entries(PLAYLIST_INFO, 25)
    assert [e.source_id for e in entries] == ["aB3dEfG7hIj", "zZ9yY8xX7wV"]
    assert len(sources.playlist_entries(PLAYLIST_INFO, 1)) == 1


def test_every_pipeline_env_var_is_documented() -> None:
    """CLAUDE.md: `deploy/.env.example` is the document of record, and an env
    var without an entry there is a bug. Cheaper to assert than to remember."""
    import re
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "src/vidtheque_mcp/pipeline/settings.py"
    documented = (Path(__file__).resolve().parents[2] / "deploy/.env.example").read_text()
    names = set(re.findall(r'_env\(\s*"(VIDTHEQUE_[A-Z0-9_]+)"', source.read_text()))
    assert names, "the settings module stopped reading the environment"
    missing = sorted(name for name in names if f"\n{name}=" not in documented)
    assert not missing, f"undocumented env vars: {missing}"


def test_ytdlp_options_use_the_python_key_names_for_the_throttle() -> None:
    """The CLI is `--sleep-requests`; the Python key is `sleep_interval_requests`.

    Getting it wrong silently disables the throttle, and the throttle is the
    difference between a residential IP that keeps working and one that does
    not — a single invocation earned a 429 on its third subtitle request
    (research §5.5).
    """
    from vidtheque_mcp.pipeline.settings import PipelineSettings

    opts = sources.YtDlpSource(PipelineSettings())._base_opts()
    assert opts["sleep_interval_requests"] == 0.75
    assert opts["sleep_interval_subtitles"] == 5.0
    assert opts["sleep_interval"] == 10.0
    assert opts["max_sleep_interval"] == 20.0
    # ~200 machine-translated caption tracks, otherwise.
    assert opts["extractor_args"]["youtube"]["skip"] == ["translated_subs"]
    # We execute YouTube's JavaScript on the same box as the MCP server.
    assert opts["extractor_args"]["youtube-ejs"]["jitless"] == ["true"]
    assert "cookiefile" not in opts  # authenticated downloading is opt-in only


def test_only_two_subtitle_languages_are_ever_requested() -> None:
    from vidtheque_mcp.pipeline.settings import PipelineSettings

    settings = PipelineSettings(subtitle_langs=("en", "fr", "de", "es"))
    assert len(PipelineSettings.from_env().subtitle_langs) <= 2
    assert len(settings.subtitle_langs) == 4  # the cap is applied at parse time
    assert sources.parse_info(INFO, VIDEO_URL).track(("fr", "en"), automatic=True) is not None


# ------------------------------------------------------------------- captions


def test_auto_json3_gives_per_word_timings() -> None:
    cues = captions.cues_from_json3(AUTO_JSON3)
    assert [c.text for c in cues] == [
        "paged attention keeps a block table",
        "fragmentation drops to four percent",
    ]
    words = json.loads(cues[0].words_json() or "[]")
    # Word start = tStartMs + tOffsetMs (research §5.4).
    assert words[0][0] == "paged" and words[0][1] == pytest.approx(4.08)
    assert words[1][1] == pytest.approx(4.32)


def test_the_rolling_window_does_not_duplicate_text() -> None:
    """The ASR stream repeats the growing caption line; only `segs` events count."""
    payload = json.loads(AUTO_JSON3)
    assert len(payload["events"]) == 4  # two of them are window/append noise
    assert len(captions.cues_from_json3(AUTO_JSON3)) == 2


def test_manual_json3_has_no_word_timings() -> None:
    cues = captions.cues_from_json3(MANUAL_JSON3, word_timed=False)
    assert len(cues) == 2
    assert all(cue.words_json() is None for cue in cues)


def test_whisperx_cue_text_is_exactly_the_joined_words() -> None:
    """index-schema §1.4's invariant: highlight() spans map to word indices by
    walking the same list, so the two must agree character for character."""
    cues = captions.cues_from_verbose_json(WHISPERX_RESPONSE)
    assert len(cues) == 2
    for cue in cues:
        words = json.loads(cue.words_json() or "[]")
        assert cue.text == " ".join(word for word, _, _ in words)
    assert cues[0].avg_logprob == pytest.approx(-0.21)


def test_vtt_is_understood_as_a_last_resort() -> None:
    payload = (
        "WEBVTT\n\n"
        "00:00:04.080 --> 00:00:08.280\n<c>paged</c> attention keeps a block table\n\n"
        "00:00:08.400 --> 00:00:11.400\nfragmentation drops to four percent\n"
    )
    cues = captions.cues_from_vtt(payload)
    assert [c.text for c in cues] == [
        "paged attention keeps a block table",
        "fragmentation drops to four percent",
    ]
    assert cues[0].start_s == pytest.approx(4.08)


def test_cues_never_overlap_after_tidying() -> None:
    raw = [
        captions.CueDraft(0.0, 9.0, "one"),
        captions.CueDraft(2.0, 4.0, "two"),
        captions.CueDraft(2.0, 4.0, "two"),
    ]
    tidy = captions._tidy(raw)
    assert [c.text for c in tidy] == ["one", "two"]
    assert tidy[0].end_s <= tidy[1].start_s


# ------------------------------------------------------------------ chunking


def _cues(count: int, step: float = 3.0) -> list[captions.CueDraft]:
    return [
        captions.CueDraft(i * step, i * step + step - 0.2, f"sentence number {i}")
        for i in range(count)
    ]


def test_chunks_are_45_seconds_with_15_of_overlap() -> None:
    chunks = chunking.build_chunks(_cues(40), 45.0, 15.0)
    assert chunks
    for chunk in chunks:
        assert chunk.end_s - chunk.start_s <= 45.0 + 3.0
    # Stride is target - overlap, so consecutive windows start 30 s apart.
    assert chunks[1].start_s - chunks[0].start_s == pytest.approx(30.0, abs=3.0)
    # Overlap means the second window starts before the first one ends.
    assert chunks[1].start_s < chunks[0].end_s


def test_chunk_spans_are_contiguous_and_cover_every_cue() -> None:
    cues = _cues(20)
    chunks = chunking.build_chunks(cues, 45.0, 15.0)
    covered = set()
    for chunk in chunks:
        assert chunk.first_index <= chunk.last_index
        covered.update(range(chunk.first_index, chunk.last_index + 1))
        assert chunk.text == " ".join(
            c.text for c in cues[chunk.first_index : chunk.last_index + 1]
        )
    assert covered == set(range(len(cues)))


def test_chunking_an_empty_transcript_is_empty_not_an_error() -> None:
    assert chunking.build_chunks([], 45.0, 15.0) == []


def test_one_very_long_cue_still_produces_a_chunk() -> None:
    chunks = chunking.build_chunks([captions.CueDraft(0.0, 300.0, "one long take")], 45.0, 15.0)
    assert len(chunks) == 1


def _coverage(cues: list[captions.CueDraft], target: float, overlap: float) -> set[int]:
    covered: set[int] = set()
    for chunk in chunking.build_chunks(cues, target, overlap):
        covered.update(range(chunk.first_index, chunk.last_index + 1))
    return covered


def test_a_long_cue_across_the_stride_boundary_is_not_dropped() -> None:
    """The review's trigger, exactly: 0-20, 25-55, 60-62 at 45 s / 15 s.

    The middle cue does not fit the first window, and its start (25) is before
    the stride (30), so the time-based advance scan skipped past it to the cue
    at 60 — permanently omitting it from every chunk, and so from the vector
    index. Nothing anywhere would have noticed.
    """
    cues = [
        captions.CueDraft(0.0, 20.0, "the first thing said"),
        captions.CueDraft(25.0, 55.0, "the middle one that used to vanish"),
        captions.CueDraft(60.0, 62.0, "the last thing said"),
    ]
    chunks = chunking.build_chunks(cues, 45.0, 15.0)
    assert _coverage(cues, 45.0, 15.0) == {0, 1, 2}
    assert any("used to vanish" in chunk.text for chunk in chunks)


def test_every_cue_lands_in_a_chunk_whatever_the_layout() -> None:
    """The invariant, over the layouts that break the naive stride: silences,
    runaway cues, bursts, and cues that straddle the window edge."""
    import random

    rng = random.Random(20260809)
    for trial in range(200):
        cues: list[captions.CueDraft] = []
        clock = 0.0
        for index in range(rng.randint(1, 40)):
            clock += rng.choice([0.0, 0.2, 1.0, 5.0, 30.0, 90.0])
            span = rng.choice([0.5, 2.0, 8.0, 29.0, 30.0])
            cues.append(captions.CueDraft(clock, clock + span, f"cue {index}"))
            clock += span
        target = rng.choice([10.0, 45.0, 60.0])
        overlap = rng.choice([0.0, 5.0, 15.0, 45.0])
        assert _coverage(cues, target, overlap) == set(range(len(cues))), (
            trial,
            target,
            overlap,
            [(c.start_s, c.end_s) for c in cues],
        )


# ----------------------------------------------------------------- keyframes


def test_phash_is_stored_signed_because_sqlite_integers_are() -> None:
    """A raw 64-bit hash overflows on insert; the conversion round-trips."""
    assert keyframes.signed64(0xFFFF_FFFF_FFFF_FFFF) == -1
    assert keyframes.signed64(1) == 1
    assert -(2**63) <= keyframes.signed64(0x8000_0000_0000_0001) < 2**63


def test_a_single_scene_video_is_subdivided_rather_than_yielding_one_frame() -> None:
    shots = keyframes.subdivide([(0.0, 100.0)], 100.0, 25.0)
    assert len(shots) == 4
    assert shots[0].start_s == 0.0 and shots[-1].end_s == 100.0


def test_detection_that_returns_nothing_still_produces_shots() -> None:
    assert keyframes.subdivide([], 30.0, 25.0)


def test_the_keyframe_budget_thins_uniformly() -> None:
    shots = keyframes.subdivide([(0.0, 1000.0)], 1000.0, 5.0)
    thinned = keyframes.thin(shots, 10)
    assert len(thinned) == 10
    assert thinned[0].start_s == 0.0
    assert thinned[-1].start_s > shots[len(shots) // 2].start_s
