"""The indexing pipeline: what an `index-video` job actually executes.

One class, one public method — ``run_item(ctx)`` — because that is the seam
``jobs/runner.py`` defines and everything else (claiming, heartbeats, retries,
rollups, cancellation) is already built around it.

The seven stages are the seven values `video_stages.stage` allows, and each one
is recorded with the model or parameters that produced it. That is not
bookkeeping for its own sake: it is what makes "swap the STT model and re-run
stt, chunk and text_embed, leaving 40,000 keyframes and their OCR alone" a
query rather than a rewrite (index-schema §1.3).

    fetch        yt-dlp: info dict (title, chapters, subtitle inventory,
                 heatmap), then audio and — when frames are wanted — video at
                 the height cap
    stt          whisperX via the worker, YouTube auto-captions (word-timed
                 json3), or manual subs, in the order the policy asks for
    chunk        45 s windows, 15 s overlap, from `config`
    text_embed   worker /v1/embeddings -> vec_chunks
    keyframe     PySceneDetect -> sharpest per shot -> JPEG -> phash dedup
    ocr          worker /v1/ocr -> ocr_lines
    frame_embed  worker /v1/embeddings/image -> vec_frames

**Failure is per stage.** A stage that fails records its own failure and leaves
every completed stage alone, so resume means "re-run the failed stages", not
"start again". Only `fetch` and `stt` are load-bearing enough to abort the item:
without a video row there is nothing to attach to, and without cues there is
nothing to search. Everything downstream degrades — a video with no OCR is a
video you can still find by what was said in it, and `data_status` says which.

**Below the stage, failure is per frame.** A keyframe the worker refuses as
undecodable is skipped and named (`_call_per_frame`, `_note_skipped_frames`); the
stage finishes for the other 599. A refusal about the *request* rather than one
file, and a stage where every frame was refused, still fail the stage.

**Cancellation is honoured between stages**, which is what `job-status` promises
("the job stops at the next stage boundary"). Nothing is rolled back: the stages
that finished stay finished.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from secrets import token_hex
from typing import Any, Awaitable, Callable, Sequence

from ..db import Database
from ..jobs.runner import ItemCancelled, ItemContext, ItemFailed, ItemSkipped
from . import store
from .captions import CueDraft, cues_from_json3, cues_from_verbose_json, cues_from_vtt
from .chunking import build_chunks
from .keyframes import KeyframeDraft, extract_keyframes
from .paths import Layout
from .settings import PipelineSettings
from .sources import (
    NotYetAvailable,
    RateLimited,
    Source,
    SourceError,
    Unavailable,
    VideoMeta,
    is_playlist,
    looks_like_container,
    parse_info,
    playlist_entries,
)
from .worker_client import OcrPage, WorkerAPI, WorkerRejected, WorkerUnavailable

logger = logging.getLogger(__name__)

# How long a lifecycle state waits when the source did not name a time: long
# enough that a `post_live` VOD has finished processing, short enough that three
# attempts still cover an evening. The cap keeps a premiere three days out from
# parking its claim for three days — it retires with a clear reason instead.
LIFECYCLE_RETRY_S = 1_800
LIFECYCLE_RETRY_MAX_S = 6 * 3_600

CONTAINER_HINT = (
    "index-video expand=playlist (or channel_recent) to fan that URL out into "
    "one job item per video."
)


@dataclass
class ItemRun:
    """Everything one video's pass accumulates. Lives for one `run_item`."""

    ctx: ItemContext
    args: dict[str, Any]
    meta: VideoMeta | None = None
    video_id: int = 0
    stages: dict[str, Any] = field(default_factory=dict)
    audio: Path | None = None
    media: Path | None = None
    cues: list[CueDraft] = field(default_factory=list)
    cue_ids: list[int] = field(default_factory=list)
    worker_ok: bool = False
    degraded: list[str] = field(default_factory=list)
    failed_stages: list[str] = field(default_factory=list)
    # Force is a thing that *happens once*, not a mode every attempt re-enters.
    # `force` is what the caller asked for; `force_active` is whether this
    # attempt is the one that threw the recorded stages away.
    force_active: bool = False
    # The audio download was skipped because the worker looked unreachable, so
    # whisperX never got its chance. If captions then fail too, that is the
    # worker's fault and not the video's.
    stt_deferred_to_captions: bool = False
    # The frame source could not be downloaded. Only the frame stages care,
    # and they record it as a failure rather than a deliberate skip.
    media_error: str | None = None

    @property
    def force(self) -> bool:
        return bool(self.args.get("force_reindex"))

    @property
    def forced_videos(self) -> set[int]:
        return {int(v) for v in (self.args.get("force_applied") or [])}

    @property
    def intended_stages(self) -> tuple[str, ...]:
        """The stages this job's `channels` mean to produce for this video."""
        stages = ["fetch"]
        if self.wants_transcript:
            stages += ["stt", "chunk", "text_embed"]
        if self.wants_frames:
            stages.append("keyframe")
        if self.wants_ocr:
            stages.append("ocr")
        if self.wants_frame_vectors:
            stages.append("frame_embed")
        return tuple(stages)

    @property
    def channels(self) -> set[str]:
        raw = str(self.args.get("channels") or "all")
        return {part.strip() for part in raw.split(",") if part.strip()} or {"all"}

    @property
    def wants_transcript(self) -> bool:
        return bool({"all", "transcript"} & self.channels)

    @property
    def wants_frames(self) -> bool:
        # OCR is a property of frames: asking for one asks for the other.
        return bool({"all", "frames", "ocr"} & self.channels)

    @property
    def wants_ocr(self) -> bool:
        return bool({"all", "ocr"} & self.channels)

    @property
    def wants_frame_vectors(self) -> bool:
        return bool({"all", "frames"} & self.channels)


