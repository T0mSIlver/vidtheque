"""End to end: `index-video` creates a job, the runner executes it, and the
content it wrote comes back out of `search`.

The only real things in the loop are the database, the scene detector and the
perceptual hasher. yt-dlp is canned info dicts and a synthesized clip; the
worker is the `WorkerAPI` protocol with no HTTP behind it. Nothing downloads a
model, needs a GPU, or reaches the network.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from vidtheque_mcp.app import Assembled, assemble
from vidtheque_mcp.config import Settings
from vidtheque_mcp.jobs import store as jobs_store
from vidtheque_mcp.pipeline import build_pipeline
from vidtheque_mcp.pipeline.settings import PipelineSettings
from vidtheque_mcp.tools import indexing, search

from .pipeline_fakes import PLAYLIST_URL, VIDEO_URL, FakeWorker, canned_source

# The politeness sleeps are real defaults and would add a minute per video to
# the suite; the source is a dictionary, so there is nothing to be polite to.
FAST = PipelineSettings(between_videos_s=0.0, sleep_subtitles_s=0.0, max_shot_seconds=2.0)


def body(result) -> str:
    from mcp_types import TextContent

    return "\n".join(b.text for b in result.content if isinstance(b, TextContent))


def structured(result) -> dict:
    assert result.structured_content is not None
    return result.structured_content


class Harness:
    """An assembled app whose pipeline is wired to fakes."""

    def __init__(self, parts: Assembled, worker: FakeWorker, source) -> None:
        self.parts = parts
        self.worker = worker
        self.source = source

    @property
    def deps(self):
        return self.parts.deps

    @property
    def db(self):
        return self.parts.db

    async def index(self, **kwargs) -> str:
        result = await indexing.index_video(self.deps, **kwargs)
        assert not result.is_error, body(result)
        job_id = structured(result)["job_id"]
        assert job_id is not None
        return str(job_id)

    async def run(self) -> bool:
        return await self.parts.runner.run_once()

    async def rows(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return await self.db.read(lambda c: c.execute(sql, params).fetchall())

    async def one(self, sql: str, params: tuple = ()):
        rows = await self.rows(sql, params)
        return rows[0] if rows else None

    async def stages(self, source_id: str = "aB3dEfG7hIj") -> dict[str, sqlite3.Row]:
        rows = await self.rows(
            "SELECT s.* FROM video_stages s JOIN videos v ON v.id = s.video_id "
            "WHERE v.source_id = ?",
            (source_id,),
        )
        return {str(row["stage"]): row for row in rows}

    async def events(self) -> list[str]:
        return [str(row["message"]) for row in await self.rows("SELECT message FROM job_events")]


async def harness(
    settings: Settings,
    clip: Path | None,
    *,
    worker: FakeWorker | None = None,
    pipeline_settings: PipelineSettings = FAST,
) -> Harness:
    fake_worker = worker or FakeWorker()
    source = canned_source(clip)
    parts = assemble(settings, embeddings=fake_worker, run_pipeline=False)
    await parts.db.open()
    parts.runner.pipeline = build_pipeline(
        settings,
        parts.db,
        worker=fake_worker,
        source=source,
        pipeline_settings=pipeline_settings,
    )
    return Harness(parts, fake_worker, source)


@pytest.fixture
async def indexed(settings: Settings, clip: Path):
    """One video, indexed end to end, with every stage on."""
    parts = await harness(settings, clip)
    try:
        job_id = await parts.index(url=VIDEO_URL, tags="topic:attention")
        assert await parts.run() is True
        parts.job_id = job_id  # type: ignore[attr-defined]
        yield parts
    finally:
        await parts.db.close()
        parts.parts.auth.close()


# --------------------------------------------------------------------- happy


async def test_the_job_finishes_and_the_video_is_ready(indexed: Harness) -> None:
    job = await indexed.one("SELECT * FROM jobs WHERE public_id = ?", (indexed.job_id,))
    assert job["state"] == "done"
    assert (job["n_done"], job["n_failed"]) == (1, 0)

    video = await indexed.one("SELECT * FROM videos WHERE source_id = 'aB3dEfG7hIj'")
    assert video["index_state"] == "ready"
    assert video["indexed_at"] is not None
    assert video["title"] == "Paged attention, end to end"
    assert video["channel_name"] == "GPU MODE"
    assert video["duration_s"] == 620.0
    # The heatmap is captured at index time — cheap now, and nobody else has it.
    assert '"value": 0.91' in str(video["heatmap_json"])


async def test_every_stage_is_recorded_with_what_produced_it(indexed: Harness) -> None:
    stages = await indexed.stages()
    assert set(stages) == {"fetch", "stt", "chunk", "text_embed", "keyframe", "ocr", "frame_embed"}
    assert all(row["state"] == "done" for row in stages.values()), {
        k: (v["state"], v["error"]) for k, v in stages.items()
    }
    # The four model-bearing stages must record exactly the `config` value, or
    # the reindex planner in index-schema §1.3 reads them as out of date.
    config = indexed.db.config
    assert stages["stt"]["model_key"] == config["stt.model"]
    assert stages["text_embed"]["model_key"] == config["text_embed.model"]
    assert stages["ocr"]["model_key"] == config["ocr.model"]
    assert stages["frame_embed"]["model_key"] == config["frame_embed.model"]
    assert "scenedetect" in stages["keyframe"]["model_key"]
    assert "yt-dlp" in stages["fetch"]["model_key"]


async def test_transcript_chapters_and_links_land_in_their_tables(indexed: Harness) -> None:
    cues = await indexed.rows("SELECT * FROM cues ORDER BY seq")
    assert [c["text"] for c in cues] == [
        "paged attention keeps a block table",
        "fragmentation drops to four percent",
    ]
    assert all(c["origin"] == "whisperx" for c in cues)
    assert all(c["words_json"] for c in cues)
    # id order == seq order == time order, the contiguity invariant §1.4 needs.
    assert [c["id"] for c in cues] == sorted(c["id"] for c in cues)

    chapters = await indexed.rows("SELECT * FROM chapters ORDER BY seq")
    assert [c["title"] for c in chapters] == ["intro", "the block table"]
    links = await indexed.rows("SELECT * FROM video_links ORDER BY seq")
    assert any(link["t_s"] == 15.0 for link in links)


async def test_chunks_and_their_vectors_exist_and_line_up(indexed: Harness) -> None:
    chunks = await indexed.rows("SELECT * FROM chunks ORDER BY seq")
    assert chunks
    vectors = await indexed.rows("SELECT chunk_id FROM vec_chunks")
    assert len(vectors) == len(chunks)
    for chunk in chunks:
        assert chunk["first_cue_id"] <= chunk["last_cue_id"]
        assert chunk["n_chars"] == len(chunk["text"])


async def test_keyframes_ocr_and_frame_vectors_land(indexed: Harness) -> None:
    frames = await indexed.rows("SELECT * FROM keyframes ORDER BY ord")
    assert frames
    for frame in frames:
        assert frame["jpeg_path"].startswith("keyframes/aB3dEfG7hIj/")
        assert (indexed.parts.settings.data_dir / frame["jpeg_path"]).exists()
    # Duplicates are kept as rows and never OCR'd or embedded.
    duplicates = [f for f in frames if f["dup_of"] is not None]
    assert all(f["ocr_state"] == "skipped" for f in duplicates)

    lines = await indexed.rows("SELECT * FROM ocr_lines")
    assert lines
    for line in lines:
        assert 0.0 <= line["x0"] <= 1.0 and 0.0 <= line["y1"] <= 1.0  # normalized
        assert line["video_id"] == frames[0]["video_id"]  # denormalized on purpose

    live = [f for f in frames if f["dup_of"] is None]
    assert len(await indexed.rows("SELECT keyframe_id FROM vec_frames")) == len(live)


async def test_the_indexed_content_is_searchable(indexed: Harness) -> None:
    """The whole point: what the pipeline wrote comes back out of `search`."""
    result = await search.run(indexed.deps, q="fragmentation", limit=5)
    text = body(result)
    assert "[transcript]" in text
    assert "https://youtu.be/aB3dEfG7hIj?t=" in text
    assert "aB3dEfG7hIj" in {r["video_id"] for r in structured(result)["results"]}

    # `nvidia-smi` survives as one token because the OCR index uses
    # `unicode61 tokenchars '_-./'` — that is the tokenizer decision paying off.
    on_screen = await search.run(indexed.deps, q="nvidia-smi", content_type="ocr", limit=5)
    assert "aB3dEfG7hIj" in body(on_screen)
    assert "[ocr]" in body(on_screen)


async def test_tags_from_the_job_are_applied(indexed: Harness) -> None:
    rows = await indexed.rows("SELECT t.full FROM video_tags vt JOIN tags t ON t.id = vt.tag_id")
    assert [row["full"] for row in rows] == ["topic:attention"]


async def test_the_source_video_is_deleted_and_the_audio_is_kept(indexed: Harness) -> None:
    """DECISIONS.md #3: the corpus is the index; the mp4 is scaffolding."""
    video = await indexed.one("SELECT audio_path, media_path FROM videos")
    assert video["media_path"] is None
    assert video["audio_path"] is not None
    root = indexed.parts.settings.data_dir
    assert (root / str(video["audio_path"])).exists()
    assert not list((root / "media").glob("aB3dEfG7hIj.*"))


