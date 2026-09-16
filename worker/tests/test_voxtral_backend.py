from __future__ import annotations

import http.client
from pathlib import Path

import pytest

from vidtheque_worker.backends.base import BackendUnavailable
from vidtheque_worker.backends.voxtral_stt import (
    VoxtralBackend,
    _chunk_starts,
    _MistralClient,
    _multipart,
)


class FakeClient:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, list[str]]] = []
        self.closed = False

    def transcribe(self, path: str, *, model: str, context_bias: list[str]) -> dict:
        self.calls.append((path, model, context_bias))
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def response(words: list[tuple[str, float, float]]) -> dict:
    return {
        "language": "en",
        "segments": [
            {"type": "transcription_word", "text": text, "start": start, "end": end}
            for text, start, end in words
        ],
    }


def test_load_and_unload_manage_only_the_http_client() -> None:
    client = FakeClient([])
    backend = VoxtralBackend(
        api_key="secret",
        client_factory=lambda _key, _url: client,
        duration_probe=lambda _path: 1.0,
    )
    assert backend.vram_estimate_mb == 0 and not backend.loaded
    backend.load()
    assert backend.loaded
    backend.unload()
    assert not backend.loaded and client.closed


def test_long_audio_is_sequentially_chunked_and_safely_merged(tmp_path: Path) -> None:
    client = FakeClient(
        [
            response(
                [
                    ("opening", 0.0, 0.4),
                    ("alpha", 3571.0, 3571.3),
                    ("beta", 3571.4, 3571.7),
                    ("gamma", 3571.8, 3572.1),
                ]
            ),
            response(
                [
                    ("alpha", 1.0, 1.3),
                    ("beta", 1.4, 1.7),
                    ("gamma", 1.8, 2.1),
                    ("closing", 2.2, 2.6),
                ]
            ),
        ]
    )
    chunks: list[tuple[float, float]] = []

    def chunker(_source: str, start: float, duration: float, destination: str) -> None:
        chunks.append((start, duration))
        Path(destination).write_bytes(b"audio")

    audio = tmp_path / "day.opus"
    audio.write_bytes(b"audio")
    backend = VoxtralBackend(
        api_key="secret",
        client_factory=lambda _key, _url: client,
        duration_probe=lambda _path: 3_630.0,
        chunker=chunker,
    )
    backend.load()
    result = backend.infer(
        str(audio), language="fr", align=True, context_bias=["Voxtral", "Mistral"]
    )

    assert chunks == [(0.0, 3_600.0), (3_570.0, 60.0)]
    assert [word.word for segment in result.segments for word in segment.words] == [
        "opening",
        "alpha",
        "beta",
        "gamma",
        "closing",
    ]
    assert [call[2] for call in client.calls] == [["Voxtral", "Mistral"]] * 2
    assert result.language == "en" and result.duration == 3_630.0
    assert backend.last_degraded_seams == []
    assert all(
        right.start >= left.end for left, right in zip(result.segments, result.segments[1:])
    )


def test_an_unsafe_seam_keeps_both_sides_and_records_it(tmp_path: Path) -> None:
    client = FakeClient(
        [
            response(
                [
                    ("opening", 0.0, 0.4),
                    ("kept", 3_575.0, 3_576.0),
                    ("tail", 3_590.0, 3_591.0),
                ]
            ),
            response([("different", 8.0, 9.0), ("closing", 25.0, 26.0)]),
        ]
    )

    def chunker(_source: str, _start: float, _duration: float, destination: str) -> None:
        Path(destination).write_bytes(b"audio")

    audio = tmp_path / "day.opus"
    audio.write_bytes(b"audio")
    backend = VoxtralBackend(
        api_key="secret",
        client_factory=lambda _key, _url: client,
        duration_probe=lambda _path: 3_630.0,
        chunker=chunker,
    )
    backend.load()
    result = backend.infer(str(audio))
    assert result.text == "opening kept different tail closing"
    assert backend.last_degraded_seams == [3_570.0]

    words = [word for segment in result.segments for word in segment.words]
    assert [word.start for word in words] == sorted(word.start for word in words)
    for segment in result.segments:
        assert all(
            segment.start <= word.start and word.end <= segment.end
            for word in segment.words
        )


