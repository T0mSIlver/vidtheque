"""Local resolution: the fetch that costs no request.

`_stage_fetch` resolves the item's URL to a video, and until 2026-08-10 it did
that the only way it knew how — sleep out the politeness gap, then ask YouTube
who this URL is. Measured that night on a pure re-embed: 78 videos, ~90 s of
sleep and one round trip each to learn an identity `videos` already held, in
front of ~15 s of local work. 2.5 hours instead of ~20 minutes, and 78 requests
a rate-limited box did not have to spend.

So the tests here are about two things and nothing else: **that the fast path is
taken when it is free** (no probe, no sleep), and **that it is not taken a
moment before that** — an unknown video, a `force_reindex`, a container URL, a
stale `fetch` key, an outstanding stage that needs media or a transcript. The
claim is the third: skipping a round trip must not skip the thing that stops two
jobs embedding one video at once.

CPU-only. The source is canned info dicts and the worker is the protocol with no
HTTP behind it, same as the rest of the pipeline suite.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from vidtheque_mcp.config import Settings
from vidtheque_mcp.jobs import store as jobs_store
from vidtheque_mcp.jobs.runner import ItemContext, ItemFailed, ItemSkipped
from vidtheque_mcp.pipeline.runner import ItemRun
from vidtheque_mcp.pipeline.settings import PipelineSettings
from vidtheque_mcp.errors import ToolError
from vidtheque_mcp.pipeline.sources import (
    SourceError,
    is_indexable_url,
    looks_like_container,
    parse_info,
    source_ref_of,
)
from vidtheque_mcp.tools.indexing import normalize_url

from .pipeline_fakes import SECOND_URL, VIDEO_URL
from .test_pipeline_e2e import Harness, harness

SOURCE_ID = "aB3dEfG7hIj"

# Unlike everywhere else in the suite the politeness gap is *on* here: it is the
# cost being measured. The sleep itself is spied, never served.
POLITE = PipelineSettings(between_videos_s=5.0, sleep_subtitles_s=0.0, max_shot_seconds=2.0)

# A URL that is a container *and* names a video id. The syntactic container
# check earns its keep on exactly this shape.
PLAYLIST_WITH_V = f"https://www.youtube.com/playlist?list=PL9tOrKPmQ4nAbC&v={SOURCE_ID}"


def spy_on_sleep(parts: Harness) -> list[float]:
    """Record the politeness sleeps instead of serving them.

    `_between_videos` only waits once a previous video has set the clock, so the
    clock is primed: without that, the first item of any job sleeps for nothing
    and "no sleep" would pass on the slow path too.
    """
    pipeline = parts.parts.runner.pipeline
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    pipeline._sleep = record
    pipeline._last_fetch_at = time.monotonic()
    return slept


async def indexed(settings: Settings, clip: Path) -> Harness:
    """One video, fully indexed, with the politeness clock running."""
    parts = await harness(settings, clip, pipeline_settings=POLITE)
    spy_on_sleep(parts)
    await parts.index(url=VIDEO_URL)
    assert await parts.run() is True
    return parts


async def close(parts: Harness) -> None:
    await parts.db.close()
    parts.parts.auth.close()


async def stale_embeds(parts: Harness) -> None:
    """What migration 0004 does to a corpus when the checkpoint moves."""
    await parts.db.write(
        lambda c: c.execute(
            "UPDATE video_stages SET state = 'pending', model_key = NULL "
            "WHERE stage IN ('text_embed', 'frame_embed')"
        )
    )


async def contexts(parts: Harness, *urls: str) -> list[ItemContext]:
    """One job over these URLs, as the item contexts the pipeline would get."""
    public_id = await parts.db.write(
        lambda c: jobs_store.create_job(c, "index", {}, [(url, None) for url in urls])
    )
    rows = await parts.rows(
        "SELECT i.id AS item_id, i.job_id AS job_id, i.source_url FROM job_items i "
        "JOIN jobs j ON j.id = i.job_id WHERE j.public_id = ? ORDER BY i.seq",
        (public_id,),
    )
    return [
        ItemContext(
            db=parts.db,
            job_id=int(row["job_id"]),
            job_public_id=public_id,
            item_id=int(row["item_id"]),
            source_url=str(row["source_url"]),
            video_id=None,
        )
        for row in rows
    ]


async def resolve(parts: Harness, url: str = VIDEO_URL, **args) -> tuple[bool, ItemRun]:
    """`_resolve_locally` against a real job item, and what it left on the run."""
    run = ItemRun(ctx=(await contexts(parts, url))[0], args=args)
    taken = await parts.parts.runner.pipeline._resolve_locally(run)
    return taken, run


# --------------------------------------------------------------- the fast path


async def test_a_pure_re_embed_never_touches_youtube(settings: Settings, clip: Path) -> None:
    """The measured problem, as an assertion: a re-embed is local work end to
    end, so it pays neither the round trip nor the gap that exists for it."""
    parts = await indexed(settings, clip)
    try:
        before = await parts.stages()
        await stale_embeds(parts)
        parts.source.probes.clear()
        parts.source.downloads.clear()
        parts.worker.calls.clear()
        slept = spy_on_sleep(parts)

        await parts.index(url=VIDEO_URL)
        assert await parts.run() is True

        assert parts.source.probes == [], "the identity came out of the index"
        assert slept == [], "the gap is YouTube's, and YouTube was not asked"
        assert parts.source.downloads == []
        assert any(c.startswith("embed:document") for c in parts.worker.calls), "it did the work"
        after = await parts.stages()
        assert after["text_embed"]["state"] == "done"
        assert after["frame_embed"]["state"] == "done"
        # `fetch` was already done with this key; re-recording it would claim a
        # stage ran that did not.
        assert after["fetch"]["finished_at"] == before["fetch"]["finished_at"]
        video = await parts.one("SELECT index_state FROM videos")
        assert video["index_state"] == "ready"
    finally:
        await close(parts)


async def test_the_log_says_where_the_metadata_came_from(
    settings: Settings, clip: Path
) -> None:
    """`fetched metadata for …` would be a lie on this path, and the job log is
    the record an operator reads when a night looks too fast to be true."""
    parts = await indexed(settings, clip)
    try:
        await stale_embeds(parts)
        await parts.index(url=VIDEO_URL)
        assert await parts.run() is True

        events = await parts.events()
        assert any("resolved locally; nothing to fetch" in e for e in events), events
        assert any("came from the index, not YouTube" in e for e in events), events
    finally:
        await close(parts)


async def test_what_the_fast_path_hands_downstream(settings: Settings, clip: Path) -> None:
    """Everything `_land_metadata` leaves on the run, minus what only a probe
    can answer — and the `indexing` flag, because both paths hold the video."""
    parts = await indexed(settings, clip)
    try:
        await stale_embeds(parts)
        taken, run = await resolve(parts)

        assert taken is True
        video = await parts.one("SELECT id, index_state FROM videos")
        assert run.video_id == int(video["id"])
        assert video["index_state"] == "indexing"
        assert run.meta is not None
        assert run.meta.source_id == SOURCE_ID
        assert run.meta.title == "Paged attention, end to end"
        assert run.meta.url == VIDEO_URL
        assert run.meta.duration_s == 620.0
        # The probe's inventory is not invented: nobody enumerated these.
        assert run.meta.subtitles == ()
        assert run.meta.chapters == ()
        assert run.stages["text_embed"]["state"] == "pending"
        assert run.stages["keyframe"]["state"] == "done"
    finally:
        await close(parts)


# --------------------------------------------------------------- the slow path


async def test_a_video_the_index_does_not_hold_still_probes(
    settings: Settings, clip: Path
) -> None:
    """The control. A URL nobody has indexed is exactly what the probe is for,
    and it pays the gap in front of it like every other YouTube-touching item."""
    parts = await indexed(settings, clip)
    try:
        parts.source.probes.clear()
        slept = spy_on_sleep(parts)

        await parts.index(url=SECOND_URL)
        assert await parts.run() is True

        assert parts.source.probes == [SECOND_URL]
        assert slept, "the politeness gap is unchanged for anything that asks YouTube"
    finally:
        await close(parts)


async def test_an_unknown_url_is_not_resolvable(settings: Settings, clip: Path) -> None:
    parts = await indexed(settings, clip)
    try:
        taken, _ = await resolve(parts, SECOND_URL)
        assert taken is False
    finally:
        await close(parts)


async def test_force_reindex_is_a_fetch(settings: Settings, clip: Path) -> None:
    """It throws the recorded stages away, so the metadata is the work again."""
    parts = await indexed(settings, clip)
    try:
        await stale_embeds(parts)
        taken, _ = await resolve(parts, force_reindex=True)
        assert taken is False
    finally:
        await close(parts)


def test_a_url_only_names_a_video_on_a_host_we_index_from() -> None:
    """The 2026-08-09 review's MEDIUM, at the parser.

    `videos.source_id` is half of the unique key, so an eleven-character id in
    the query string of *any* URL used to resolve to the YouTube row that
    happens to hold it. The identity is the pair, and the pair is only claimed
    for a host the corpus could have been indexed from.
    """
    for hostile in (
        f"https://evil.example/?v={SOURCE_ID}",
        f"https://evil.example/watch?v={SOURCE_ID}",
        # The userinfo trick: the host is `evil.example`, and `urlsplit` knows.
        f"https://www.youtube.com@evil.example/watch?v={SOURCE_ID}",
        f"https://notyoutube.com/shorts/{SOURCE_ID}",
        # A lookalike suffix is a different host, not a YouTube one.
        f"https://youtube.com.evil.example/watch?v={SOURCE_ID}",
        f"javascript:x?v={SOURCE_ID}",
        "https://www.youtube.com/watch?v=too-short",
    ):
        assert source_ref_of(hostile) is None, hostile

    for legitimate in (
        f"https://youtu.be/{SOURCE_ID}",
        f"https://youtu.be/{SOURCE_ID}?t=90",
        f"youtu.be/{SOURCE_ID}",  # scheme-less, the way people paste them
        f"https://www.youtube.com/watch?v={SOURCE_ID}",
        f"https://www.youtube.com/watch?v={SOURCE_ID}&list=PL9tOrKPmQ4nAbC",
        f"https://m.youtube.com/watch?v={SOURCE_ID}",
        f"https://music.youtube.com/watch?v={SOURCE_ID}",
        f"http://YouTube.com/watch?v={SOURCE_ID}",
        f"https://www.youtube.com/shorts/{SOURCE_ID}",
        f"https://www.youtube-nocookie.com/embed/{SOURCE_ID}",
    ):
        assert source_ref_of(legitimate) == ("youtube", SOURCE_ID), legitimate


def test_a_submitted_url_must_be_youtube_before_yt_dlp_ever_sees_it() -> None:
    """The slow path was `yt-dlp fetches it`, and that was the whole problem.

    `source_ref_of` returning None means "I cannot name this locally", which is
    correct for every playlist and channel URL too — so it was never a
    boundary. The only check on a submitted URL was `^https?://`, while the
    error message beside it promised YouTube. An unrecognised host fell through
    to the fetch, from inside the home network.

    (2026-08-10 audit, F-15.)
    """
    for hostile in (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8100/dashboard",
        "http://[::1]:8080/",
        "https://attacker.example/*.mp4",
        f"https://www.youtube.com@evil.example/watch?v={SOURCE_ID}",
        f"https://youtube.com.evil.example/watch?v={SOURCE_ID}",
        "https://youtube.com:8080@evil.example/",
        "file:///etc/passwd",
        "ftp://youtube.com/",
    ):
        assert not is_indexable_url(hostile), hostile
        with pytest.raises(ToolError) as caught:
            normalize_url(hostile)
        assert caught.value.code == "E_UNSUPPORTED_SOURCE"

    # Every shape the corpus is actually built from still goes through, and
    # that includes the container URLs `source_ref_of` cannot name.
    for legitimate in (
        f"https://youtu.be/{SOURCE_ID}",
        f"https://www.youtube.com/watch?v={SOURCE_ID}",
        "https://www.youtube.com/playlist?list=PL9tOrKPmQ4nAbC",
        "https://www.youtube.com/@aiDotEngineer",
        "https://www.youtube.com/c/SomeChannel/videos",
        f"https://m.youtube.com/watch?v={SOURCE_ID}",
        f"https://www.youtube-nocookie.com/embed/{SOURCE_ID}",
    ):
        assert is_indexable_url(legitimate), legitimate
        assert normalize_url(legitimate) == legitimate

    # A bare id is still the shorthand it always was.
    assert normalize_url(SOURCE_ID) == f"https://youtu.be/{SOURCE_ID}"


def test_a_remote_id_that_would_become_a_glob_is_refused() -> None:
    """`media_candidates` globs the id and retention unlinks every match.

    A video served from `https://host/*.mp4` gives the generic extractor an id
    of `*`, which matched every file in media/ and deleted all of them. The
    grammar is exact rather than a denylist, because the only ids this pipeline
    should ever see are YouTube's. (2026-08-10 audit, F-16.)
    """
    for bad in ("*", "../../etc/passwd", "a" * 400, "id with spaces", "a/b"):
        with pytest.raises(SourceError):
            parse_info({"id": bad, "title": "t", "duration": 10}, "https://youtu.be/x")

    meta = parse_info(
        {"id": SOURCE_ID, "title": "t", "duration": 10}, f"https://youtu.be/{SOURCE_ID}"
    )
    assert meta.source_id == SOURCE_ID


async def test_a_hostile_url_carrying_a_known_id_takes_the_slow_path(
    settings: Settings, clip: Path
) -> None:
    """End to end: the free identity is refused, so the probe decides.

    Without it, `https://evil.example/?v=<known id>` resolved to the indexed
    video, skipped the probe entirely, and ran that job's stages and tags
    against a video the caller never named.
    """
    parts = await indexed(settings, clip)
    try:
        await stale_embeds(parts)
        taken, run = await resolve(parts, f"https://evil.example/?v={SOURCE_ID}")
        assert taken is False
        assert run.meta is None, "nothing was resolved on a stranger's behalf"
        assert not run.stages, "and no half-built run was left for the slow path"
    finally:
        await close(parts)


async def test_a_container_url_is_never_resolved_locally(
    settings: Settings, clip: Path
) -> None:
    """`/playlist?list=…&v=<id>` names a video inside a URL that means "fan me
    out". `_maybe_expand` screens it first; this is the guard behind that."""
    assert source_ref_of(PLAYLIST_WITH_V) == ("youtube", SOURCE_ID)
    assert looks_like_container(PLAYLIST_WITH_V) is True
    parts = await indexed(settings, clip)
    try:
        await stale_embeds(parts)
        taken, _ = await resolve(parts, PLAYLIST_WITH_V)
        assert taken is False
    finally:
        await close(parts)


