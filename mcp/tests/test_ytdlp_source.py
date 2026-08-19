"""`YtDlpSource` itself — the one class in the repo that talks to YouTube.

The rest of the suite fakes the whole source (`RecordedSource`); these tests
fake one level lower, at `yt_dlp.YoutubeDL`, because the two things under test
here *are* the calls made to yt-dlp: how many, and what a failure means.

Both come from `research/ytdlp-usage-audit-2026-08-10.md`:

* §1 — a bot-check is an IP block for the whole logged-out session, not a
  transient extractor error. It must not spend the short inner retries, which
  are 35 seconds against a 60-90 minute wave.
* §5 — one item used to pay three full extractions (metadata, audio, video).
  The probe's info dict feeds all three.

Nothing here reaches the network: `yt_dlp.YoutubeDL` is replaced wholesale and
no real extractor is ever constructed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from vidtheque_mcp.pipeline import sources
from vidtheque_mcp.pipeline.settings import PipelineSettings
from vidtheque_mcp.pipeline.sources import RateLimited, Unavailable, YtDlpSource

BOT_CHECK = (
    "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you’re not a bot. "
    "Use --cookies-from-browser or --cookies for the authentication."
)
TOO_MANY = "ERROR: [youtube] dQw4w9WgXcQ: HTTP Error 429: Too Many Requests"
STALE = "ERROR: unable to download video data: HTTP Error 403: Forbidden"
URL = "https://youtu.be/dQw4w9WgXcQ"
INFO: dict[str, Any] = {"id": "dQw4w9WgXcQ", "_type": "video", "formats": [{"format_id": "251"}]}


class FakeYoutubeDL:
    """`yt_dlp.YoutubeDL`, minus yt-dlp.

    Each of `extract_info` and `process_ie_result` is driven by a script: a
    list whose entries are either an exception to raise or a value to return.
    The last entry repeats, so `[Boom()]` means "always fails" and
    `[Boom(), INFO]` means "fails once, then works".
    """

    def __init__(self, rig: "Rig") -> None:
        self.rig = rig

    def __enter__(self) -> "FakeYoutubeDL":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def sanitize_info(self, info: dict[str, Any]) -> dict[str, Any]:
        return info

    def extract_info(self, url: str, download: bool = False) -> Any:
        self.rig.extractions.append(url)
        return self.rig.step(self.rig.on_extract, download)

    def process_ie_result(self, info: dict[str, Any], download: bool = False) -> Any:
        self.rig.processed.append(info)
        return self.rig.step(self.rig.on_process, download)


class Rig:
    """The scripted fake plus everything worth asserting about the calls."""

    def __init__(
        self,
        on_extract: list[Any] | None = None,
        on_process: list[Any] | None = None,
        writes: Path | None = None,
    ) -> None:
        self.on_extract = list(on_extract or [INFO])
        self.on_process = list(on_process or [INFO])
        self.writes = writes
        self.extractions: list[str] = []
        self.processed: list[dict[str, Any]] = []
        self.opts: list[dict[str, Any]] = []
        self.slept: list[float] = []

    def __call__(self, opts: dict[str, Any]) -> FakeYoutubeDL:
        self.opts.append(opts)
        return FakeYoutubeDL(self)

    def step(self, script: list[Any], download: bool) -> Any:
        result = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(result, Exception):
            raise result
        if download and self.writes is not None:
            self.writes.parent.mkdir(parents=True, exist_ok=True)
            self.writes.write_bytes(b"downloaded")
        return result


def install(monkeypatch: pytest.MonkeyPatch, rig: Rig) -> YtDlpSource:
    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", rig)
    source = YtDlpSource(PipelineSettings())
    source._sleeper = rig.slept.append  # type: ignore[method-assign]
    return source


# ============================================================== retry classes


def test_a_bot_check_trips_the_breaker_with_no_inner_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit §1: the wave lasts 60-90 minutes; 5+10+20 s cannot outlive it.

    All the inner retries did was spend the item's attempts and add requests to
    a block that hammering makes longer. One request, then `E_RATE_LIMIT`, and
    `VIDTHEQUE_RATE_LIMIT_BACKOFF_S` owns the wait.
    """
    rig = Rig(on_extract=[RuntimeError(BOT_CHECK)])
    source = install(monkeypatch, rig)

    with pytest.raises(RateLimited) as caught:
        source.probe(URL)

    assert len(rig.extractions) == 1  # asked once. Not four times.
    assert rig.slept == []  # and waited zero seconds doing it
    assert "not a bot" in str(caught.value)