class IndexingPipeline:
    """Implements ``jobs.runner.Pipeline``. Stateless between items but for the
    politeness clock, which is per-process on purpose."""

    def __init__(
        self,
        db: Database,
        layout: Layout,
        settings: PipelineSettings,
        source: Source,
        worker: WorkerAPI | None = None,
    ) -> None:
        self.db = db
        self.layout = layout
        self.settings = settings
        self.source = source
        self.worker = worker
        self._sleep = asyncio.sleep
        self._last_fetch_at: float | None = None

    # ------------------------------------------------------------------ entry

    async def run_item(self, ctx: ItemContext) -> None:
        args = await self.db.read(lambda c: store.job_args(c, ctx.job_id))
        run = ItemRun(ctx=ctx, args=args)
        self.layout.ensure()
        try:
            await self._stages(run)
        except ItemFailed:
            # A hard failure must not leave the row reading `indexing` forever;
            # `data_status` and the `index_state` filter both believe it.
            await self._settle_video(run, "failed")
            raise
        except ItemCancelled:
            await self._settle_video(run, "pending")
            raise

    async def _stages(self, run: ItemRun) -> None:
        await self._maybe_expand(run)
        await self._stage_fetch(run)
        await self._checkpoint(run)

        if run.wants_transcript:
            await self._stage_transcript(run)
            await self._checkpoint(run)
            await self._stage_chunk(run)
            await self._checkpoint(run)
            await self._stage_text_embed(run)
            await self._checkpoint(run)
        else:
            await self._skip(run, "stt", "channels did not ask for the transcript")
            await self._skip(run, "chunk", "no transcript to chunk")
            await self._skip(run, "text_embed", "no transcript to embed")

        if run.wants_frames:
            await self._stage_keyframes(run)
            await self._checkpoint(run)
            await self._stage_ocr(run)
            await self._checkpoint(run)
            await self._stage_frame_embed(run)
        else:
            await self._skip(run, "keyframe", "channels did not ask for frames")
            await self._skip(run, "ocr", "channels did not ask for on-screen text")
            await self._skip(run, "frame_embed", "channels did not ask for frames")

        await self._finalize(run)

    # ------------------------------------------------------------- expansion

    async def _maybe_expand(self, run: ItemRun) -> None:
        """A playlist/channel URL becomes N more items of the *same* job."""
        url = run.ctx.source_url
        expand = str(run.args.get("expand") or "playlist")
        if not looks_like_container(url):
            return
        if expand == "none":
            raise ItemFailed(
                "E_UNSUPPORTED_SOURCE",
                f"{url} is a playlist or channel URL and expand=none.",
                retryable=False,
            )
        max_items = int(run.args.get("max_items") or 25)
        await run.ctx.record("fetch", 0.0)
        try:
            entries = await asyncio.to_thread(self.source.expand, url, expand, max_items)
        except RateLimited as exc:
            raise _rate_limited(exc, str(exc)) from exc
        except SourceError as exc:
            raise ItemFailed("E_UNSUPPORTED_SOURCE", str(exc), retryable=False) from exc

        urls = [entry.url for entry in entries][:max_items]
        added = await self.db.write(lambda c: store.append_items(c, run.ctx.job_id, urls))
        await run.ctx.log(f"expanded {url} into {added} item(s)", "info", stage="fetch")
        raise ItemSkipped(f"expanded into {added} item(s)", code="E_EXPANDED")

    # ------------------------------------------------------------------ fetch

    async def _stage_fetch(self, run: ItemRun) -> None:
        await self._between_videos()
        await run.ctx.record("fetch", 0.0)
        url = run.ctx.source_url
        try:
            info = await asyncio.to_thread(self.source.probe, url)
            if is_playlist(info):
                # A container that did not look like one (a bare playlist id).
                expand = str(run.args.get("expand") or "playlist")
                if expand == "none":
                    raise ItemFailed(
                        "E_UNSUPPORTED_SOURCE", f"{url} is a playlist.", retryable=False
                    )
                max_items = int(run.args.get("max_items") or 25)
                urls = [e.url for e in playlist_entries(info, max_items)]
                added = await self.db.write(lambda c: store.append_items(c, run.ctx.job_id, urls))
                raise ItemSkipped(f"expanded into {added} item(s)", code="E_EXPANDED")
            meta = parse_info(info, url, getattr(self.source, "version", None))
        except NotYetAvailable as exc:
            raise _not_yet(exc) from exc
        except Unavailable as exc:
            raise ItemFailed("E_UNSUPPORTED_SOURCE", str(exc), retryable=False) from exc
        except RateLimited as exc:
            raise _rate_limited(exc, str(exc)) from exc
        except SourceError as exc:
            raise ItemFailed(
                "E_UNSUPPORTED_SOURCE",
                f"{url} could not be extracted: {exc}",
                retryable=False,
            ) from exc

        run.meta = meta
        video_id, claim = await self.db.write(
            lambda c: _land_metadata(c, meta, run.ctx.item_id)
        )
        if video_id is None:
            if claim.same_job:
                # A playlist listing the same video twice: bookkeeping, not an
                # error, and the other item is doing the work.
                raise ItemSkipped(
                    f"{meta.source_id} is already an item of this job",
                    code="E_DUPLICATE_ITEM",
                )
            # Another *job* holds the claim. Before the crash sweep existed
            # this was silently skipped, and a job whose only item skipped read
            # as `done` with nothing fetched. It is a typed failure now.
            holder = claim.job_public_id or "another job"
            raise ItemFailed(
                "E_INDEXING",
                f"{meta.source_id} is already claimed by {holder}; nothing was "
                "indexed by this item. Wait for that job, or cancel it and retry "
                "with force_reindex=true.",
                retryable=False,
            )
        run.video_id = video_id
        run.stages = await self.db.read(lambda c: store.stage_map(c, video_id))
        await self._apply_force(run)
        await run.ctx.log(
            f'fetched metadata for {meta.source_id} "{meta.title}"', "info", stage="fetch"
        )
        await run.ctx.record("fetch", 0.25)

        # --- media -----------------------------------------------------------
        # Gated on whether the stage that consumes the file is actually going to
        # run. A resume whose only outstanding stage is `ocr` needs no mp4, and
        # downloading one "in case" is how a wave of already-indexed videos cost
        # a night of bandwidth before any later stage noticed they were current.
        self._note_worker(run, await self._worker_healthy())
        want_media = run.wants_frames and self._should_run(
            run, "keyframe", self._keyframe_model_key()
        )
        need_audio = (
            run.wants_transcript
            and self.settings.wants_whisperx
            and self._should_run(run, "stt", self.db.config.get("stt.model", "whisperx"))
        )

        if (
            need_audio
            and not run.worker_ok
            and self.settings.captions_allowed
            and self._caption_candidate(run) is not None
        ):
            # Zero-GPU path: nothing to send the audio to, and the captions the
            # metadata pass already inventoried carry word timings.
            #
            # The candidate check is the whole point. Skipping the download
            # because captions are *allowed* — without checking one exists —
            # left an uncaptioned video with no audio and no captions: whisperX
            # returned None, captions returned None, the error list was empty,
            # and an empty error list settled the item as a permanent
            # `E_UNSUPPORTED_SOURCE`. One flaky health probe was enough.
            need_audio = False
            run.stt_deferred_to_captions = True
            run.degraded.append(
                "the inference worker was unreachable, so this video was indexed CPU-only"
            )

        await self._stage_running(run, "fetch")
        try:
            if need_audio:
                run.audio = await self._fetch_audio(run)
                await run.ctx.record("fetch", 0.5)
        except RateLimited as exc:
            await self._stage_failed(run, "fetch", str(exc))
            raise _rate_limited(exc, str(exc)) from exc
        except (SourceError, OSError) as exc:
            await self._stage_failed(run, "fetch", str(exc))
            raise ItemFailed("E_INTERNAL", f"download failed: {exc}", retryable=True) from exc

        if want_media:
            # The *frame* source is a separate fetch from the metadata and audio,
            # because it is a separate answer. A VOD that exposes no usable video
            # format used to raise here, fail `fetch` — an essential stage — and
            # discard a perfectly good transcript along with the frames. Only the
            # frame stages depend on this file, so only they fail.
            try:
                run.media = await self._fetch_media(run)
                await run.ctx.record("fetch", 0.95)
            except RateLimited as exc:
                # Throttling is about this box, not this file: back the whole
                # item off rather than indexing half of it during a block.
                await self._stage_failed(run, "fetch", str(exc))
                raise _rate_limited(exc, str(exc)) from exc
            except (SourceError, OSError) as exc:
                run.media_error = str(exc)
                await run.ctx.log(
                    f"the frame source could not be downloaded: {exc}. The transcript "
                    "legs continue; the frame stages will record the failure.",
                    "warn",
                    stage="fetch",
                )

        audio_rel = _relative(run.audio, self.layout.data_dir)
        media_rel = _relative(run.media, self.layout.data_dir)
        video_id_final = run.video_id
        await self.db.write(
            lambda c: store.set_media_paths(c, video_id_final, audio=audio_rel, media=media_rel)
        )
        await self._stage_done(run, "fetch", getattr(self.source, "version", "yt-dlp"))
        await run.ctx.record("fetch", 1.0)

    async def _fetch_audio(self, run: ItemRun) -> Path | None:
        assert run.meta is not None
        existing = self.layout.audio_path(run.meta.source_id, self.settings.audio_codec)
        if existing.exists() and not run.force_active:
            return existing
        media = await asyncio.to_thread(
            self.source.download_audio,
            run.meta.url,
            run.meta.source_id,
            self.layout.audio_dir(),
            self.settings.audio_codec,
        )
        return media.path

    async def _fetch_media(self, run: ItemRun) -> Path | None:
        assert run.meta is not None
        existing = self.layout.media_candidates(run.meta.source_id)
        if existing and not run.force_active:
            return existing[0]
        media = await asyncio.to_thread(
            self.source.download_video,
            run.meta.url,
            run.meta.source_id,
            self.layout.media_dir(),
            self.settings.max_height,
        )
        return media.path

    async def _between_videos(self) -> None:
        """A randomised gap between videos, not just between downloads.

        Indexing is not latency-sensitive; a channel backfill at a video a
        minute overnight is invisible, and it is the difference between a
        residential IP that keeps working and one that does not (research §5.5).
        """
        gap = self.settings.between_videos_s
        if gap <= 0:
            return
        if self._last_fetch_at is not None:
            waited = time.monotonic() - self._last_fetch_at
            remaining = gap + random.uniform(0, gap) - waited
            if remaining > 0:
                await self._sleep(remaining)
        self._last_fetch_at = time.monotonic()

    # -------------------------------------------------------------- transcript

    async def _stage_transcript(self, run: ItemRun) -> None:
        assert run.meta is not None
        # The fetch-time probe is minutes old by now and it decided whether the
        # audio was even downloaded. If it said "down", ask again before acting
        # on it: a worker that came back mid-item can still do the better job,
        # and `_should_run`'s stt clause wants a current answer too.
        if not run.worker_ok and self.settings.wants_whisperx:
            self._note_worker(run, await self._worker_healthy())
        stt_model = self.db.config.get("stt.model", "whisperx")
        if not self._should_run(run, "stt", stt_model):
            run.cues = []  # already on disk; chunking will skip too
            return

        await self._stage_running(run, "stt")
        await run.ctx.record("stt", 0.05)
        # `prefer_captions` and `captions_only` are the zero-GPU orderings; the
        # other two try the model that punctuates and cases first, because
        # punctuation and casing are what make FTS5 and the embedder behave.
        attempts = (
            ("captions", "whisperx") if self.settings.captions_first else ("whisperx", "captions")
        )

        errors: list[str] = []
        throttled: RateLimited | None = None
        worker_down = run.stt_deferred_to_captions
        for kind in attempts:
            try:
                if kind == "whisperx":
                    result = await self._transcribe_whisperx(run)
                else:
                    result = await self._transcribe_captions(run, errors)
            except RateLimited as exc:
                # A 429 on the caption track is the *source* saying "later", not
                # this video saying "never". Collapsing it into
                # E_UNSUPPORTED_SOURCE retired the item on the spot and told the
                # caller the video had no transcript.
                throttled = exc
                errors.append(f"{kind}: {exc}")
                continue
            except WorkerUnavailable as exc:
                # The worker, not the video. Retryable, and typed as such rather
                # than inferred from whether the word "worker" is in the string.
                worker_down = True
                errors.append(f"{kind}: {exc}")
                continue
            except (WorkerRejected, SourceError) as exc:
                # `invalid_media` lands here: the audio the decoder could not
                # read *was* this attempt's whole payload, so unlike a frame
                # there is nothing to drop and continue with. The loop falls
                # through to captions, and if those fail too the stage fails
                # non-retryably — re-sending the same file fails identically.
                errors.append(f"{kind}: {exc}")
                continue
            if result is None:
                continue
            cues, origin, model_key = result
            if not cues:
                errors.append(f"{kind}: produced no cues")
                continue
            run.cues = cues
            keep_words = self.settings.keep_word_timings
            video_id = run.video_id
            run.cue_ids = await self.db.write(
                lambda c: store.replace_cues(c, video_id, cues, origin, keep_words)
            )
            await self._stage_done(run, "stt", model_key)
            await run.ctx.record("stt", 1.0)
            await run.ctx.log(
                f"transcript from {origin} ({len(cues)} cues, model_key={model_key})",
                "info",
                stage="stt",
            )
            if origin != "whisperx":
                run.degraded.append(f"transcript came from {origin}, not whisperX")
            return

        detail = "; ".join(errors) or "no transcript source was available"
        await self._stage_failed(run, "stt", detail)
        if throttled is not None:
            raise _rate_limited(
                throttled, f"rate-limited fetching a transcript for {run.meta.source_id}: {detail}"
            )
        if worker_down:
            # Including the case where the *fetch* stage skipped the audio
            # because the worker looked unreachable: whisperX never got its
            # chance, so "this video has no transcript" is not a conclusion this
            # attempt is entitled to draw.
            raise ItemFailed(
                "E_WORKER_UNAVAILABLE",
                f"no transcript for {run.meta.source_id} because the inference worker "
                f"was unavailable: {detail}",
                retryable=True,
            )
        raise ItemFailed(
            "E_UNSUPPORTED_SOURCE",
            f"no usable transcript for {run.meta.source_id}: {detail}",
            retryable=False,
        )

    async def _transcribe_whisperx(self, run: ItemRun) -> tuple[list[CueDraft], str, str] | None:
        if not self.settings.wants_whisperx or self.worker is None:
            return None
        if run.audio is None or not run.audio.exists():
            if not self.settings.captions_allowed:
                # `whisperx_only` and no audio: there is no other path, and
                # returning None here is what produced the empty error list that
                # settled the item permanently.
                raise WorkerUnavailable(
                    "no audio was downloaded for this video, so whisperX had nothing "
                    "to transcribe"
                )
            return None
        assert run.meta is not None
        model = self.db.config.get("stt.model")
        payload = await self.worker.transcribe(
            run.audio,
            language=run.meta.language,
            model=model,
            # The budget is sized from the recording: a flat 1,800 s covers
            # a conference talk and not a three-hour stream.
            duration_s=run.meta.duration_s or None,
        )
        cues = cues_from_verbose_json(payload)
        served = str(payload.get("model") or model or "whisperx")
        if cues and not any(cue.words for cue in cues):
            run.degraded.append(
                "the worker returned segment timings without words, so deep links "
                "land on the sentence rather than the word"
            )
        # The stage's model_key must equal `config['stt.model']` or the reindex
        # planner reads this video as permanently out of date.
        return cues, "whisperx", model or served

    async def _transcribe_captions(
        self, run: ItemRun, errors: list[str] | None = None
    ) -> tuple[list[CueDraft], str, str] | None:
        """Every candidate track in preference order, not just the best one.

        One 403 or one malformed json3 used to end the caption path outright,
        because a single track was selected and a fetch exception left the
        method. The French VTT beside it, the English auto track and the whole
        manual inventory were never tried.
        """
        assert run.meta is not None
        if not self.settings.captions_allowed:
            return None
        recorded = errors if errors is not None else []
        throttled: RateLimited | None = None
        for track in run.meta.candidates(self.settings.subtitle_langs):
            label = f"captions {track.lang}/{track.ext}"
            try:
                payload = await asyncio.to_thread(self.source.fetch_subtitle, track)
                if track.ext == "json3":
                    cues = cues_from_json3(payload, word_timed=track.word_timed)
                else:
                    cues = cues_from_vtt(payload)
            except RateLimited as exc:
                # Remembered, not raised yet: another language may be served
                # from a path this box has not been throttled on.
                throttled = exc
                recorded.append(f"{label}: {exc}")
                continue
            except SourceError as exc:
                recorded.append(f"{label}: {exc}")
                continue
            except (ValueError, TypeError, KeyError) as exc:
                # A timedtext URL that expired answers with an error page, not
                # json3, and `json.loads` says so in a way nothing above caught.
                recorded.append(f"{label}: malformed payload ({exc})")
                continue
            if not cues:
                recorded.append(f"{label}: produced no cues")
                continue
            origin = "yt_auto" if track.automatic else "yt_manual"
            return cues, origin, f"youtube-{'asr' if track.automatic else 'subs'}-{track.lang}"
        if throttled is not None:
            raise throttled
        return None

    # ------------------------------------------------------------------ chunk

    async def _stage_chunk(self, run: ItemRun) -> None:
        target = float(self.db.config_int("chunk.target_seconds", 45))
        overlap = float(self.db.config_int("chunk.overlap_seconds", 15))
        model_key = f"chunk-{int(target)}-{int(overlap)}"
        if not run.cues:
            # `stt` was left alone as already-current, so its chunks are too —
            # unless the chunker's own parameters moved, and then there are no
            # cues in memory to rebuild from and it is a reindex, not a resume.
            if self._should_run(run, "chunk", model_key):
                await self._skip(run, "chunk", "no fresh cues to chunk")
            return

        await self._stage_running(run, "chunk")
        chunks = build_chunks(run.cues, target, overlap)
        video_id, cue_ids = run.video_id, run.cue_ids
        await self.db.write(lambda c: store.replace_chunks(c, video_id, chunks, cue_ids))
        await self._stage_done(run, "chunk", model_key)
        await run.ctx.record("chunk", 1.0)

    # ------------------------------------------------------------- text_embed

    async def _stage_text_embed(self, run: ItemRun) -> None:
        model = self.db.config.get("text_embed.model", "")
        if not self._should_run(run, "text_embed", model):
            return
        if self.worker is None:
            await self._skip(run, "text_embed", "no worker is configured")
            return
        # The anti-drift assertion, before a single vector is written: config
        # and the vec table must already agree (checked at boot), and the worker
        # must answer in the same model and width (checked per batch below).
        if not self.db.vectors.enabled or not self.db.writes_allowed:
            await self._skip(
                run, "text_embed", self.db.vectors.reason or "vector legs are disabled"
            )
            return

        chunks = await self.db.read(lambda c: store.pending_chunks(c, run.video_id))
        if not chunks:
            await self._skip(run, "text_embed", "this video has no chunks")
            return

        await self._stage_running(run, "text_embed")
        await run.ctx.record("text_embed", 0.0)
        batch_size = max(1, self.settings.embed_batch)
        written = 0
        try:
            for offset in range(0, len(chunks), batch_size):
                batch = chunks[offset : offset + batch_size]
                vectors, served, dims = await self.worker.embed(
                    [str(row["text"]) for row in batch],
                    model=model or None,
                    # Documents, not queries: the asymmetric prefix belongs to
                    # whoever runs the model, and this is the document side.
                    input_type="document",
                )
                mismatch = _dimension_mismatch(vectors, dims, self.db.text_dim, served, model)
                if mismatch:
                    await self._skip(run, "text_embed", mismatch)
                    return
                rows = [
                    (int(row["id"]), float(row["start_s"]), vector)
                    for row, vector in zip(batch, vectors, strict=True)
                ]
                video_id = run.video_id
                await self.db.write(lambda c: store.write_chunk_vectors(c, video_id, rows))
                written += len(rows)
                await run.ctx.record("text_embed", min(1.0, (offset + len(batch)) / len(chunks)))
        except (WorkerUnavailable, WorkerRejected) as exc:
            await self._soft_fail(run, "text_embed", str(exc))
            return
        await self._stage_done(run, "text_embed", model)
        await run.ctx.log(f"embedded {written} chunks", "info", stage="text_embed")

    # --------------------------------------------------------------- keyframe

    def _keyframe_model_key(self) -> str:
        return f"scenedetect-{self.settings.detector}-w{self.settings.keyframe_max_width}"

    async def _stage_keyframes(self, run: ItemRun) -> None:
        assert run.meta is not None
        model_key = self._keyframe_model_key()
        if not self._should_run(run, "keyframe", model_key):
            return
        if run.media is None or not run.media.exists():
            if run.media_error:
                await self._soft_fail(
                    run,
                    "keyframe",
                    f"the frame source could not be downloaded: {run.media_error}",
                )
            else:
                await self._skip(run, "keyframe", "the source video is not on disk")
            return

        await self._stage_running(run, "keyframe")
        await run.ctx.record("keyframe", 0.0)
        source_id = run.meta.source_id
        out_dir = self.layout.keyframes_dir(source_id)
        # Whatever a previous run was killed in the middle of. Safe because one
        # runner drives one item at a time (multi-process fencing is a separate,
        # deferred, question).
        for leftover in self.layout.keyframes_leftovers(source_id):
            await asyncio.to_thread(_rmtree, leftover)
        staging = self.layout.keyframes_staging_dir(source_id)
        loop = asyncio.get_running_loop()

        def report(fraction: float) -> None:
            # Called from the worker thread; hop back to the loop to touch the db.
            asyncio.run_coroutine_threadsafe(run.ctx.record("keyframe", fraction * 0.9), loop)

        try:
            # Into staging, never into the served directory: extraction writes
            # two hundred JPEGs over minutes and can fail at any one of them.
            drafts: list[KeyframeDraft] = await asyncio.to_thread(
                extract_keyframes,
                run.media,
                staging,
                lambda ordinal, t_s: self.layout.keyframe_relpath(source_id, ordinal, t_s),
                kind=self.settings.detector,
                max_shot_seconds=self.settings.max_shot_seconds,
                candidates_per_shot=self.settings.candidates_per_shot,
                max_width=self.settings.keyframe_max_width,
                quality=self.settings.keyframe_quality,
                budget=self.settings.max_keyframes,
                phash_threshold=self.settings.phash_threshold,
                # Not in `_keyframe_model_key()` on purpose: both knobs are
                # meant to produce the same frames faster, so putting them in
                # the key would reindex the whole corpus for a thread count.
                workers=self.settings.extract_workers,
                decode_threads=self.settings.extract_decode_threads,
                progress=report,
            )
        except Exception as exc:  # decode failures, unreadable container, full disk
            await asyncio.to_thread(_rmtree, staging)
            await self._soft_fail(run, "keyframe", f"frame extraction failed: {exc}")
            return

        video_id = run.video_id
        try:
            # Rows first: if the insert fails, the previous generation is still
            # whole and the staged bytes are simply thrown away. Then publish,
            # which is a rename — the old directory is retired, the staged one
            # takes its place, and anything the new rows do not name is removed.
            await self.db.write(lambda c: store.replace_keyframes(c, video_id, drafts))
            orphans = await asyncio.to_thread(
                _publish_keyframes, staging, out_dir, {Path(d.relpath).name for d in drafts}
            )
        except Exception as exc:
            await asyncio.to_thread(_rmtree, staging)
            await self._soft_fail(run, "keyframe", f"could not publish the keyframes: {exc}")
            return

        duplicates = sum(1 for d in drafts if d.dup_of is not None)
        await self._stage_done(run, "keyframe", model_key)
        await run.ctx.record("keyframe", 1.0)
        await run.ctx.log(
            f"{len(drafts)} keyframes ({duplicates} near-duplicates kept as dup_of"
            + (f", {orphans} orphan file(s) removed" if orphans else "")
            + ")",
            "info",
            stage="keyframe",
        )

    # -------------------------------------------------------------------- ocr

    async def _stage_ocr(self, run: ItemRun) -> None:
        model = self.db.config.get("ocr.model", "")
        if not run.wants_ocr:
            await self._skip(run, "ocr", "channels did not ask for on-screen text")
            return
        if not self._should_run(run, "ocr", model):
            return
        if self.worker is None:
            await self._skip(run, "ocr", "no worker is configured")
            return
        frames = await self.db.read(
            lambda c: store.live_keyframes(c, run.video_id, ("pending", "failed"))
        )
        if not frames:
            await self._skip(run, "ocr", "no keyframes to read")
            return

        await self._stage_running(run, "ocr")
        batch_size = max(1, self.settings.ocr_batch)
        lines_written = 0
        read = 0
        skipped: list[tuple[Any, str]] = []
        for offset in range(0, len(frames), batch_size):
            batch = frames[offset : offset + batch_size]
            try:
                kept, pages, _served, refused = await self._call_per_frame(batch, self._ocr_call)
            except (WorkerUnavailable, WorkerRejected) as exc:
                ids = [int(row["id"]) for row in batch]
                await self.db.write(lambda c: store.set_ocr_state(c, ids, "failed"))
                await self._soft_fail(run, "ocr", str(exc))
                return
            unreadable = list(refused)
            for row, page in zip(kept, pages, strict=True):
                if page.error is not None:
                    # The worker read the batch and refused *this* image. Its
                    # rows are left alone and the frame is marked `failed`, not
                    # `empty`: a re-run picks failed frames back up, and nothing
                    # downstream gets to conclude the slide had no text on it.
                    unreadable.append((row, page.error))
                    continue
                read += 1
                video_id = run.video_id
                keyframe_id = int(row["id"])
                t_s = float(row["t_s"])
                width, height = int(row["width"]), int(row["height"])
                lines_written += await self.db.write(
                    lambda c: store.write_ocr(
                        c, video_id, keyframe_id, t_s, page.lines, width, height
                    )
                )
            if unreadable:
                ids = [int(row["id"]) for row, _ in unreadable]
                await self.db.write(lambda c: store.set_ocr_state(c, ids, "failed"))
                skipped.extend(unreadable)
            await run.ctx.record("ocr", min(1.0, (offset + len(batch)) / len(frames)))
        if skipped and not read:
            # Not one frame came back. `done` here would claim OCR coverage the
            # video has none of (`has_ocr` is "the stage is done"), so this is
            # the case that stays a stage failure.
            await self._soft_fail(
                run, "ocr", _all_unreadable(len(skipped), skipped[0][1])
            )
            return
        await self._stage_done(run, "ocr", model)
        await run.ctx.log(f"read {lines_written} on-screen lines", "info", stage="ocr")
        await self._note_skipped_frames(run, "ocr", model, skipped)

    # ------------------------------------------------------------ frame_embed

    async def _stage_frame_embed(self, run: ItemRun) -> None:
        model = self.db.config.get("frame_embed.model", "")
        if not run.wants_frame_vectors:
            await self._skip(run, "frame_embed", "channels did not ask for frame search")
            return
        if not self._should_run(run, "frame_embed", model):
            return
        if self.worker is None:
            await self._skip(run, "frame_embed", "no worker is configured")
            return
        if not self.db.vectors.enabled or not self.db.writes_allowed:
            await self._skip(
                run, "frame_embed", self.db.vectors.reason or "vector legs are disabled"
            )
            return
        frames = await self.db.read(lambda c: store.all_live_keyframes(c, run.video_id))
        if not frames:
            await self._skip(run, "frame_embed", "no keyframes to embed")
            return

        await self._stage_running(run, "frame_embed")
        batch_size = max(1, self.settings.frame_embed_batch)
        written = 0
        skipped: list[tuple[Any, str]] = []
        for offset in range(0, len(frames), batch_size):
            batch = frames[offset : offset + batch_size]
            try:
                kept, vectors, extra, refused = await self._call_per_frame(
                    batch, lambda paths: self._embed_images_call(paths, model)
                )
            except (WorkerUnavailable, WorkerRejected) as exc:
                await self._soft_fail(run, "frame_embed", str(exc))
                return
            skipped.extend(refused)
            served, dims = extra if extra is not None else (None, None)
            mismatch = _dimension_mismatch(vectors, dims, self.db.frame_dim, served, model)
            if mismatch:
                await self._skip(run, "frame_embed", mismatch)
                return
            rows = [
                (int(row["id"]), float(row["t_s"]), vector)
                for row, vector in zip(kept, vectors, strict=True)
            ]
            video_id = run.video_id
            await self.db.write(lambda c: store.write_frame_vectors(c, video_id, rows))
            written += len(rows)
            await run.ctx.record("frame_embed", min(1.0, (offset + len(batch)) / len(frames)))
        if skipped and not written:
            # `has_frames` is "the frame_embed stage is done", so a done stage
            # with no vectors would advertise a search channel that answers
            # nothing. Every frame refused is a stage failure.
            await self._soft_fail(
                run, "frame_embed", _all_unreadable(len(skipped), skipped[0][1])
            )
            return
        await self._stage_done(run, "frame_embed", model)
        await run.ctx.log(f"embedded {written} frames", "info", stage="frame_embed")
        await self._note_skipped_frames(run, "frame_embed", model, skipped)

    # ----------------------------------------------------- per-frame refusals

    async def _ocr_call(self, paths: list[Path]) -> tuple[list[OcrPage], str | None]:
        assert self.worker is not None
        return await self.worker.ocr(paths, min_confidence=self.settings.ocr_min_confidence)

    async def _embed_images_call(
        self, paths: list[Path], model: str
    ) -> tuple[list[list[float]], tuple[str | None, int | None]]:
        assert self.worker is not None
        vectors, served, dims = await self.worker.embed_images(
            paths, model=model or None, max_num_patches=self.settings.frame_embed_max_patches
        )
        return vectors, (served, dims)

    async def _call_per_frame(
        self,
        rows: Sequence[Any],
        call: Callable[[list[Path]], Awaitable[tuple[list[Any], Any]]],
    ) -> tuple[list[Any], list[Any], Any, list[tuple[Any, str]]]:
        """One worker call over these frames, with the ones it refuses cut out.

        `invalid_image` is the worker saying *one* of these files is not an
        image — a truncated JPEG, a zero-pixel frame, bytes no decoder takes
        (worker/openapi.json; `PER_ITEM_CODES`). The request fails whole, and the
        wire error names no index, so the offender is found by halving the batch
        and re-sending: ~2·log₂(n) extra calls for a batch that contains a bad
        frame, none at all for a batch that does not. The alternative is what
        this used to do — one corrupt keyframe out of six hundred failed the
        whole stage, and the video lost its frame search entirely.

        Every other refusal propagates untouched: `invalid_input` and
        `invalid_media` are about the request, not one file in it, and no subset
        of the batch would be accepted either.

        Returns `(kept_rows, results, extra, skipped)` — `results` lines up with
        `kept_rows`, `extra` is whatever the call carries alongside (the served
        model, dimensions), and `skipped` pairs each refused row with the reason.
        """
        paths = [self.layout.absolute(str(row["jpeg_path"])) for row in rows]
        try:
            results, extra = await call(paths)
        except WorkerRejected as exc:
            if not exc.per_item:
                raise
            if len(rows) <= 1:
                return [], [], None, [(row, str(exc)) for row in rows]
            half = len(rows) // 2
            left = await self._call_per_frame(rows[:half], call)
            right = await self._call_per_frame(rows[half:], call)
            return (
                [*left[0], *right[0]],
                [*left[1], *right[1]],
                # Both halves answer with the same model; keep whichever half
                # actually reached a live worker.
                left[2] if left[2] is not None else right[2],
                [*left[3], *right[3]],
            )
        return list(rows), list(results), extra, []

    async def _note_skipped_frames(
        self, run: ItemRun, stage: str, model_key: str, skipped: Sequence[tuple[Any, str]]
    ) -> None:
        """Frames the worker refused, on a stage that finished anyway.

        The stage is `done` because it did its job for the other 599 frames, but
        "done" must not read as "all of them". The count and the first reason go
        into the job log *and* onto the stage row, which is the record that
        outlives the process — `video_stages.error` beside `state='done'` is
        exactly "what this stage could not do", and no query treats a non-null
        error as a failure (`degraded_items` and the resume plan both filter on
        `state`).
        """
        if not skipped:
            return
        ordinals = [int(row["ord"]) for row, _ in skipped]
        shown = ", ".join(str(o) for o in ordinals[:5])
        if len(ordinals) > 5:
            shown += f", … (+{len(ordinals) - 5})"
        note = (
            f"{len(skipped)} frame(s) skipped: the worker could not read them "
            f"(ord {shown}). First: {skipped[0][1]}"
        )
        video_id = run.video_id
        await self.db.write(
            lambda c: store.stage_finished(c, video_id, stage, "done", model_key, note)
        )
        run.stages = await self.db.read(lambda c: store.stage_map(c, video_id))
        await run.ctx.log(note, "warn", stage=stage)
        run.degraded.append(f"{stage} skipped {len(skipped)} unreadable frame(s)")

    # --------------------------------------------------------------- finalize

    async def _finalize(self, run: ItemRun) -> None:
        video_id = run.video_id
        tags = [str(t) for t in (run.args.get("tags") or [])]
        if tags:
            await self.db.write(lambda c: store.apply_tags(c, video_id, tags))

        stages = await self.db.read(lambda c: store.stage_map(c, video_id))
        required = ("fetch", "stt") if run.wants_transcript else ("fetch",)
        essential_ok = all(_state_of(stages, name) == "done" for name in required)

        if essential_ok:
            await self.db.write(lambda c: store.mark_ready(c, video_id))
        else:
            await self.db.write(lambda c: store.mark_failed(c, video_id))

        await self._retention(run, stages)
        if run.degraded:
            await run.ctx.log("; ".join(sorted(set(run.degraded))), "warn")
        if run.failed_stages:
            await run.ctx.log(
                "completed with failed stages: " + ", ".join(sorted(set(run.failed_stages))),
                "warn",
            )

    async def _settle_video(self, run: ItemRun, state: str) -> None:
        if not run.video_id:
            return
        video_id = run.video_id
        if state == "failed":
            await self.db.write(lambda c: store.mark_failed(c, video_id))
            return
        await self.db.write(
            lambda c: c.execute(
                "UPDATE videos SET index_state = ?, updated_at = unixepoch() WHERE id = ?",
                (state, video_id),
            )
        )

    async def _retention(self, run: ItemRun, stages: dict[str, Any]) -> None:
        """DECISIONS.md #3: keep the audio, drop the mp4 — per *resource*.

        "Nothing failed" was the wrong gate. A file must outlive a failure only
        while it is still the input to something that will re-run, and the mp4
        is the input to exactly one stage: `keyframe`. OCR and frame embedding
        read the JPEGs. So a transient OCR error used to pin a multi-gigabyte
        source video forever — the item finished `done`, the video went `ready`,
        nothing scheduled a retry, and only an explicit reindex would ever
        release it. On a 116-video night that is the disk.

        The rule now: each file is released once the stage that consumes it has
        settled (`done` or `skipped`), and kept while that stage is still
        retryable (`failed`, `pending`, `running`).
        """
        if run.meta is None:
            return
        keep = self.settings.keep_source
        video_id = run.video_id
        if keep != "originals" and _settled(stages, "keyframe"):
            for path in self.layout.media_candidates(run.meta.source_id):
                _unlink(path)
            if run.media is not None:
                _unlink(run.media)
            await self.db.write(lambda c: store.clear_media_path(c, video_id))
        if keep == "none" and run.audio is not None and _settled(stages, "stt"):
            _unlink(run.audio)
            await self.db.write(lambda c: store.clear_media_path(c, video_id, audio=True))

    # ---------------------------------------------------------------- helpers

    async def _checkpoint(self, run: ItemRun) -> None:
        """The cancellation boundary the contract promises."""
        if await run.ctx.cancelled():
            raise ItemCancelled("cancelled at a stage boundary")

    async def _apply_force(self, run: ItemRun) -> None:
        """`force_reindex` invalidates the intended stages once, at the start.

        It used to be a mode: `force_reindex=true` stayed in the job args, so
        every attempt after a crash read it and re-ran *every* completed stage
        and ignored every file already on disk. A forced reindex that died at
        keyframes redownloaded and retranscribed the whole video on each retry,
        forever if the crash was deterministic.

        Throwing the stage rows away once is the same instruction, expressed so
        that ordinary resume semantics can carry the rest: the second attempt
        sees fetch and stt already `done` *by this job* and skips them. The
        marker lives in the job's own args because that is the row that survives
        the process.
        """
        if not run.force or not run.video_id or run.video_id in run.forced_videos:
            return
        run.force_active = True
        video_id, stages = run.video_id, run.intended_stages
        await self.db.write(lambda c: _invalidate_stages(c, video_id, stages))
        await self.db.write(
            lambda c: _note_force_applied(c, run.ctx.job_id, video_id)
        )
        run.stages = await self.db.read(lambda c: store.stage_map(c, video_id))
        await run.ctx.log(
            "force_reindex: discarded the recorded state of " + ", ".join(stages),
            "info",
            stage="fetch",
        )

    def _should_run(self, run: ItemRun, stage: str, model_key: str | None) -> bool:
        """Resume: re-run failed and out-of-date stages, leave finished ones.

        `stt` gets one extra clause. A video indexed from auto-captions has a
        `model_key` that will never equal `config['stt.model']`, so the plain
        rule would re-fetch its captions on every retry of an unrelated stage.
        It is only worth re-running when we can actually do better — which
        means the worker is answering now.
        """
        if run.force_active:
            return True
        row = run.stages.get(stage)
        if row is None or str(row["state"]) != "done":
            return True
        recorded = row["model_key"]
        if stage == "stt":
            return bool(recorded != model_key and run.worker_ok and self.settings.wants_whisperx)
        return recorded != model_key

    async def _stage_running(self, run: ItemRun, stage: str) -> None:
        await run.ctx.record(stage, 0.0)
        video_id = run.video_id
        await self.db.write(lambda c: store.stage_running(c, video_id, stage))

    async def _stage_done(self, run: ItemRun, stage: str, model_key: str | None) -> None:
        video_id = run.video_id
        await self.db.write(lambda c: store.stage_finished(c, video_id, stage, "done", model_key))
        run.stages = await self.db.read(lambda c: store.stage_map(c, video_id))

    async def _stage_failed(self, run: ItemRun, stage: str, error: str) -> None:
        video_id = run.video_id
        if not video_id:
            return
        run.failed_stages.append(stage)
        await self.db.write(
            lambda c: store.stage_finished(c, video_id, stage, "failed", None, error)
        )

    async def _soft_fail(self, run: ItemRun, stage: str, error: str) -> None:
        """A stage that failed without taking the video down with it.

        The row says `failed` with the reason, the job event says it out loud,
        and the item carries on: a video with no OCR is still a video you can
        find by what was said in it, and re-running one stage is a reindex, not
        a re-download.
        """
        await self._stage_failed(run, stage, error)
        await run.ctx.log(f"{stage} failed: {error}", "error", stage=stage)
        run.degraded.append(f"{stage} failed ({error})")

    async def _skip(self, run: ItemRun, stage: str, reason: str) -> None:
        """`skipped` is not `pending`: it records a deliberate choice, so
        `coverage` and `data_status` never report one as missing data."""
        if not run.video_id:
            return
        video_id = run.video_id
        await self.db.write(
            lambda c: store.stage_finished(c, video_id, stage, "skipped", None, reason)
        )
        await run.ctx.log(f"{stage} skipped: {reason}", "info", stage=stage)

    def _caption_candidate(self, run: ItemRun) -> Any:
        """A caption track the STT stage could actually use, auto or manual."""
        if run.meta is None:
            return None
        langs = self.settings.subtitle_langs
        for automatic in (True, False):
            track = run.meta.track(langs, automatic=automatic)
            if track is not None:
                return track
        return None

    def _note_worker(self, run: ItemRun, healthy: bool) -> None:
        run.worker_ok = healthy

    async def _worker_healthy(self) -> bool:
        if self.worker is None:
            return False
        try:
            return await self.worker.healthy()
        except Exception:  # pragma: no cover - healthy() swallows its own
            return False


