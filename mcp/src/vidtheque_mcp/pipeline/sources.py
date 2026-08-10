"""The yt-dlp seam: info dicts in, normalized records out.

Two halves, deliberately separated so the second is testable without a network:

* ``parse_info`` and friends are **pure functions over an info dict**. The test
  suite feeds them canned dicts captured from real extractions; nothing in a
  test ever constructs a ``YoutubeDL``.
* ``YtDlpSource`` is the only place that talks to YouTube. Every method is
  blocking and is called through ``asyncio.to_thread`` — yt-dlp is a sync
  library and pretending otherwise would block the event loop for minutes.

Politeness is not optional here (research §5.5): a *single* invocation on one
video, from a cold residential IP, earned ``HTTP Error 429`` on its **third**
subtitle request. The `-t sleep` preset is the floor, the language list is
capped at two, and translated subtitle enumeration is skipped — without
``youtube:skip=translated_subs`` the info dict lists ~200 machine-translated
caption tracks.
"""

from __future__ import annotations

import copy
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol, Sequence
from urllib.parse import parse_qs, urlsplit

from .settings import PipelineSettings

logger = logging.getLogger(__name__)

# Word-timed captions only exist in this format (research §5.4).
PREFERRED_SUB_EXT = ("json3", "srv3", "vtt", "srt", "ttml")


class SourceError(RuntimeError):
    """Extraction failed for a reason retrying will not fix."""


class RateLimited(SourceError):
    """YouTube said 429. The job backs off; hammering earns a longer block."""


class Unavailable(SourceError):
    """Private, removed, members-only, age-gated — nothing to index, ever."""


class NotYetAvailable(SourceError):
    """A lifecycle state, not a verdict: it will be indexable, just not now.

    A premiere, a stream in progress, or the `post_live` window while YouTube
    finishes processing the VOD. These used to be `Unavailable` too, which made
    them `E_UNSUPPORTED_SOURCE` with `retryable=False` — a video permanently
    settled `failed` tonight for the crime of being published this afternoon.

    ``retry_after_s`` is the source's own answer when it gave one
    (``release_timestamp`` on a premiere); the caller supplies a default
    otherwise.
    """

    def __init__(self, message: str, retry_after_s: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


# ---------------------------------------------------------------------- records


@dataclass(frozen=True)
class SubtitleTrack:
    lang: str
    ext: str
    url: str
    automatic: bool

    @property
    def word_timed(self) -> bool:
        """Only YouTube's *auto* json3 carries per-word offsets.

        Verified both ways in research §5.4: the ASR track's segs carry
        ``tOffsetMs``; the same request against a human-authored track returns
        one blob per cue.
        """
        return self.automatic and self.ext == "json3"


@dataclass(frozen=True)
class Chapter:
    seq: int
    start_s: float
    end_s: float
    title: str


@dataclass(frozen=True)
class Link:
    seq: int
    url: str
    title: str | None
    t_s: float | None


@dataclass
class VideoMeta:
    """What the `fetch` stage writes to `videos` and its satellites."""

    source_id: str
    url: str
    title: str
    source: str = "youtube"
    description: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    published_at: int | None = None
    duration_s: float = 0.0
    language: str | None = None
    chapters: tuple[Chapter, ...] = ()
    chapters_json: str | None = None
    heatmap_json: str | None = None
    links: tuple[Link, ...] = ()
    subtitles: tuple[SubtitleTrack, ...] = ()
    extractor_version: str | None = None

    def track(self, langs: Sequence[str], *, automatic: bool) -> SubtitleTrack | None:
        """Best track for the wanted languages, preferring word-timed formats."""
        for candidate in self.candidates(langs):
            if candidate.automatic is automatic:
                return candidate
        return None

    def candidates(self, langs: Sequence[str]) -> list[SubtitleTrack]:
        """Every usable track, best first — not just the best one.

        Returning a single track meant one 403 or one malformed json3 ended the
        caption path outright: the French VTT beside it, the English auto track,
        and the manual inventory were never tried, and under `captions_only` the
        video failed for the sake of one URL.

        Order is the preference that was already implied: automatic before
        manual (only YouTube's auto json3 carries word offsets), the requested
        languages in the order they were asked for, and word-timed formats
        before the rest.
        """
        ordered: list[SubtitleTrack] = []
        for automatic in (True, False):
            for lang in langs:
                matches = [
                    track
                    for track in self.subtitles
                    if track.automatic is automatic and _lang_matches(track.lang, lang)
                ]
                matches.sort(key=lambda t: _ext_rank(t.ext))
                ordered += [track for track in matches if track not in ordered]
        return ordered


@dataclass(frozen=True)
class PlaylistEntry:
    url: str
    source_id: str | None
    title: str | None


@dataclass
class MediaFile:
    path: Path
    bytes: int = 0

    def __post_init__(self) -> None:
        if not self.bytes and self.path.exists():
            self.bytes = self.path.stat().st_size


# ---------------------------------------------------------------------- parsing


def _lang_matches(track_lang: str, wanted: str) -> bool:
    track = track_lang.lower()
    want = wanted.lower()
    return track == want or track.startswith(want + "-") or track == want + "-orig"


def _ext_rank(ext: str) -> int:
    try:
        return PREFERRED_SUB_EXT.index(ext)
    except ValueError:
        return len(PREFERRED_SUB_EXT)


_URL_RE = re.compile(r"https?://[^\s<>\)\]]+")
_TIMESTAMP_RE = re.compile(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})")