def test_a_repeated_trigram_far_from_the_seam_is_not_a_match(tmp_path: Path) -> None:
    """The true overlap match failed — ffmpeg cut mid-word — and a filler
    trigram matches instead, the earlier chunk's copy 27 s after the later
    chunk's. Dropping the later prefix on that would append words starting
    before the last word kept, so the seam degrades instead."""

    client = FakeClient(
        [
            response(
                [
                    ("opening", 0.0, 0.4),
                    ("and", 3_598.0, 3_598.2),
                    ("so", 3_598.3, 3_598.5),
                    ("the", 3_598.6, 3_598.8),
                ]
            ),
            response(
                [
                    ("and", 1.0, 1.2),
                    ("so", 1.3, 1.5),
                    ("the", 1.6, 1.8),
                    ("model", 2.0, 2.4),
                    ("closing", 25.0, 25.5),
                ]
            ),
        ]
    )

    def chunker(_source: str, _start: float, _duration: float, destination: str) -> None:
        Path(destination).write_bytes(b"audio")

    audio = tmp_path / "day.opus"
    audio.write_bytes(b"audio")
    backend = VoxtralBackend(
        api_key="secret",
        client_factory=lambda _key, _url: client,
        duration_probe=lambda _path: 3_630.0,
        chunker=chunker,
    )
    backend.load()
    result = backend.infer(str(audio))

    assert backend.last_degraded_seams == [3_570.0]
    words = [word for segment in result.segments for word in segment.words]
    assert [word.start for word in words] == sorted(word.start for word in words)
    assert all(
        right.start >= left.end for left, right in zip(result.segments, result.segments[1:])
    )
    assert [word.word for word in words].count("model") == 1


def test_chunk_starts_use_a_thirty_second_overlap() -> None:
    assert _chunk_starts(10_800.0) == [0.0, 3_570.0, 7_140.0, 10_710.0]


def test_upstream_multipart_repeats_array_fields_and_omits_language(tmp_path: Path) -> None:
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF")
    body, content_type = _multipart(
        [
            ("model", "voxtral-mini-latest"),
            ("timestamp_granularities", "segment"),
            ("timestamp_granularities", "word"),
            ("context_bias", "KV cache"),
        ],
        str(audio),
    )
    assert content_type.startswith("multipart/form-data; boundary=")
    assert body.count(b'name="timestamp_granularities"') == 2
    assert b'name="context_bias"' in body and b"KV cache" in body
    assert b'name="language"' not in body


def test_a_truncated_response_body_is_the_retryable_failure(tmp_path: Path) -> None:
    """The connection drops after Mistral has already transcribed the hour. That
    is a read to retry, not a payload to reject, so it leaves as the 503 rather
    than a 500 that would settle the item as unsupported."""

    class TruncatedResponse:
        def __enter__(self) -> TruncatedResponse:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def read(self, *_args: object) -> bytes:
            raise http.client.IncompleteRead(b'{"segments": [', 4_096)

    class TruncatingOpener:
        def open(self, _request: object, timeout: float) -> TruncatedResponse:
            del timeout
            return TruncatedResponse()

    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF")
    client = _MistralClient("secret", "https://api.mistral.ai/v1")
    client._opener = TruncatingOpener()

    with pytest.raises(BackendUnavailable) as caught:
        client.transcribe(str(audio), model="voxtral-mini-latest", context_bias=[])
    assert caught.value.code == "backend_unavailable"
    assert "truncated" in str(caught.value)
    assert "secret" not in str(caught.value)