async def test_a_stale_fetch_key_takes_the_slow_path(settings: Settings, clip: Path) -> None:
    """A yt-dlp upgrade moves `fetch`'s own key. Then this item *is* a fetch,
    and the probe is the work rather than an overhead on it."""
    parts = await indexed(settings, clip)
    try:
        await stale_embeds(parts)
        await parts.db.write(
            lambda c: c.execute(
                "UPDATE video_stages SET model_key = 'yt-dlp-1999.1.1' WHERE stage = 'fetch'"
            )
        )
        taken, _ = await resolve(parts)
        assert taken is False
    finally:
        await close(parts)


@pytest.mark.parametrize("stage", ["fetch", "keyframe", "stt"])
async def test_an_outstanding_stage_that_needs_the_source_takes_the_slow_path(
    settings: Settings, clip: Path, stage: str
) -> None:
    """The resume that still wants media or a transcript. `keyframe` is
    `want_media`; `stt` needs either the audio or the subtitle inventory, and
    both come out of the info dict."""
    parts = await indexed(settings, clip)
    try:
        await parts.db.write(
            lambda c: c.execute(
                "UPDATE video_stages SET state = 'pending' WHERE stage = ?", (stage,)
            )
        )
        taken, _ = await resolve(parts)
        assert taken is False
    finally:
        await close(parts)


