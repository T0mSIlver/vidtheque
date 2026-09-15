from __future__ import annotations

from pathlib import Path

from vidtheque_worker.backends.voxtral_stt import VoxtralBackend, _chunk_starts, _multipart


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
            response([("kept", 3_575.0, 3_576.0)]),
            response([("different", 5.0, 6.0)]),
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
    assert result.text == "kept different"
    assert backend.last_degraded_seams == [3_570.0]


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
