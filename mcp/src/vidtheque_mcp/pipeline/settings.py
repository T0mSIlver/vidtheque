"""Every knob the indexing pipeline reads, resolved from the environment once.

Same rule as ``config.py``: an env var without an entry in
``deploy/.env.example`` is a bug. Same two-spelling convention too, which is why
this reuses that module's readers rather than reaching for ``os.environ``.

Defaults are the research doc's measured recommendations, not guesses:
1080p H.264 for the frame source (§5.3 — 720p is where a 14 px editor font
falls under 10 px and PP-OCR recall goes with it), the `-t sleep` preset as the
yt-dlp floor (§5.5 — a *single* invocation earned a 429 on its third subtitle
request from a cold residential IP), phash at 16/24 (§4.4), and the screencast
`ContentDetector` weights (§4.2).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import ConfigError, _bool_env, _env, _float_env, _int_env

# `originals` keeps the mp4; `audio` (the default, DECISIONS.md #3) keeps only
# the extracted audio, which is what makes an STT re-run free; `none` keeps
# neither and every reindex re-downloads.
KEEP_SOURCE = ("none", "audio", "originals")

# The research doc's `auto|always|never` under names that say which way they
# lean. DECISIONS.md picks `prefer_whisperx`.
STT_POLICIES = (
    "prefer_whisperx",  # whisperX when the worker answers, captions when it does not
    "whisperx_only",  # never index a caption track; fail instead
    "prefer_captions",  # captions when they exist, whisperX only when they do not
    "captions_only",  # zero-GPU install path
)

AUDIO_CODECS = ("opus", "wav", "flac")
DETECTORS = ("screencast", "talking_head")


@dataclass(frozen=True)
class PipelineSettings:
    """The indexing half's configuration. Immutable, validated at boot."""

    # ---------------------------------------------------------------- retention
    keep_source: str = "audio"
    keep_word_timings: bool = True

    # ------------------------------------------------------------------- media
    audio_codec: str = "opus"
    max_height: int = 1080

    # --------------------------------------------------------------- transcript
    stt_policy: str = "prefer_whisperx"
    subtitle_langs: tuple[str, ...] = ("en",)

    # ---------------------------------------------------------------- keyframes
    detector: str = "screencast"
    keyframe_max_width: int = 1280
    keyframe_quality: int = 92
    max_keyframes: int = 600
    max_shot_seconds: float = 25.0
    candidates_per_shot: int = 9
    phash_threshold: int = 24

    # -------------------------------------------------------------- worker load
    ocr_batch: int = 8
    ocr_min_confidence: float = 0.5
    embed_batch: int = 32
    frame_embed_batch: int = 8
    frame_embed_max_patches: int | None = None
    worker_timeout_s: float = 120.0
    stt_timeout_s: float = 1800.0
    worker_retries: int = 3
    worker_retry_max_wait_s: float = 120.0
    # Backpressure (503 + Retry-After) is the worker saying "not yet", not
    # failing. It is bounded by wall clock rather than by request count:
    # four requests at Retry-After 30 is 90 seconds, which is not the
    # "an indexing job has hours" the retry loop was written for.
    worker_retry_total_s: float = 1800.0
    # `stt_timeout_s` is a floor; a recording longer than it gets this many
    # seconds of budget per second of audio. A read timeout is no longer
    # replayed (the worker may still be transcribing), so the budget has to
    # be right rather than recoverable.
    stt_realtime_factor: float = 2.0

    # ------------------------------------------------------------- yt-dlp manners
    sleep_requests_s: float = 0.75
    sleep_interval_s: float = 10.0
    max_sleep_interval_s: float = 20.0
    sleep_subtitles_s: float = 5.0
    between_videos_s: float = 30.0
    extractor_retries: int = 3
    player_client: str | None = None
    cookiefile: str | None = None
    jitless: bool = True

    @classmethod
    def from_env(cls) -> "PipelineSettings":
        langs = tuple(
            lang.strip()
            for lang in (_env("VIDTHEQUE_SUBTITLE_LANGS", "en") or "en").split(",")
            if lang.strip()
        )
        patches = _int_env("VIDTHEQUE_FRAME_EMBED_MAX_PATCHES", 0)
        settings = cls(
            keep_source=(_env("VIDTHEQUE_KEEP_SOURCE", "audio") or "audio").strip().lower(),
            keep_word_timings=_bool_env("VIDTHEQUE_KEEP_WORD_TIMINGS", True),
            audio_codec=(_env("VIDTHEQUE_AUDIO_CODEC", "opus") or "opus").strip().lower(),
            max_height=_int_env("VIDTHEQUE_INDEX_MAX_HEIGHT", 1080),
            stt_policy=(_env("VIDTHEQUE_STT_POLICY", "prefer_whisperx") or "prefer_whisperx")
            .strip()
            .lower(),
            # Never more than two: enumerating languages is what earns the 429.
            subtitle_langs=langs[:2] or ("en",),
            detector=(_env("VIDTHEQUE_KEYFRAME_DETECTOR", "screencast") or "screencast")
            .strip()
            .lower(),
            keyframe_max_width=_int_env("VIDTHEQUE_KEYFRAME_MAX_WIDTH", 1280),
            keyframe_quality=_int_env("VIDTHEQUE_KEYFRAME_JPEG_QUALITY", 92),
            max_keyframes=_int_env("VIDTHEQUE_MAX_KEYFRAMES", 600),
            max_shot_seconds=_float_env("VIDTHEQUE_MAX_SHOT_SECONDS", 25.0),
            candidates_per_shot=_int_env("VIDTHEQUE_SHOT_CANDIDATES", 9),
            phash_threshold=_int_env("VIDTHEQUE_PHASH_THRESHOLD", 24),
            ocr_batch=_int_env("VIDTHEQUE_OCR_BATCH", 8),
            ocr_min_confidence=_float_env("VIDTHEQUE_OCR_MIN_CONFIDENCE", 0.5),
            embed_batch=_int_env("VIDTHEQUE_EMBED_BATCH", 32),
            frame_embed_batch=_int_env("VIDTHEQUE_FRAME_EMBED_BATCH", 8),
            frame_embed_max_patches=patches or None,
            worker_timeout_s=_float_env("VIDTHEQUE_WORKER_TIMEOUT_S", 120.0),
            stt_timeout_s=_float_env("VIDTHEQUE_STT_TIMEOUT_S", 1800.0),
            worker_retries=_int_env("VIDTHEQUE_WORKER_RETRIES", 3),
            worker_retry_max_wait_s=_float_env("VIDTHEQUE_WORKER_RETRY_MAX_WAIT_S", 120.0),
            worker_retry_total_s=_float_env("VIDTHEQUE_WORKER_RETRY_TOTAL_S", 1800.0),
            stt_realtime_factor=_float_env("VIDTHEQUE_STT_REALTIME_FACTOR", 2.0),
            sleep_requests_s=_float_env("VIDTHEQUE_YTDLP_SLEEP_REQUESTS", 0.75),
            sleep_interval_s=_float_env("VIDTHEQUE_YTDLP_SLEEP_INTERVAL", 10.0),
            max_sleep_interval_s=_float_env("VIDTHEQUE_YTDLP_MAX_SLEEP_INTERVAL", 20.0),
            sleep_subtitles_s=_float_env("VIDTHEQUE_YTDLP_SLEEP_SUBTITLES", 5.0),
            between_videos_s=_float_env("VIDTHEQUE_YTDLP_BETWEEN_VIDEOS_S", 30.0),
            extractor_retries=_int_env("VIDTHEQUE_YTDLP_EXTRACTOR_RETRIES", 3),
            player_client=_env("VIDTHEQUE_YTDLP_PLAYER_CLIENT"),
            cookiefile=_env("VIDTHEQUE_YTDLP_COOKIEFILE"),
            jitless=_bool_env("VIDTHEQUE_YTDLP_JITLESS", True),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        for value, allowed, name in (
            (self.keep_source, KEEP_SOURCE, "VIDTHEQUE_KEEP_SOURCE"),
            (self.stt_policy, STT_POLICIES, "VIDTHEQUE_STT_POLICY"),
            (self.audio_codec, AUDIO_CODECS, "VIDTHEQUE_AUDIO_CODEC"),
            (self.detector, DETECTORS, "VIDTHEQUE_KEYFRAME_DETECTOR"),
        ):
            if value not in allowed:
                raise ConfigError(f"{name} must be one of {'|'.join(allowed)}, got {value!r}")

    # ------------------------------------------------------------------ derived

    @property
    def wants_whisperx(self) -> bool:
        return self.stt_policy in ("prefer_whisperx", "whisperx_only", "prefer_captions")

    @property
    def captions_allowed(self) -> bool:
        return self.stt_policy != "whisperx_only"

    @property
    def captions_first(self) -> bool:
        return self.stt_policy in ("prefer_captions", "captions_only")