# ------------------------------------------------------------------ degraded


async def test_no_worker_means_auto_captions_and_a_note_saying_so(
    settings: Settings, clip: Path
) -> None:
    """The zero-GPU path: word-timed captions, no audio download, and it says so."""
    parts = await harness(settings, clip, worker=FakeWorker(healthy=False))
    try:
        await parts.index(url=VIDEO_URL, channels="transcript")
        assert await parts.run() is True

        cues = await parts.rows("SELECT * FROM cues ORDER BY seq")
        assert [c["origin"] for c in cues] == ["yt_auto", "yt_auto"]
        assert all(c["words_json"] for c in cues)  # json3 carries per-word offsets
        stages = await parts.stages()
        assert stages["stt"]["state"] == "done"
        assert stages["stt"]["model_key"] == "youtube-asr-en"
        assert "audio" not in parts.source.downloads  # nothing to send it to
        assert any("CPU-only" in message for message in await parts.events())
        video = await parts.one("SELECT index_state FROM videos")
        assert video["index_state"] == "ready"
    finally:
        await parts.db.close()
        parts.parts.auth.close()


async def test_a_failed_stage_keeps_the_finished_ones(settings: Settings, clip: Path) -> None:
    parts = await harness(settings, clip, worker=FakeWorker(fail={"ocr"}))
    try:
        job_id = await parts.index(url=VIDEO_URL)
        assert await parts.run() is True

        stages = await parts.stages()
        assert stages["ocr"]["state"] == "failed"
        assert "refuses ocr" in str(stages["ocr"]["error"])
        for name in ("fetch", "stt", "chunk", "text_embed", "keyframe", "frame_embed"):
            assert stages[name]["state"] == "done", name
        # The item still completes: a video with no OCR is still a video you
        # can find by what was said in it.
        job = await parts.one("SELECT state FROM jobs WHERE public_id = ?", (job_id,))
        assert job["state"] == "done"
        assert (await parts.one("SELECT index_state FROM videos"))["index_state"] == "ready"
        # A failure never destroys the input to a retry.
        assert list((parts.parts.settings.data_dir / "media").glob("aB3dEfG7hIj.*"))
    finally:
        await parts.db.close()
        parts.parts.auth.close()