async def test_a_worker_that_is_down_cannot_talk_the_probe_away(
    settings: Settings, clip: Path
) -> None:
    """The subtle one. `_should_run`'s stt clause is "only re-transcribe when we
    can do better", so an unreachable worker reads as "stt will not run" — but
    `_stage_transcript` re-probes, and a worker back in thirty seconds turns
    that into a whisperX call with no audio and a caption fallback with no
    inventory. The predicate asks with the worker assumed healthy."""
    parts = await indexed(settings, clip)
    try:
        await stale_embeds(parts)
        # A transcript that came from captions: its key can never equal
        # `config['stt.model']`, so the clause hangs entirely on worker health.
        await parts.db.write(
            lambda c: c.execute(
                "UPDATE video_stages SET model_key = 'youtube-asr-en' WHERE stage = 'stt'"
            )
        )
        taken, run = await resolve(parts)
        assert taken is False
        assert run.worker_ok is False, "the observed health was never the question"
    finally:
        await close(parts)


# -------------------------------------------------------------------- the claim


async def test_the_fast_path_still_takes_the_per_video_claim(
    settings: Settings, clip: Path
) -> None:
    """Two jobs, one video: the second is refused with the holder named. Without
    this, skipping the round trip would have skipped the only thing stopping two
    jobs embedding one video at the same time."""
    parts = await indexed(settings, clip)
    try:
        await stale_embeds(parts)
        holder, run = await resolve(parts)
        assert holder is True

        with pytest.raises(ItemFailed) as raised:
            await resolve(parts)
        assert raised.value.code == "E_INDEXING"
        assert run.ctx.job_public_id in str(raised.value)
    finally:
        await close(parts)


async def test_a_duplicate_inside_one_job_is_still_a_skip(
    settings: Settings, clip: Path
) -> None:
    """A playlist listing the same video twice, resolved locally: bookkeeping,
    not a collision — the other item is doing the work."""
    parts = await indexed(settings, clip)
    try:
        await stale_embeds(parts)
        first, second = await contexts(parts, VIDEO_URL, VIDEO_URL)
        pipeline = parts.parts.runner.pipeline

        assert await pipeline._resolve_locally(ItemRun(ctx=first, args={})) is True
        with pytest.raises(ItemSkipped) as raised:
            await pipeline._resolve_locally(ItemRun(ctx=second, args={}))
        assert raised.value.code == "E_DUPLICATE_ITEM"
    finally:
        await close(parts)