# ------------------------------------------------------------------ helpers


def _invalidate_stages(conn: Any, video_id: int, stages: Sequence[str]) -> None:
    """Throw away what these stages recorded, so resume has to run them again.

    Deliberately not a delete: the row keeps its history and its `stage_version`,
    and `pending` is the state a resume already knows how to read.
    """
    placeholders = ",".join("?" for _ in stages)
    conn.execute(
        "UPDATE video_stages SET state = 'pending', model_key = NULL, "
        "finished_at = NULL, error = NULL "
        f"WHERE video_id = ? AND stage IN ({placeholders})",
        (video_id, *stages),
    )


def _note_force_applied(conn: Any, job_id: int, video_id: int) -> None:
    """Record that this job has already spent its force on this video.

    In `args_json`, because that is the row that outlives the process: the next
    attempt reads it back through `job_args` and behaves like a normal resume.
    """
    import json

    row = conn.execute("SELECT args_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
    args = json.loads(row["args_json"] or "{}") if row is not None else {}
    applied = {int(v) for v in (args.get("force_applied") or [])}
    applied.add(int(video_id))
    args["force_applied"] = sorted(applied)
    conn.execute("UPDATE jobs SET args_json = ? WHERE id = ?", (json.dumps(args), job_id))


def _publish_keyframes(staging: Path, final: Path, keep: set[str]) -> int:
    """Swap the staged directory into place, then remove what no row names.

    Two renames on one filesystem. The window between them is the only moment
    the served directory does not exist, and it is bounded by a `rename(2)`;
    before this, the run overwrote deterministic filenames in place, so a rerun
    that produced fewer or shifted frames left permanent orphans, and a crash
    mid-extraction left the previous generation's rows pointing at bytes the new
    run had already replaced.

    Returns the number of orphan files removed. The reconciliation is a
    belt-and-braces pass over the published directory — after the swap it should
    contain exactly the staged frames — because the rows are the authority on
    what exists and this is the one place that can prove it.
    """
    final.parent.mkdir(parents=True, exist_ok=True)
    retired = final.with_name(f"{final.name}.retired-{token_hex(4)}")
    if final.exists():
        os.replace(final, retired)
    os.replace(staging, final)
    _rmtree(retired)
    removed = 0
    for path in sorted(final.iterdir()):
        if path.is_file() and path.name not in keep:
            _unlink(path)
            removed += 1
    return removed


def _rmtree(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError as exc:  # pragma: no cover - permissions
        logger.warning("could not remove %s: %s", path, exc)


def _all_unreadable(count: int, first: str) -> str:
    """Every frame in the stage was refused: there is nothing partial to keep."""
    return (
        f"the worker could not read any of the {count} frame(s) sent, so nothing "
        f"was written. First: {first}"
    )


def _not_yet(exc: NotYetAvailable) -> ItemFailed:
    """A premiere or a stream still finishing is a *later*, not a *never*.

    The item is deferred by the runner's backoff machinery — the source's own
    `release_timestamp` when it named one, `LIFECYCLE_RETRY_S` otherwise, capped
    so a premiere three days out does not park a claim for three days.
    """
    delay = exc.retry_after_s if exc.retry_after_s is not None else LIFECYCLE_RETRY_S
    return ItemFailed(
        "E_NOT_READY_YET",
        str(exc),
        retryable=True,
        retry_after_s=min(max(int(delay), 60), LIFECYCLE_RETRY_MAX_S),
    )


def _rate_limited(exc: Exception, message: str) -> ItemFailed:
    """One typed failure for every 429, carrying the window if the source gave one.

    `retry_after_s` travels as far as it is known: the runner defers the job by
    exactly that long, and falls back to its own cool-off when it is None.
    """
    retry_after = getattr(exc, "retry_after_s", None)
    return ItemFailed(
        "E_RATE_LIMIT",
        message,
        retryable=True,
        retry_after_s=int(retry_after) if retry_after is not None else None,
    )


def _land_metadata(
    conn: Any, meta: VideoMeta, item_id: int
) -> tuple[int | None, store.Claim]:
    """Video row, chapters and links in one transaction, then claim the item."""
    video_id = store.upsert_video(conn, meta)
    store.replace_chapters(conn, video_id, meta)
    claim = store.attach_video(conn, item_id, video_id)
    return (video_id if claim.ok else None), claim


def _dimension_mismatch(
    vectors: Sequence[Sequence[float]],
    dimensions: int | None,
    expected: int,
    served: str | None,
    wanted: str,
) -> str | None:
    """The response's `model`/`dimensions` are the authoritative drift check.

    They describe the vector about to be written next to vectors from a
    previous run; a width that does not match means the two are not comparable,
    and writing them anyway would poison the index silently.
    """
    width = dimensions if dimensions is not None else (len(vectors[0]) if vectors else None)
    if width is not None and int(width) != int(expected):
        return (
            f"the worker returned {width}-d vectors but the corpus stores "
            f"{expected}-d — nothing was written."
        )
    if vectors and len(vectors[0]) != expected:
        return (
            f"the worker returned {len(vectors[0])}-d vectors but the corpus stores "
            f"{expected}-d — nothing was written."
        )
    if served and wanted and served.lower() != wanted.lower():
        return f"the worker is serving {served!r} but the corpus is embedded with {wanted!r}."
    return None


def _state_of(stages: dict[str, Any], name: str) -> str:
    row = stages.get(name)
    return str(row["state"]) if row is not None else "missing"


def _settled(stages: dict[str, Any], name: str) -> bool:
    """The stage will not run again on its own: its inputs are free."""
    return _state_of(stages, name) in ("done", "skipped")


def _relative(path: Path | None, root: Path) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(root))
    except ValueError:  # pragma: no cover - an operator-supplied absolute path
        return str(path)


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - permissions
        logger.warning("could not delete %s: %s", path, exc)