async def test_resume_reruns_the_failed_stage_and_nothing_else(
    settings: Settings, clip: Path
) -> None:
    worker = FakeWorker(fail={"ocr"})
    parts = await harness(settings, clip, worker=worker)
    try:
        await parts.index(url=VIDEO_URL)
        assert await parts.run() is True
        before_cues = [r["id"] for r in await parts.rows("SELECT id FROM cues ORDER BY id")]
        before_frames = [r["id"] for r in await parts.rows("SELECT id FROM keyframes ORDER BY id")]
        downloads = list(parts.source.downloads)
        first_pass = len(worker.calls)

        worker.fail = set()  # the worker came back
        video = await parts.one("SELECT id FROM videos")
        video_id = int(video["id"])
        await parts.db.write(
            lambda c: jobs_store.create_job(c, "index", {}, [(VIDEO_URL, video_id)])
        )
        assert await parts.run() is True

        stages = await parts.stages()
        assert stages["ocr"]["state"] == "done"
        # Re-running stt or keyframe would reallocate these ids.
        assert [r["id"] for r in await parts.rows("SELECT id FROM cues ORDER BY id")] == before_cues
        assert [
            r["id"] for r in await parts.rows("SELECT id FROM keyframes ORDER BY id")
        ] == before_frames
        # The second pass calls the worker for OCR and nothing else.
        assert worker.calls[first_pass:] == ["ocr:3"]
        assert parts.source.downloads == downloads  # nothing re-downloaded
        assert await parts.rows("SELECT id FROM ocr_lines")
    finally:
        await parts.db.close()
        parts.parts.auth.close()