def extract_links(description: str | None, limit: int = 100) -> tuple[Link, ...]:
    """Description links, timestamped where the line says so.

    ``get-segment-context include_links`` wants "what was on screen and what did
    he link to at 12:03", so a URL on a line that starts with a timestamp gets
    that timestamp. Everything else is a video-level link (``t_s IS NULL``).
    """
    if not description:
        return ()
    links: list[Link] = []
    seen: set[str] = set()
    for line in description.splitlines():
        stamp = _TIMESTAMP_RE.match(line.strip())
        t_s: float | None = None
        if stamp:
            hours, minutes, seconds = stamp.groups()
            t_s = float(int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds))
        for match in _URL_RE.finditer(line):
            url = match.group(0).rstrip(".,;")
            if url in seen:
                continue
            seen.add(url)
            title = line.replace(url, "").strip(" -–—:|\t") or None
            links.append(Link(seq=len(links), url=url, title=title, t_s=t_s))
            if len(links) >= limit:
                return tuple(links)
    return tuple(links)


def _published_at(info: dict[str, Any]) -> int | None:
    for key in ("timestamp", "release_timestamp"):
        value = info.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    raw = info.get("upload_date")
    if isinstance(raw, str) and len(raw) == 8 and raw.isdigit():
        day = date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
        # Midnight UTC: yt-dlp's upload_date has no time component, and
        # inventing one would put a video in the wrong day for `published_after`.
        return int((day.toordinal() - date(1970, 1, 1).toordinal()) * 86_400)
    return None


def _chapters(info: dict[str, Any], duration: float) -> tuple[Chapter, ...]:
    raw = info.get("chapters")
    if not isinstance(raw, list):
        return ()
    out: list[Chapter] = []
    for seq, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        start = float(entry.get("start_time") or 0.0)
        end = entry.get("end_time")
        end_s = float(end) if isinstance(end, (int, float)) else duration or start
        out.append(
            Chapter(
                seq=seq,
                start_s=start,
                end_s=max(end_s, start),
                title=str(entry.get("title") or f"chapter {seq + 1}"),
            )
        )
    return tuple(out)


def _subtitles(info: dict[str, Any]) -> tuple[SubtitleTrack, ...]:
    tracks: list[SubtitleTrack] = []
    for key, automatic in (("subtitles", False), ("automatic_captions", True)):
        catalogue = info.get(key)
        if not isinstance(catalogue, dict):
            continue
        for lang, entries in catalogue.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict) or not entry.get("url"):
                    continue
                tracks.append(
                    SubtitleTrack(
                        lang=str(lang),
                        ext=str(entry.get("ext") or ""),
                        url=str(entry["url"]),
                        automatic=automatic,
                    )
                )
    return tuple(tracks)


def is_playlist(info: dict[str, Any]) -> bool:
    return str(info.get("_type") or "video") in ("playlist", "multi_video")


