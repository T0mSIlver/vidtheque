"""whisperX speech-to-text backend.

Chosen for word-level forced alignment: the corpus cites timestamps, and
segment-level timestamps drift enough to land a deep link on the wrong
sentence. Falls back to CPU with ``compute_type=int8`` when CUDA is absent,
which is slow but keeps the endpoint honest on a laptop or in CI.

The ``whisperx`` import is guarded and happens inside :meth:`_load`, so the app
imports fine without the ``gpu`` extra installed.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

from .base import (
    BackendUnavailable,
    BaseBackend,
    InvalidMediaError,
    Segment,
    Transcription,
    Word,
    looks_like_device_failure,
)

log = logging.getLogger(__name__)


def normalize_language(tag: str | None) -> str | None:
    """BCP-47-ish tag → whisper's bare code: ``en-US`` → ``en``, ``pt_BR`` → ``pt``.

    YouTube metadata reports region-qualified tags; faster-whisper's tokenizer
    hard-rejects them (measured live: ``ValueError: 'en-US' is not a valid
    language code``, which knocked the whole STT stage over to the caption
    fallback). Returns ``None`` (= auto-detect) for anything that doesn't
    reduce to a plausible primary subtag — auto-detect beats a crash.
    """
    if not tag:
        return None
    primary = tag.replace("_", "-").split("-", 1)[0].strip().lower()
    return primary if 2 <= len(primary) <= 3 and primary.isalpha() else None


BASELINE_BATCH = 16
"""The batch size ``default_vram_mb`` was measured at."""

PER_BATCH_VRAM_MB = 216
"""Slope through the two measured points, 5352 MB at batch 4 and 7941 at 16
(research/gpu-validation-2026-08-08.md). An extrapolation, and labelled one."""

MAX_ALIGN_MODELS = 3
"""Alignment models kept warm. Each is a Wav2Vec2 checkpoint of a few hundred
MB that admission control never sees — the estimate covers the *default* one
only — so an unbounded cache is a VRAM leak with the length of the corpus's
language list. Three keeps a bilingual channel from reloading on every video."""


def estimate_vram_mb(batch_size: int, *, base: int = 8000) -> int:
    """Admission estimate for a configured STT batch size.

    Upward only, from the measured 16 figure: a smaller batch keeps the
    measured number rather than trusting a straight line drawn through two
    points to hold at the bottom of the range. Raising ``STT_BATCH_SIZE`` used
    to leave admission believing 8000 MB — documented as such in
    ``.env.example``, which is not the same as safe.
    """
    if batch_size <= BASELINE_BATCH:
        return base
    return base + PER_BATCH_VRAM_MB * (batch_size - BASELINE_BATCH)


class WhisperXBackend(BaseBackend):
    name = "whisperx"
    task = "stt"
    # Measured on an RTX 3090 (research/gpu-validation-2026-08-08.md): 4993 MB
    # resident with the alignment model warm, 7941 MB *peak* during inference at
    # STT_BATCH_SIZE=16 (5352 MB at batch 4). Admission control has to gate on
    # the peak — a load admitted against the resident figure OOMs mid-job — so
    # this is the peak plus a little, not the weights.
    default_vram_mb = 8000

    def __init__(
        self,
        model_id: str = "large-v3",
        *,
        device: str = "cpu",
        compute_type: str = "int8",
        batch_size: int = 16,
        align: bool = True,
        vram_estimate_mb: int | None = None,
    ) -> None:
        super().__init__(model_id, vram_estimate_mb=vram_estimate_mb)
        if vram_estimate_mb is None:
            self._vram_estimate_mb = estimate_vram_mb(
                batch_size, base=self.default_vram_mb
            )
        self.device = device
        self.compute_type = compute_type
        self.batch_size = batch_size
        self.align_default = align
        self._model: Any = None
        self._align_cache: OrderedDict[str, tuple[Any, Any]] = OrderedDict()

    # -- lifecycle ---------------------------------------------------------
    def _load(self) -> None:
        try:
            import whisperx
        except ImportError as exc:  # pragma: no cover - depends on gpu extra
            raise BackendUnavailable(
                "whisperx is not installed — install the worker's 'gpu' extra"
            ) from exc

        log.info(
            "loading whisperx model=%s device=%s compute_type=%s",
            self.model_id,
            self.device,
            self.compute_type,
        )
        self._model = whisperx.load_model(
            self.model_id, self.device, compute_type=self.compute_type
        )

    def _unload(self) -> None:
        self._model = None
        self._align_cache.clear()
        self._empty_cuda_cache()

    # -- inference ---------------------------------------------------------
    def infer(
        self,
        audio_path: str,
        *,
        language: str | None = None,
        align: bool | None = None,
        **kwargs: Any,
    ) -> Transcription:
        import whisperx  # already proven importable by _load()

        if self._model is None:
            raise BackendUnavailable("whisperx model is not loaded")

        do_align = self.align_default if align is None else align
        language = normalize_language(language)
        try:
            audio = whisperx.load_audio(audio_path)
        except Exception as exc:
            if looks_like_device_failure(exc):
                raise
            # ffmpeg refusing the container is the upload's problem, and it
            # raises a plain RuntimeError. Untyped, that used to unload the
            # model (5.8 s) and answer 503, which the client reads as
            # backpressure — so it replayed the same unreadable file until its
            # 1800 s budget was gone.
            raise InvalidMediaError(f"the audio could not be decoded: {exc}") from exc
        duration = float(len(audio)) / 16000.0

        try:
            result = self._model.transcribe(
                audio, batch_size=self.batch_size, language=language
            )
        except ValueError:
            if language is None:
                raise
            # A tag that survived normalization but whisper still rejects
            # (exotic subtag, list drift). Auto-detect beats failing the stage.
            log.warning("whisper rejected language=%r; retrying with auto-detect", language)
            language = None
            result = self._model.transcribe(
                audio, batch_size=self.batch_size, language=None
            )
        detected = result.get("language") or language

        if do_align and detected:
            try:
                model_a, metadata = self._alignment_model(detected)
                result = whisperx.align(
                    result["segments"],
                    model_a,
                    metadata,
                    audio,
                    self.device,
                    return_char_alignments=False,
                )
            except Exception:  # alignment is a bonus, not a hard requirement
                log.warning("alignment failed for language=%s; segment timestamps only",
                            detected, exc_info=True)

        return _to_transcription(result.get("segments", []), detected, duration)

    def _alignment_model(self, language: str) -> tuple[Any, Any]:
        """One alignment model per language, at most :data:`MAX_ALIGN_MODELS`.

        The cache used to grow for the life of the process. Every entry is a
        few hundred MB of device that admission control cannot see — the 8000
        MB estimate is measured with the *default* alignment model warm — so a
        polyglot corpus quietly ate the headroom until a transcription OOMed
        with no evictable candidate on the card (every model there is the
        resident STT slot). Eviction is oldest-first: a video is one language,
        and a channel is rarely more than two or three.
        """
        import whisperx

        if language in self._align_cache:
            self._align_cache[language] = self._align_cache.pop(language)  # refresh
            return self._align_cache[language]

        while len(self._align_cache) >= MAX_ALIGN_MODELS:
            evicted, _ = self._align_cache.popitem(last=False)
            log.info("evicting the %s alignment model (cache is full)", evicted)
            self._empty_cuda_cache()

        self._align_cache[language] = whisperx.load_align_model(
            language_code=language, device=self.device
        )
        return self._align_cache[language]


def _to_transcription(
    raw_segments: list[dict[str, Any]], language: str | None, duration: float | None
) -> Transcription:
    segments: list[Segment] = []
    for index, seg in enumerate(raw_segments):
        words = [
            Word(
                word=str(w.get("word", "")),
                start=_maybe_float(w.get("start")),
                end=_maybe_float(w.get("end")),
                score=_maybe_float(w.get("score")),
            )
            for w in seg.get("words", []) or []
        ]
        segments.append(
            Segment(
                id=index,
                start=float(seg.get("start", 0.0) or 0.0),
                end=float(seg.get("end", 0.0) or 0.0),
                text=str(seg.get("text", "")).strip(),
                words=words,
            )
        )
    text = " ".join(s.text for s in segments).strip()
    return Transcription(text=text, language=language, duration=duration, segments=segments)


def _maybe_float(value: Any) -> float | None:
    return None if value is None else float(value)