async def test_cancellation_is_honoured_between_stages(settings: Settings, clip: Path) -> None:
    parts_holder: list[Harness] = []

    async def cancel_now() -> None:
        harness_ = parts_holder[0]
        job_id = harness_.job_id  # type: ignore[attr-defined]
        await harness_.db.write(lambda c: jobs_store.request_cancel(c, job_id))

    worker = FakeWorker(on_transcribe=cancel_now)
    parts = await harness(settings, clip, worker=worker)
    parts_holder.append(parts)
    try:
        parts.job_id = await parts.index(url=VIDEO_URL)  # type: ignore[attr-defined]
        assert await parts.run() is True

        job = await parts.one("SELECT state FROM jobs WHERE public_id = ?", (parts.job_id,))
        assert job["state"] == "cancelled"
        item = await parts.one("SELECT state FROM job_items")
        assert item["state"] == "cancelled"
        # The stages that finished stay finished; nothing is rolled back.
        stages = await parts.stages()
        assert stages["fetch"]["state"] == "done"
        assert stages["stt"]["state"] == "done"
        assert "keyframe" not in stages
    finally:
        await parts.db.close()
        parts.parts.auth.close()


async def test_channels_transcript_skips_the_frame_stages(settings: Settings, clip: Path) -> None:
    parts = await harness(settings, clip)
    try:
        await parts.index(url=VIDEO_URL, channels="transcript")
        assert await parts.run() is True
        stages = await parts.stages()
        for name in ("keyframe", "ocr", "frame_embed"):
            # `skipped`, not `pending`: a deliberate choice must never be
            # reported as missing data.
            assert stages[name]["state"] == "skipped", name
        assert "video" not in parts.source.downloads
        assert not await parts.rows("SELECT id FROM keyframes")
    finally:
        await parts.db.close()
        parts.parts.auth.close()


async def test_vector_writes_stop_when_the_corpus_config_disagrees(
    settings: Settings, clip: Path
) -> None:
    """The anti-drift assertion: mixing embedding spaces is worse than no vectors."""
    parts = await harness(settings, clip, worker=FakeWorker(text_dim=768))
    try:
        await parts.index(url=VIDEO_URL, channels="transcript")
        assert await parts.run() is True
        stages = await parts.stages()
        assert stages["text_embed"]["state"] == "skipped"
        assert "768" in str(stages["text_embed"]["error"])
        assert await parts.rows("SELECT id FROM chunks")  # the text is still there
        assert not await parts.rows("SELECT chunk_id FROM vec_chunks")
    finally:
        await parts.db.close()
        parts.parts.auth.close()