_CONTAINER_MARKERS = ("/playlist", "/channel/", "/user/", "/c/", "/@")
_CONTAINER_TAILS = ("/videos", "/streams", "/shorts", "/featured")


def looks_like_container(url: str) -> bool:
    """Is this a playlist/channel URL, judged before spending a request?

    Syntax, not extraction, because the cheap answer has to come first: probing
    a playlist URL with the normal options runs a full per-video extraction of
    every entry, which is exactly what `extract_flat` exists to avoid. A
    `watch?v=…&list=…` URL is *not* a container — `noplaylist` picks the single
    video out of it, which is what someone pasting that link means.
    """
    lowered = url.lower().split("#", 1)[0]
    if "watch?v=" in lowered or "youtu.be/" in lowered or "/shorts/" in lowered:
        return False
    path = lowered.split("?", 1)[0].rstrip("/")
    if any(marker in lowered for marker in _CONTAINER_MARKERS):
        return True
    return path.endswith(_CONTAINER_TAILS)


# The hosts a `videos.source = 'youtube'` row can legitimately have come from.
# Anything else is a URL we have never probed, whatever it has in its query
# string.
_YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }
)
_YOUTUBE_SHORT_HOSTS = frozenset({"youtu.be", "www.youtu.be"})

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# `/shorts/<id>` and `/embed/<id>`, the two path forms the corpus has ever been
# handed. `/watch?v=<id>` is read out of the query instead, because a `v` in a
# path segment is not the same claim as a `v` parameter.
_PATH_ID = re.compile(r"^/(?:shorts|embed)/([A-Za-z0-9_-]{11})(?:[/?]|$)")


def source_ref_of(url: str) -> tuple[str, str] | None:
    """The `(source, source_id)` a URL names — syntactically, and only if the
    *host* is one we index from.

    Lives here beside `looks_like_container` because it is the same kind of
    knowledge: what a URL *says* before anything asks the source. Two callers
    need it and neither wants a round trip — `index-video` deciding whether a
    URL is a video the corpus already holds, and the pipeline's local
    resolution (`runner._resolve_locally`) deciding whether it needs the probe
    at all. One parser, so the two can never disagree about what a URL means.

    **The host is load-bearing, not decoration.** This used to be one regex
    hunting for `v=` anywhere in the string, and `videos.source_id` is only
    half of the unique key — so `https://evil.example/?v=<a known id>` resolved
    to that known YouTube row, skipped the probe, and ran stages or applied the
    caller's tags against a video the URL had nothing to do with. Reported by
    the 2026-08-09 review. Now the answer carries the source it belongs to and
    the lookups key on both columns; anything unrecognised returns `None`,
    which is the slow path, which asks yt-dlp — the correct answer, just not
    the free one.
    """
    # yt-dlp takes `youtu.be/<id>` without a scheme and so do people pasting
    # links; without this, `urlsplit` reads the host as the first path segment
    # and every scheme-less URL would lose the fast path.
    parts = urlsplit(url if "://" in url else f"https://{url}")
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()

    if host in _YOUTUBE_SHORT_HOSTS:
        candidate = parts.path.lstrip("/").split("/", 1)[0]
        return ("youtube", candidate) if _VIDEO_ID.match(candidate) else None

    if host not in _YOUTUBE_HOSTS:
        return None

    match = _PATH_ID.match(parts.path)
    if match:
        return "youtube", match.group(1)
    for candidate in parse_qs(parts.query).get("v", ()):
        if _VIDEO_ID.match(candidate):
            return "youtube", candidate
    return None