def test_the_bot_check_ignores_the_extractor_retries_knob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The finding, stated as the coupling it removes: one env var used to set
    both classes, so `VIDTHEQUE_YTDLP_EXTRACTOR_RETRIES=3` bought three
    bot-check retries nobody asked for."""
    rig = Rig(on_extract=[RuntimeError(BOT_CHECK)])
    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", rig)
    source = YtDlpSource(PipelineSettings(extractor_retries=9))
    source._sleeper = rig.slept.append  # type: ignore[method-assign]

    with pytest.raises(RateLimited):
        source.probe(URL)
    assert len(rig.extractions) == 1


def test_a_transient_429_still_gets_its_inner_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other class, unchanged: a 429 is about *this request* and often
    clears in seconds, so it keeps yt-dlp's exp=5:120 shape."""
    rig = Rig(on_extract=[RuntimeError(TOO_MANY), RuntimeError(TOO_MANY), INFO])
    source = install(monkeypatch, rig)

    assert source.probe(URL) == INFO
    assert len(rig.extractions) == 3
    assert [int(s) for s in rig.slept] == [5, 10]  # plus a second of jitter each


def test_a_429_that_outlasts_the_retries_is_still_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = Rig(on_extract=[RuntimeError(TOO_MANY)])
    source = install(monkeypatch, rig)

    with pytest.raises(RateLimited):
        source.probe(URL)
    # `extractor_retries` retries, so one call more than that.
    assert len(rig.extractions) == PipelineSettings().extractor_retries + 1
    assert [int(s) for s in rig.slept] == [5, 10, 20]


def test_a_stable_verdict_is_neither_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """The age gate reads like the bot-check and is the opposite of it."""
    rig = Rig(on_extract=[RuntimeError("ERROR: Sign in to confirm your age")])
    source = install(monkeypatch, rig)

    with pytest.raises(Unavailable):
        source.probe(URL)
    assert len(rig.extractions) == 1
    assert rig.slept == []


# ========================================================= one extraction, reused


def test_the_media_downloads_reuse_the_probe_extraction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Audit §5: metadata, audio and video used to be three full extractions.

    With the probe's info dict in hand the two downloads select their formats
    out of it — `process_ie_result`, the same call `--load-info-json` makes —
    so the item pays one extraction instead of three.
    """
    rig = Rig(writes=tmp_path / "audio" / "dQw4w9WgXcQ.opus")
    source = install(monkeypatch, rig)

    media = source.download_audio(URL, "dQw4w9WgXcQ", tmp_path / "audio", "opus", INFO)

    assert media.path.name == "dQw4w9WgXcQ.opus"
    assert rig.extractions == []  # the whole point
    assert len(rig.processed) == 1
    assert source.stale_info_refreshes == 0
    # Format selection is still per-download: the audio ladder went with it.
    assert rig.opts[0]["format"] == "bestaudio[abr<=80]/bestaudio/best"


def test_a_download_without_the_probe_info_extracts_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`info` is an optimisation, never a requirement: a resume that never
    probed (the locally-resolved path) still downloads."""
    rig = Rig(writes=tmp_path / "media" / "dQw4w9WgXcQ.mp4")
    source = install(monkeypatch, rig)

    source.download_video(URL, "dQw4w9WgXcQ", tmp_path / "media", 1080)

    assert rig.extractions == [URL]
    assert rig.processed == []