async def test_captions_only_never_asks_the_worker_to_transcribe(
    settings: Settings, clip: Path
) -> None:
    """The zero-GPU install path: `WORKER_URL` pointing at nothing still works."""
    parts = await harness(
        settings,
        clip,
        pipeline_settings=PipelineSettings(
            between_videos_s=0.0, sleep_subtitles_s=0.0, stt_policy="captions_only"
        ),
    )
    try:
        await parts.index(url=VIDEO_URL, channels="transcript")
        assert await parts.run() is True
        assert "transcribe" not in parts.worker.calls
        assert "audio" not in parts.source.downloads
        cues = await parts.rows("SELECT origin FROM cues")
        assert {c["origin"] for c in cues} == {"yt_auto"}
    finally:
        await parts.db.close()
        parts.parts.auth.close()


async def test_whisperx_only_refuses_to_index_a_caption_track(
    settings: Settings, clip: Path
) -> None:
    parts = await harness(
        settings,
        clip,
        worker=FakeWorker(healthy=False, fail={"transcribe"}),
        pipeline_settings=PipelineSettings(
            between_videos_s=0.0, sleep_subtitles_s=0.0, stt_policy="whisperx_only"
        ),
    )
    try:
        job_id = await parts.index(url=VIDEO_URL, channels="transcript")
        assert await parts.run() is True
        stages = await parts.stages()
        assert stages["stt"]["state"] == "failed"
        assert not await parts.rows("SELECT id FROM cues")
        video = await parts.one("SELECT index_state FROM videos")
        assert video["index_state"] == "failed"
        job = await parts.one("SELECT state FROM jobs WHERE public_id = ?", (job_id,))
        assert job["state"] == "failed"
    finally:
        await parts.db.close()
        parts.parts.auth.close()


# ----------------------------------------------------------------- expansion


async def test_a_playlist_fans_out_into_items_of_the_same_job(
    settings: Settings, clip: Path
) -> None:
    parts = await harness(settings, clip)
    try:
        job_id = await parts.index(url=PLAYLIST_URL, expand="playlist", channels="transcript")
        assert await parts.run() is True

        items = await parts.rows(
            "SELECT i.* FROM job_items i JOIN jobs j ON j.id = i.job_id "
            "WHERE j.public_id = ? ORDER BY i.seq",
            (job_id,),
        )
        # The container item plus one per entry, deduplicated (the fixture
        # lists the same video twice, as real playlists do).
        assert len(items) == 3
        assert items[0]["state"] == "skipped"
        assert [i["state"] for i in items[1:]] == ["done", "done"]

        job = await parts.one("SELECT * FROM jobs WHERE public_id = ?", (job_id,))
        assert job["n_items"] == 3 and job["state"] == "done"
        videos = await parts.rows("SELECT source_id FROM videos ORDER BY source_id")
        assert [v["source_id"] for v in videos] == ["aB3dEfG7hIj", "zZ9yY8xX7wV"]
        assert any("expanded" in message for message in await parts.events())
    finally:
        await parts.db.close()
        parts.parts.auth.close()


async def test_a_playlist_with_expand_none_is_a_typed_refusal(
    settings: Settings, clip: Path
) -> None:
    parts = await harness(settings, clip)
    try:
        await parts.index(url=PLAYLIST_URL, expand="none")
        assert await parts.run() is True
        item = await parts.one("SELECT state, error_code FROM job_items")
        assert item["state"] == "failed"
        assert item["error_code"] == "E_UNSUPPORTED_SOURCE"
    finally:
        await parts.db.close()
        parts.parts.auth.close()


async def test_an_unextractable_url_fails_the_item_not_the_process(
    settings: Settings, clip: Path
) -> None:
    parts = await harness(settings, clip)
    try:
        job_id = await parts.index(url="https://youtu.be/nOtInFiXtUr")
        assert await parts.run() is True
        job = await parts.one("SELECT * FROM jobs WHERE public_id = ?", (job_id,))
        assert job["state"] == "failed"
        assert job["error_code"] == "E_UNSUPPORTED_SOURCE"
        result = await indexing.job_status(parts.deps, job_id=job_id)
        assert "E_UNSUPPORTED_SOURCE" in body(result)
    finally:
        await parts.db.close()
        parts.parts.auth.close()
