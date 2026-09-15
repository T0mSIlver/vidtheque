"""Mistral Voxtral speech-to-text backend."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import subprocess
import tempfile
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .base import BackendUnavailable, BaseBackend, InvalidMediaError, Segment, Transcription, Word

log = logging.getLogger(__name__)

MAX_CHUNK_SECONDS = 3_600.0
CHUNK_OVERLAP_SECONDS = 30.0
MIN_SAFE_MATCH_WORDS = 3


class _MistralClient:
    def __init__(self, api_key: str, base_url: str, *, timeout_s: float = 3_900.0) -> None:
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/audio/transcriptions"
        self._timeout_s = timeout_s
        self._opener = urllib.request.build_opener()

    def close(self) -> None:
        self._opener = urllib.request.build_opener()

    def transcribe(self, audio_path: str, *, model: str, context_bias: Sequence[str]) -> dict:
        fields: list[tuple[str, str]] = [
            ("model", model),
            ("diarize", "false"),
            ("timestamp_granularities", "segment"),
            ("timestamp_granularities", "word"),
        ]
        fields.extend(("context_bias", term) for term in context_bias)
        body, content_type = _multipart(fields, audio_path)
        request = urllib.request.Request(
            self._url,
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": content_type,
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout_s) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in {400, 413, 415, 422}:
                raise InvalidMediaError(
                    f"Mistral refused the audio with HTTP {exc.code}"
                ) from exc
            raise BackendUnavailable(f"Mistral transcription failed with HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BackendUnavailable(f"Mistral transcription request failed: {exc}") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BackendUnavailable("Mistral transcription response was not JSON") from exc
        if not isinstance(payload, dict):
            raise BackendUnavailable("Mistral transcription response was not an object")
        return payload


def _multipart(fields: Sequence[tuple[str, str]], audio_path: str) -> tuple[bytes, str]:
    boundary = f"vidtheque-{uuid.uuid4().hex}"
    out = bytearray()

    def line(value: str) -> None:
        out.extend(value.encode("utf-8"))
        out.extend(b"\r\n")

    for name, value in fields:
        line(f"--{boundary}")
        line(f'Content-Disposition: form-data; name="{name}"')
        line("")
        line(value)

    filename = Path(audio_path).name.replace('"', "")
    line(f"--{boundary}")
    line(f'Content-Disposition: form-data; name="file"; filename="{filename}"')
    line(f"Content-Type: {mimetypes.guess_type(filename)[0] or 'application/octet-stream'}")
    line("")
    with open(audio_path, "rb") as handle:
        out.extend(handle.read())
    out.extend(b"\r\n")
    line(f"--{boundary}--")
    return bytes(out), f"multipart/form-data; boundary={boundary}"


class VoxtralBackend(BaseBackend):
    """API-backed STT with model word timestamps.

    ``align=True`` is accepted as a no-op. Voxtral's word timestamps already
    provide the alignment, so no second alignment model runs.
    """

    name = "voxtral"
    task = "stt"
    default_vram_mb = 0

    def __init__(
        self,
        model_id: str = "voxtral-mini-latest",
        *,
        api_key: str,
        base_url: str = "https://api.mistral.ai/v1",
        client_factory: Callable[[str, str], Any] | None = None,
        duration_probe: Callable[[str], float] | None = None,
        chunker: Callable[[str, float, float, str], None] | None = None,
    ) -> None:
        super().__init__(model_id, vram_estimate_mb=0)
        self._api_key = api_key
        self._base_url = base_url
        self._client_factory = client_factory or (
            lambda key, url: _MistralClient(key, url)
        )
        self._duration_probe = duration_probe or _duration_seconds
        self._chunker = chunker or _extract_chunk
        self._client: Any | None = None
        self.last_degraded_seams: list[float] = []

    def _load(self) -> None:
        self._client = self._client_factory(self._api_key, self._base_url)

    def _unload(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    def infer(
        self,
        audio_path: str,
        *,
        language: str | None = None,
        align: bool = True,
        context_bias: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> Transcription:
        del language, align, kwargs
        if self._client is None:
            raise BackendUnavailable("Voxtral HTTP client is not loaded")
        try:
            duration = float(self._duration_probe(audio_path))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise InvalidMediaError(f"could not read audio duration: {exc}") from exc
        if duration <= 0:
            raise InvalidMediaError("audio duration must be positive")

        starts = _chunk_starts(duration)
        merged: list[Word] = []
        language_seen: str | None = None
        self.last_degraded_seams = []
        with tempfile.TemporaryDirectory(prefix="vidtheque-voxtral-") as scratch:
            for index, start in enumerate(starts):
                length = min(MAX_CHUNK_SECONDS, duration - start)
                if len(starts) == 1:
                    chunk_path = audio_path
                else:
                    chunk_path = os.path.join(scratch, f"chunk-{index:03d}.flac")
                    self._chunker(audio_path, start, length, chunk_path)
                payload = self._client.transcribe(
                    chunk_path,
                    model=self.model_id,
                    context_bias=list(context_bias or ()),
                )
                language_seen = language_seen or _optional_string(payload.get("language"))
                words = _response_words(payload, offset=start)
                if index:
                    matched = _merge_at_seam(merged, words, start)
                    if not matched:
                        self.last_degraded_seams.append(start)
                        log.warning(
                            "Voxtral seam at %.3fs had no safe %d-word match; keeping both sides",
                            start,
                            MIN_SAFE_MATCH_WORDS,
                        )
                else:
                    merged.extend(words)

        segments = _segments_from_words(merged)
        return Transcription(
            text=_join_words(word.word for word in merged),
            language=language_seen,
            duration=duration,
            segments=segments,
        )


def _duration_seconds(audio_path: str) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def _extract_chunk(source: str, start: float, duration: float, destination: str) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            source,
            "-vn",
            "-c:a",
            "flac",
            destination,
        ],
        check=True,
        capture_output=True,
    )


def _chunk_starts(duration: float) -> list[float]:
    if duration <= MAX_CHUNK_SECONDS:
        return [0.0]
    stride = MAX_CHUNK_SECONDS - CHUNK_OVERLAP_SECONDS
    starts: list[float] = []
    start = 0.0
    while start < duration:
        starts.append(start)
        if start + MAX_CHUNK_SECONDS >= duration:
            break
        start += stride
    return starts


def _response_words(payload: Mapping[str, Any], *, offset: float) -> list[Word]:
    candidates: list[Mapping[str, Any]] = []
    top_words = payload.get("words")
    if isinstance(top_words, list):
        candidates.extend(item for item in top_words if isinstance(item, Mapping))

    raw_segments = payload.get("segments")
    if isinstance(raw_segments, list):
        for segment in raw_segments:
            if not isinstance(segment, Mapping):
                continue
            nested = segment.get("words")
            if isinstance(nested, list):
                candidates.extend(item for item in nested if isinstance(item, Mapping))
                continue
            kind = str(segment.get("type") or "").casefold()
            if "word" in kind or "word" in segment:
                candidates.append(segment)

    words: list[Word] = []
    for item in candidates:
        source = item.get("word", item.get("text"))
        if not isinstance(source, str) or not source.strip():
            continue
        try:
            start = float(item["start"]) + offset
            end = float(item["end"]) + offset
        except (KeyError, TypeError, ValueError):
            continue
        if end < start:
            continue
        score = item.get("score", item.get("confidence"))
        words.append(
            Word(
                word=source.strip(),
                start=start,
                end=end,
                score=float(score) if isinstance(score, (int, float)) else None,
            )
        )
    if not words:
        raise BackendUnavailable("Mistral returned no word timestamps")
    return words


def _normalized_word(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"(^\W+|\W+$)", "", value, flags=re.UNICODE)


def _merge_at_seam(earlier: list[Word], later: list[Word], seam: float) -> bool:
    overlap_end = seam + CHUNK_OVERLAP_SECONDS
    left = [word for word in earlier if (word.end or 0.0) >= seam]
    right = [word for word in later if (word.start or overlap_end) <= overlap_end]
    left_keys = [_normalized_word(word.word) for word in left]
    right_keys = [_normalized_word(word.word) for word in right]
    limit = min(len(left_keys), len(right_keys))
    matched = 0
    for size in range(limit, MIN_SAFE_MATCH_WORDS - 1, -1):
        if left_keys[-size:] == right_keys[:size] and all(left_keys[-size:]):
            matched = size
            break
    if not matched:
        earlier.extend(later)
        return False
    earlier.extend(later[matched:])
    return True


def _segments_from_words(words: Sequence[Word]) -> list[Segment]:
    if not words:
        return []
    groups: list[list[Word]] = []
    group: list[Word] = []
    group_start = words[0].start or 0.0
    for word in words:
        start = word.start if word.start is not None else group_start
        if group and (start - group_start >= 30.0 or len(group) >= 100):
            groups.append(group)
            group = []
            group_start = start
        group.append(word)
    if group:
        groups.append(group)

    segments: list[Segment] = []
    previous_end = 0.0
    for index, items in enumerate(groups):
        raw_start = items[0].start if items[0].start is not None else previous_end
        raw_end = items[-1].end if items[-1].end is not None else raw_start
        start = max(previous_end, raw_start)
        end = max(start, raw_end)
        segments.append(
            Segment(
                id=index,
                start=start,
                end=end,
                text=_join_words(word.word for word in items),
                words=list(items),
            )
        )
        previous_end = end
    return segments


def _join_words(values: Sequence[str] | Any) -> str:
    text = " ".join(values)
    return re.sub(r"\s+([,.;:!?])", r"\1", text).strip()


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