def parse_info(
    info: dict[str, Any], fallback_url: str, extractor_version: str | None = None
) -> VideoMeta:
    """Normalize one info dict. Pure; the fixtures in the tests are real ones."""
    live_status = str(info.get("live_status") or "")
    if live_status in ("is_live", "is_upcoming", "post_live"):
        raise NotYetAvailable(
            f"{info.get('id') or fallback_url} is {live_status.replace('_', ' ')}; "
            "there is nothing stable to index yet — this is a lifecycle state, "
            "so the item comes back rather than failing.",
            retry_after_s=_seconds_until_release(info),
        )
    availability = info.get("availability")
    if availability not in (None, "public", "unlisted"):
        raise Unavailable(
            f"{info.get('id') or fallback_url} is {availability}; it cannot be fetched."
        )

    source_id = str(info.get("id") or "").strip()
    if not source_id:
        raise SourceError(f"no video id in the extraction of {fallback_url}")

    duration = float(info.get("duration") or 0.0)
    description = info.get("description")
    heatmap = info.get("heatmap")
    chapters_raw = info.get("chapters")
    return VideoMeta(
        source_id=source_id,
        url=str(info.get("webpage_url") or fallback_url),
        title=str(info.get("title") or source_id),
        source=_source_name(info),
        description=str(description) if description else None,
        channel_id=_str_or_none(info.get("channel_id") or info.get("uploader_id")),
        channel_name=_str_or_none(info.get("channel") or info.get("uploader")),
        published_at=_published_at(info),
        duration_s=duration,
        language=_str_or_none(info.get("language")),
        chapters=_chapters(info, duration),
        # Stored verbatim for provenance: query the thing you derived, keep the
        # thing you were given (index-schema §1.2).
        chapters_json=json.dumps(chapters_raw) if chapters_raw else None,
        # "Most replayed", 100 buckets, 0..1. Free popularity prior over video
        # time; nothing in the landscape survey has it (research §5.1).
        heatmap_json=json.dumps(heatmap) if heatmap else None,
        links=extract_links(description if isinstance(description, str) else None),
        subtitles=_subtitles(info),
        extractor_version=extractor_version,
    )


def _source_name(info: dict[str, Any]) -> str:
    """`videos.source` — 'youtube' for every YouTube extractor variant.

    ``public_id`` is generated from it, so a video that arrives once as
    `Youtube` and once as `youtube:tab` must not become two corpora.
    """
    key = (
        _str_or_none(info.get("extractor_key")) or _str_or_none(info.get("extractor")) or "youtube"
    ).lower()
    return "youtube" if key.startswith("youtube") else key.split(":", 1)[0]


