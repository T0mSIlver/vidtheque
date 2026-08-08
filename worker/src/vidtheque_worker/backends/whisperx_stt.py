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
from typing import Any

from .base import BackendUnavailable, BaseBackend, Segment, Transcription, Word

log = logging.getLogger(__name__)


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
        self.device = device
        self.compute_type = compute_type
        self.batch_size = batch_size
        self.align_default = align
        self._model: Any = None
        self._align_cache: dict[str, tuple[Any, Any]] = {}

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
        audio = whisperx.load_audio(audio_path)
        duration = float(len(audio)) / 16000.0

        result = self._model.transcribe(
            audio, batch_size=self.batch_size, language=language
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
        import whisperx

        if language not in self._align_cache:
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