def test_the_probe_info_survives_the_download_unmutated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`process_ie_result` mutates what it is given, and the *second* download
    needs the same dict. It gets a copy, so audio cannot spoil video."""
    rig = Rig(writes=tmp_path / "audio" / "dQw4w9WgXcQ.opus")
    source = install(monkeypatch, rig)

    source.download_audio(URL, "dQw4w9WgXcQ", tmp_path / "audio", "opus", INFO)
    rig.processed[0]["formats"].append({"format_id": "vandalised"})

    assert INFO["formats"] == [{"format_id": "251"}]


def test_a_stale_format_url_falls_back_to_one_fresh_extraction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Manifests and format URLs expire — the audit's own caveat on §5.

    A 403 on a download from a reused dict buys exactly one re-extraction:
    today's code path, one request later. Note this is *better* than before,
    where the same 403 deferred the whole job for the cool-off.
    """
    rig = Rig(
        on_process=[RuntimeError(STALE)],
        writes=tmp_path / "media" / "dQw4w9WgXcQ.mp4",
    )
    source = install(monkeypatch, rig)

    media = source.download_video(URL, "dQw4w9WgXcQ", tmp_path / "media", 1080, INFO)

    assert media.path.name == "dQw4w9WgXcQ.mp4"
    assert len(rig.processed) == 1
    assert rig.extractions == [URL]  # once, not once per retry
    assert source.stale_info_refreshes == 1


def test_a_re_extraction_that_403s_again_is_throttling_after_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The disambiguation, and the reason the fallback is safe: if the URLs
    were not stale, the fresh extraction says so in the language `_run` already
    classifies, and the job backs off exactly as it used to."""
    rig = Rig(on_process=[RuntimeError(STALE)], on_extract=[RuntimeError(STALE)])
    source = install(monkeypatch, rig)

    with pytest.raises(RateLimited):
        source.download_video(URL, "dQw4w9WgXcQ", tmp_path / "media", 1080, INFO)
    assert source.stale_info_refreshes == 1


@pytest.mark.parametrize("message", [BOT_CHECK, TOO_MANY])
def test_a_blocked_box_does_not_spend_a_request_re_extracting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, message: str
) -> None:
    """Re-extraction is for staleness. During a block the cheapest thing this
    box can do is stop asking, so these two skip the fallback entirely."""
    rig = Rig(on_process=[RuntimeError(message)])
    source = install(monkeypatch, rig)

    with pytest.raises(RateLimited):
        source.download_audio(URL, "dQw4w9WgXcQ", tmp_path / "audio", "opus", INFO)
    assert rig.extractions == []
    assert source.stale_info_refreshes == 0


def test_the_classifier_keeps_the_bot_check_inside_the_rate_limit_class() -> None:
    """Two retry classes, one typed code: everything downstream — `_rate_limited`,
    `E_RATE_LIMIT`, the sticky job error — still sees one thing."""
    assert sources._is_bot_check(BOT_CHECK)
    assert sources._is_rate_limit(BOT_CHECK)
    assert not sources._is_bot_check(TOO_MANY)
    assert not sources._is_bot_check("ERROR: Sign in to confirm your age")
    assert isinstance(sources._classified(BOT_CHECK, URL), RateLimited)
    assert isinstance(sources._classified(TOO_MANY, URL), RateLimited)


def test_the_download_403_label_does_not_promise_a_rate_limit() -> None:
    """The download 403 has two causes and one string (2026-08-19: a broken
    stable yt-dlp served it on every retry for hours, and every job event said
    "rate-limited"). Same class, same typed code — but the message must carry
    both readings and name the fix for the persistent one."""
    classified = sources._classified(STALE, URL)
    assert isinstance(classified, RateLimited)
    assert "rate-limited" not in str(classified)
    assert "updating yt-dlp" in str(classified)
    # An explicit 429 keeps the plain reading: that one *is* a rate limit.
    assert "rate-limited this box" in str(sources._classified(TOO_MANY, URL))