def _str_or_none(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def playlist_entries(info: dict[str, Any], max_items: int) -> list[PlaylistEntry]:
    """Flat playlist/channel entries, capped, in order, deduplicated by id."""
    entries = info.get("entries")
    if not isinstance(entries, list):
        return []
    out: list[PlaylistEntry] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if is_playlist(entry):  # a channel's tab listing its playlists
            out.extend(playlist_entries(entry, max_items - len(out)))
            if len(out) >= max_items:
                break
            continue
        source_id = _str_or_none(entry.get("id"))
        url = _str_or_none(entry.get("url") or entry.get("webpage_url"))
        if url is None and source_id is not None:
            url = f"https://youtu.be/{source_id}"
        if url is None or (source_id is not None and source_id in seen):
            continue
        if source_id:
            seen.add(source_id)
        out.append(
            PlaylistEntry(url=url, source_id=source_id, title=_str_or_none(entry.get("title")))
        )
        if len(out) >= max_items:
            break
    return out


# ----------------------------------------------------------------------- seam


class Source(Protocol):
    """What the pipeline needs from a video host. Faked wholesale in tests."""

    def probe(self, url: str) -> dict[str, Any]: ...

    def expand(self, url: str, kind: str, max_items: int) -> list[PlaylistEntry]: ...

    def fetch_subtitle(self, track: SubtitleTrack) -> str: ...

    # `info` is the dict `probe` returned for the same video, when the caller
    # still has it: one extraction, three uses (audit 2026-08-10 §5). An
    # implementation may ignore it and extract again — it is an optimisation,
    # never a promise about what lands on disk.
    def download_audio(
        self,
        url: str,
        source_id: str,
        dest_dir: Path,
        codec: str,
        info: dict[str, Any] | None = None,
    ) -> MediaFile: ...

    def download_video(
        self,
        url: str,
        source_id: str,
        dest_dir: Path,
        max_height: int,
        info: dict[str, Any] | None = None,
    ) -> MediaFile: ...

    @property
    def version(self) -> str: ...


# ------------------------------------------------------------------- yt-dlp


class YtDlpSource:
    """The real thing. Blocking by nature — always call it in a thread."""

    def __init__(self, settings: PipelineSettings) -> None:
        self._settings = settings
        self._sleeper = time.sleep  # swapped in tests that exercise backoff
        # How many times a reused probe extraction turned out to be stale and
        # cost a second extraction after all (`_download`). Process-lifetime,
        # logged each time it moves: it is the number that says whether
        # consolidation is paying for itself on this box.
        self.stale_info_refreshes = 0

    @property
    def version(self) -> str:
        from yt_dlp.version import __version__

        return f"yt-dlp-{__version__}"

    # ------------------------------------------------------------------ opts

    def _base_opts(self) -> dict[str, Any]:
        s = self._settings
        extractor_args: dict[str, dict[str, list[str]]] = {
            # Without this the info dict enumerates ~200 machine-translated
            # caption tracks (research §5.1).
            "youtube": {"skip": ["translated_subs"]},
        }
        if s.player_client:
            extractor_args["youtube"]["player_client"] = [s.player_client]
        if s.jitless:
            # We are executing YouTube's JavaScript on a box that also hosts the
            # MCP server. JIT-less Deno is cheap insurance (research §5.0).
            extractor_args["youtube-ejs"] = {"jitless": ["true"]}
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "extractor_args": extractor_args,
            "extractor_retries": s.extractor_retries,
            # The CLI names are --sleep-requests / --sleep-subtitles; the Python
            # keys are these. Getting it wrong silently disables the throttle.
            "sleep_interval_requests": s.sleep_requests_s,
            "sleep_interval_subtitles": s.sleep_subtitles_s,
            "sleep_interval": s.sleep_interval_s,
            "max_sleep_interval": s.max_sleep_interval_s,
            "retry_sleep_functions": {},
            "impersonate": None,
        }
        if s.cookiefile:
            opts["cookiefile"] = s.cookiefile
        return opts

    def _run(self, opts: dict[str, Any], url: str, *, download: bool) -> dict[str, Any]:
        import yt_dlp

        attempt = 0
        while True:
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=download)
                    if info is None:
                        raise SourceError(f"no extraction result for {url}")
                    return ydl.sanitize_info(info)
            except Exception as exc:  # yt_dlp.utils.DownloadError and friends
                message = str(exc)
                # Two retry classes, not one (audit 2026-08-10 §1). A 429 or a
                # throttling 403 is about *this request* and often clears in
                # seconds, so it earns the short inner backoff. A bot-check is
                # not: YouTube has blocked the whole logged-out session on this
                # IP, the measured waves run 60-90 minutes, and 5+10+20 seconds
                # of retries cannot outlive that — all they do is spend the
                # item's attempts and add requests to a block that hammering
                # makes longer. `--extractor-retries` is documented as retries
                # for *known extractor errors*; it was never a recovery
                # mechanism for an IP block. So the bot-check falls straight
                # through to `_classified` → `RateLimited` → `E_RATE_LIMIT`,
                # and the job-level cool-off owns the wait.
                if _is_rate_limit(message) and not _is_bot_check(message):
                    attempt += 1
                    if attempt <= self._settings.extractor_retries:
                        # exp=5:120, the shape yt-dlp's own --retry-sleep uses.
                        delay = min(
                            5 * 2 ** (attempt - 1), self._settings.worker_retry_max_wait_s
                        )
                        logger.warning(
                            "429 from YouTube; sleeping %.0fs before retry %d", delay, attempt
                        )
                        self._sleeper(delay + random.uniform(0, 1.0))
                        continue
                raise _classified(message, url) from exc

    def _download(self, opts: dict[str, Any], url: str, info: dict[str, Any] | None) -> None:
        """One media download — from the probe's extraction when we have it.

        This is finding 3 of the audit. A normal item used to pay *three* full
        YouTube extractions: the metadata probe, then a fresh ``YoutubeDL`` for
        the audio, then another for the frame source. Each one re-fetches the
        webpage and the player response, and the maintainers' own guest-session
        estimate is counted in webpage/player requests per hour — so two thirds
        of an item's budget went on asking the same question again.

        yt-dlp already supports the answer: ``--load-info-json`` replays a
        previously extracted info dict through ``process_ie_result``, which
        runs format *selection* (from ``info['formats']``) and the download,
        with no extractor call. Passing the probe's dict does the same thing in
        process, so audio and video both resolve out of one extraction. Format
        selection is per-call, so the two downloads still pick their own
        formats — the outputs are byte-identical, there are simply fewer
        requests.

        The line where re-extraction wins back: **format URLs and manifests
        expire.** Rather than guess which yt-dlp message means "stale", the
        fallback is defined by what re-extraction can possibly fix — anything
        except an explicit 429, a bot-check, and the two stable verdicts. Those
        four re-raise; everything else buys exactly one fresh extraction, which
        is precisely the code path this method replaced. So the worst case is
        today's cost plus nothing, the normal case is a third of it, and a
        genuinely throttled 403 still ends as ``RateLimited`` — from the
        re-extraction, one request later.
        """
        if info is None:
            self._run(opts, url, download=True)
            return
        import yt_dlp

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.process_ie_result(copy.deepcopy(info), download=True)
            return
        except Exception as exc:
            message = str(exc)
            if (
                _is_bot_check(message)
                or _is_too_many_requests(message)
                or _is_not_yet(message)
                or _is_unavailable(message)
            ):
                # Nothing a second extraction can mend, and during a block the
                # cheapest thing this box can do is stop asking.
                raise _classified(message, url) from exc
            self.stale_info_refreshes += 1
            logger.warning(
                "the probe's extraction no longer downloads %s (%s); re-extracting "
                "— %d stale-info refresh(es) so far this process",
                url,
                message,
                self.stale_info_refreshes,
            )
        self._run(opts, url, download=True)

    # --------------------------------------------------------------- methods

    def probe(self, url: str) -> dict[str, Any]:
        return self._run(self._base_opts(), url, download=False)

    def expand(self, url: str, kind: str, max_items: int) -> list[PlaylistEntry]:
        """One request, no per-video player calls (research §5.6)."""
        target = url
        if kind == "channel_recent" and "/playlist" not in url and "list=" not in url:
            target = url.rstrip("/")
            if not target.endswith(("/videos", "/streams", "/shorts")):
                target += "/videos"
        opts = self._base_opts() | {
            "noplaylist": False,
            "extract_flat": "in_playlist",
            "playlistend": max(1, max_items),
            "skip_download": True,
        }
        info = self._run(opts, target, download=False)
        if not is_playlist(info):
            return [
                PlaylistEntry(
                    url=str(info.get("webpage_url") or target),
                    source_id=_str_or_none(info.get("id")),
                    title=_str_or_none(info.get("title")),
                )
            ]
        return playlist_entries(info, max_items)

    def fetch_subtitle(self, track: SubtitleTrack) -> str:
        """One request for the track we chose, and no others.

        Deliberately *not* ``writesubtitles``: the download machinery fetches
        every wanted language and every wanted format, and three timedtext
        requests is where the 429 lands.
        """
        import yt_dlp

        opts = self._base_opts()
        with yt_dlp.YoutubeDL(opts) as ydl:
            self._sleeper(self._settings.sleep_subtitles_s)
            try:
                return ydl.urlopen(track.url).read().decode("utf-8", "replace")
            except Exception as exc:
                message = str(exc)
                if _is_rate_limit(message):
                    raise RateLimited(f"429 fetching the {track.lang} caption track") from exc
                raise SourceError(message) from exc

    def download_audio(
        self,
        url: str,
        source_id: str,
        dest_dir: Path,
        codec: str,
        info: dict[str, Any] | None = None,
    ) -> MediaFile:
        """bestaudio, converted once, by yt-dlp's own ffmpeg call.

        16 kHz mono is whisper-native, so the codec fork is only about what is
        kept: `opus` is the retention default (index-schema §6.1 sizes the disk
        budget on it), `wav` is the uncompressed 16 kHz PCM whisperX would
        otherwise decode for itself.

        ``info`` is the probe's extraction, when the caller still holds it; see
        `_download` for why that is the whole of finding 3.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        args = ["-ac", "1", "-ar", "16000"] if codec != "opus" else ["-ac", "1", "-b:a", "24k"]
        opts = self._base_opts() | {
            "format": "bestaudio[abr<=80]/bestaudio/best",
            "outtmpl": {"default": "%(id)s.%(ext)s"},
            "paths": {"home": str(dest_dir), "temp": str(dest_dir / "tmp")},
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": codec, "preferredquality": "0"}
            ],
            "postprocessor_args": {"extractaudio": args},
        }
        self._download(opts, url, info)
        return MediaFile(path=_first_match(dest_dir, source_id))

    def download_video(
        self,
        url: str,
        source_id: str,
        dest_dir: Path,
        max_height: int,
        info: dict[str, Any] | None = None,
    ) -> MediaFile:
        """H.264 at the height cap, video only — STT has its own copy.

        Not ``bestvideo``: 4K VP9 is 2.9 GB for one lecture and 99.9% of those
        pixels are discarded. Not 720p either: the OCR leg exists to read code
        in screencasts, and 1080p downscaled to 720p puts a 14 px editor font
        under 10 px (research §5.3).
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        height = max(240, int(max_height))
        opts = self._base_opts() | {
            "format": _video_format(height),
            "merge_output_format": "mp4",
            "outtmpl": {"default": "%(id)s.%(ext)s"},
            "paths": {"home": str(dest_dir), "temp": str(dest_dir / "tmp")},
        }
        self._download(opts, url, info)
        return MediaFile(path=_first_match(dest_dir, source_id))


def _video_format(height: int) -> str:
    """The height cap, then a ladder above it, then anything at all.

    The cap is a bandwidth decision, not a requirement: 4K VP9 is 2.9 GB for one
    lecture and 99.9% of those pixels are discarded (research §5.3). But a
    live-stream VOD or an odd upload can expose *no* format under the cap, or
    formats with no usable height metadata, and then a selector that only knows
    `height<=N` matches nothing, `download_video` raises, and — before the fetch
    split — the whole item died with it, transcript included.

    The ladder climbs in standard rungs so the *smallest* format above the cap
    wins rather than the best one, and the bare `b` at the end catches formats
    that declare no height. `merge_output_format=mp4` normalises whatever comes
    out of it into something the decoder handles.
    """
    rungs = [rung for rung in (height, 1440, 2160, 4320) if rung >= height]
    branches: list[str] = []
    for rung in rungs:
        branches += [
            f"bv*[vcodec^=avc1][height<={rung}]",
            f"bv*[height<={rung}]",
            f"b[height<={rung}]",
        ]
    branches += ["bv*", "b"]
    return "/".join(branches)


def _first_match(directory: Path, source_id: str) -> Path:
    matches = sorted(p for p in directory.glob(f"{source_id}.*") if p.is_file())
    if not matches:
        raise SourceError(f"yt-dlp reported success but wrote no file for {source_id}")
    return matches[0]


def _seconds_until_release(info: dict[str, Any]) -> int | None:
    """How long a premiere says it has left. yt-dlp gives an epoch, or nothing."""
    release = info.get("release_timestamp")
    if not isinstance(release, (int, float)):
        return None
    remaining = int(release) - int(time.time())
    return max(0, remaining) if remaining > 0 else None


def _classified(message: str, url: str) -> SourceError:
    """One yt-dlp message in, the typed error it deserves out.

    The precedence is the load-bearing part and it is the one `_run` has always
    used, with the bot-check lifted to the front so it can be told apart from
    the throttling it otherwise shares a code with.
    """
    if _is_bot_check(message):
        return RateLimited(
            f"YouTube is asking this box to confirm it is not a bot while fetching "
            f"{url}: the IP is blocked for logged-out access, so this was not "
            f"retried inside the extraction — {message}"
        )
    if _is_rate_limit(message):
        return RateLimited(f"YouTube rate-limited this box while fetching {url}: {message}")
    if _is_not_yet(message):
        return NotYetAvailable(message)
    if _is_unavailable(message):
        return Unavailable(message)
    return SourceError(message)


def _is_too_many_requests(message: str) -> bool:
    """The explicit throttle, with no 403 or bot-check ambiguity about it."""
    lowered = message.lower()
    return "429" in lowered or "too many requests" in lowered


def _is_bot_check(message: str) -> bool:
    """"Sign in to confirm you're not a bot" — the IP-level block, on its own.

    Split out of `_is_rate_limit` because the two need *different retry
    classes*, not different wording. A 429 or a throttling 403 is about this
    request and often clears in seconds, so it earns the short inner backoff.
    A bot-check is yt-dlp's pinned known issue #3766: YouTube has blocked the
    IP while the client is logged out, and no in-extraction retry clears it —
    the observed waves run 60-90 minutes. It is still `E_RATE_LIMIT` to
    everything downstream; it just skips straight there.
    """
    lowered = message.lower()
    return "confirm you're not a bot" in lowered or "confirm you’re not a bot" in lowered


def _is_rate_limit(message: str) -> bool:
    """429, and the 403 that YouTube serves for the same reason.

    Measured on the reference box (bench 2026-08-09 §6.5): two of seven runs hit
    `unable to download video data: HTTP Error 403: Forbidden` on the *media*
    download while a second agent shared the IP. It is throttling wearing a
    different status code — the same URL works again minutes later — but it
    arrived as `E_INTERNAL`, so it burned the generic retry budget instead of
    the rate-limit one and never triggered a cool-off.

    Deliberately narrow: a 403 on the *download* is throttling, while a 403 on
    an extraction is usually geo-blocking or a members-only stream, which
    `_is_unavailable` already claims and which no amount of waiting fixes.
    """
    lowered = message.lower()
    if _is_too_many_requests(message):
        return True
    # YouTube's bot-check IS throttling in its modern costume. Observed live
    # (overnight batch, wave 2, 2026-08-09 01:08): four of ten videos failed
    # with "Sign in to confirm you're not a bot" while the other six extracted
    # fine — a soft, intermittent gate that a cool-off clears and hammering
    # escalates. It was classified E_UNSUPPORTED_SOURCE (final), so no defer
    # fired and the driver kept submitting. Distinct from the age gate, which
    # _is_unavailable claims and no waiting fixes. `_run` splits it back out of
    # this predicate (`_is_bot_check`) to skip the inner retries: same typed
    # code downstream, no in-extraction backoff.
    if _is_bot_check(message):
        return True
    return "403" in lowered and any(
        marker in lowered
        for marker in ("unable to download", "fragment", "giving up after", "video data")
    )


def _is_not_yet(message: str) -> bool:
    """yt-dlp's prose for the same lifecycle states, when it errors instead of
    returning an info dict with `live_status`."""
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            "this live event will begin",
            "premieres in",
            "premiere will begin",
            "is not available yet",
            "live stream recording is not available",
        )
    )


def _is_unavailable(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            "video unavailable",
            "private video",
            "members-only",
            "has been removed",
            "is not available",
            "sign in to confirm your age",
        )
    )


@dataclass
class RecordedSource:
    """Test double base: a `Source` backed by canned info dicts.

    It lives in the source tree rather than the test tree because the drafts of
    both halves have to agree on the seam, and because `bench/` will want it.
    """

    infos: dict[str, dict[str, Any]] = field(default_factory=dict)
    subtitles: dict[str, str] = field(default_factory=dict)
    version: str = "yt-dlp-fake"
    # One entry per media download: did the caller hand back the probe's info
    # dict, or make this download pay for its own extraction? The seam's whole
    # point (audit §5), so the double records it.
    reused_info: list[bool] = field(default_factory=list)

    def probe(self, url: str) -> dict[str, Any]:
        try:
            return self.infos[url]
        except KeyError as exc:
            raise SourceError(f"no canned info dict for {url}") from exc

    def expand(self, url: str, kind: str, max_items: int) -> list[PlaylistEntry]:
        return playlist_entries(self.probe(url), max_items)

    def fetch_subtitle(self, track: SubtitleTrack) -> str:
        try:
            return self.subtitles[track.url]
        except KeyError as exc:
            raise SourceError(f"no canned subtitle for {track.url}") from exc

    def download_audio(
        self,
        url: str,
        source_id: str,
        dest_dir: Path,
        codec: str,
        info: dict[str, Any] | None = None,
    ) -> MediaFile:
        self.reused_info.append(info is not None)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{source_id}.{codec}"
        path.write_bytes(b"fake-audio")
        return MediaFile(path=path)

    def download_video(
        self,
        url: str,
        source_id: str,
        dest_dir: Path,
        max_height: int,
        info: dict[str, Any] | None = None,
    ) -> MediaFile:
        self.reused_info.append(info is not None)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{source_id}.mp4"
        path.write_bytes(b"fake-video")
        return MediaFile(path=path)
