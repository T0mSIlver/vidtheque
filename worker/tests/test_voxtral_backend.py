from __future__ import annotations

import http.client
from pathlib import Path
from unittest import mock

import pytest

from vidtheque_worker.backends import voxtral_stt
from vidtheque_worker.backends.base import BackendUnavailable
from vidtheque_worker.backends.voxtral_stt import (
    VoxtralBackend,
    _chunk_starts,
    _MistralClient,
    _multipart,
    _response_words,
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
            {"type": "transcription_segment", "text": text, "start": start, "end": end}
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
    assert backend.last_degraded_seams == [] and result.degraded_seams == []
    assert all(
        right.start >= left.end for left, right in zip(result.segments, result.segments[1:])
    )


def test_a_seam_whose_edges_differ_still_merges_without_doubling(tmp_path: Path) -> None:
    """The rehearsal's seam (2026-09-18): both chunks hear the same half minute, but each
    is cut mid-word at its own edge, so the two copies never agree end to end. The
    merge that wanted them equal kept both and doubled every word."""

    client = FakeClient(
        [
            response(
                [
                    ("opening", 0.0, 0.4),
                    ("code", 3_572.0, 3_572.3),
                    ("agent", 3_572.4, 3_572.8),
                    ("labs", 3_584.0, 3_584.3),
                    ("have", 3_584.4, 3_584.6),
                    ("really", 3_584.7, 3_585.0),
                    ("exploded", 3_585.1, 3_585.6),
                    ("rec", 3_599.6, 3_600.0),
                ]
            ),
            response(
                [
                    ("ode", 0.1, 0.3),
                    ("agent", 2.5, 2.9),
                    ("labs", 14.1, 14.4),
                    ("have", 14.5, 14.7),
                    ("really", 14.8, 15.1),
                    ("exploded.", 15.2, 15.7),
                    ("recently", 29.6, 30.2),
                    ("closing", 40.0, 40.5),
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
        duration_probe=lambda _path: 3_660.0,
        chunker=chunker,
    )
    backend.load()
    result = backend.infer(str(audio))

    assert result.degraded_seams == []
    words = [word.word for segment in result.segments for word in segment.words]
    assert words == [
        "opening", "code", "agent", "labs", "have", "really", "exploded.", "recently", "closing",
    ]  # fmt: skip
    starts = [word.start for segment in result.segments for word in segment.words]
    assert starts == sorted(starts)


def test_a_silent_hour_is_silence_not_a_failure(tmp_path: Path) -> None:
    """The rehearsal's day stream (2026-09-18) has an hour with nobody speaking. The
    API answers it with no text and no words, which failed the whole 8-hour job."""

    silent = {"language": None, "text": "", "segments": []}
    client = FakeClient(
        [
            response([("before", 10.0, 10.4), ("break", 3_590.0, 3_590.4)]),
            silent,
            response([("after", 50.0, 50.4)]),
        ]
    )

    def chunker(_source: str, _start: float, _duration: float, destination: str) -> None:
        Path(destination).write_bytes(b"audio")

    audio = tmp_path / "day.opus"
    audio.write_bytes(b"audio")
    backend = VoxtralBackend(
        api_key="secret",
        client_factory=lambda _key, _url: client,
        duration_probe=lambda _path: 10_000.0,
        chunker=chunker,
    )
    backend.load()
    result = backend.infer(str(audio))

    assert result.text == "before break after"
    assert result.degraded_seams == [], "a seam with a silent side has nothing to double"
    assert [word.start for s in result.segments for word in s.words] == [10.0, 3_590.0, 7_190.0]


def test_text_without_timed_words_is_still_refused() -> None:
    with pytest.raises(BackendUnavailable):
        _response_words({"text": "hello there", "segments": []}, offset=0.0)


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
    assert result.degraded_seams == [3_570.0], "the caller is told which seconds repeat"

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

    assert backend.last_degraded_seams == [3_570.0] == result.degraded_seams
    words = [word for segment in result.segments for word in segment.words]
    assert [word.start for word in words] == sorted(word.start for word in words)
    assert all(
        right.start >= left.end for left, right in zip(result.segments, result.segments[1:])
    )
    assert [word.word for word in words].count("model") == 1


def test_short_audio_is_still_extracted_before_it_is_uploaded(tmp_path: Path) -> None:
    """One chunk is normalised like any other, so the body on the wire is the
    mono 16 kHz FLAC and never the pipeline's own file at whatever size it is."""

    client = FakeClient([response([("hello", 0.0, 0.5)])])
    chunks: list[tuple[float, float]] = []

    def chunker(_source: str, start: float, duration: float, destination: str) -> None:
        chunks.append((start, duration))
        Path(destination).write_bytes(b"audio")

    audio = tmp_path / "talk.opus"
    audio.write_bytes(b"audio")
    backend = VoxtralBackend(
        api_key="secret",
        client_factory=lambda _key, _url: client,
        duration_probe=lambda _path: 900.0,
        chunker=chunker,
    )
    backend.load()
    backend.infer(str(audio))

    assert chunks == [(0.0, 900.0)]
    assert client.calls[0][0] != str(audio)
    assert client.calls[0][0].endswith("chunk-000.flac")


def test_a_chunk_over_the_upload_limit_never_reaches_the_paid_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A body upstream would answer with 413 is a 503 here: the 400 that a
    refusal becomes would fail the video without a retry."""

    monkeypatch.setattr(voxtral_stt, "MAX_UPLOAD_BYTES", 4)
    client = FakeClient([response([("hello", 0.0, 0.5)])])

    def chunker(_source: str, _start: float, _duration: float, destination: str) -> None:
        Path(destination).write_bytes(b"audio")

    audio = tmp_path / "talk.opus"
    audio.write_bytes(b"audio")
    backend = VoxtralBackend(
        api_key="secret",
        client_factory=lambda _key, _url: client,
        duration_probe=lambda _path: 900.0,
        chunker=chunker,
    )
    backend.load()
    with pytest.raises(BackendUnavailable) as caught:
        backend.infer(str(audio))

    assert caught.value.code == "backend_unavailable"
    assert "4-byte upload limit" in str(caught.value)
    assert client.calls == []


def test_the_extraction_downmixes_to_one_channel_at_sixteen_kilohertz() -> None:
    recorded: list[list[str]] = []

    class Completed:
        returncode = 0

    def fake_run(command: list[str], **_kwargs: object) -> Completed:
        recorded.append(command)
        return Completed()

    with mock.patch.object(voxtral_stt.subprocess, "run", fake_run):
        voxtral_stt._extract_chunk("in.opus", 12.0, 30.0, "out.flac")

    assert recorded[0][recorded[0].index("-ac") + 1] == "1"
    assert recorded[0][recorded[0].index("-ar") + 1] == "16000"


def test_a_response_carrying_both_word_shapes_transcribes_each_word_once() -> None:
    """Some responses repeat the words under `segments[]` as well as at the top
    level. They are the same speech described twice, not two halves of it."""

    payload = {
        "words": [
            {"word": "one", "start": 0.0, "end": 0.4},
            {"word": "two", "start": 0.5, "end": 0.9},
        ],
        "segments": [
            {
                "start": 0.0,
                "end": 0.9,
                "text": "one two",
                "words": [
                    {"word": "one", "start": 0.0, "end": 0.4},
                    {"word": "two", "start": 0.5, "end": 0.9},
                ],
            }
        ],
    }
    words = _response_words(payload, offset=10.0)
    assert [word.word for word in words] == ["one", "two"]
    assert [word.start for word in words] == [10.0, 10.5]


def test_chunk_starts_use_a_thirty_second_overlap() -> None:
    assert _chunk_starts(10_800.0) == [0.0, 3_570.0, 7_140.0, 10_710.0]


def test_upstream_multipart_repeats_array_fields_and_omits_language(tmp_path: Path) -> None:
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF")
    body, content_type = _multipart(
        [
            ("model", "voxtral-mini-latest"),
            ("timestamp_granularities", "word"),
            ("context_bias", "KV_cache"),
            ("context_bias", "vLLM"),
        ],
        str(audio),
    )
    assert content_type.startswith("multipart/form-data; boundary=")
    assert body.count(b'name="context_bias"') == 2 and b"KV_cache" in body
    assert b'name="language"' not in body


class _RecordingOpener:
    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.body = b""

    def open(self, request: object, timeout: float) -> object:
        del timeout
        self.body = request.data  # type: ignore[attr-defined]
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_the_request_is_the_one_the_live_api_accepts(tmp_path: Path) -> None:
    """Checked against the API on 2026-09-18: a second granularity is a 422, and a
    bias term with a space is a 400, so a phrase goes out with underscores."""
    import io

    class Answer(io.BytesIO):
        def __enter__(self) -> Answer:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF")
    client = _MistralClient("secret", "https://api.mistral.ai/v1")
    opener = _RecordingOpener(Answer(b'{"segments": []}'))
    client._opener = opener
    client.transcribe(
        str(audio),
        model="voxtral-mini-latest",
        context_bias=["KV cache", "Clemens  Rawert", "vLLM", "KV_cache", "a,b"],
    )
    body = opener.body
    assert body.count(b'name="timestamp_granularities"') == 1
    assert b"\r\n\r\nword\r\n" in body and b"\r\n\r\nsegment\r\n" not in body
    assert body.count(b'name="context_bias"') == 4
    for term in (b"KV_cache", b"Clemens_Rawert", b"vLLM", b"a_b"):
        assert b"\r\n\r\n" + term + b"\r\n" in body
    assert b"KV cache" not in body


def test_a_refused_request_says_why(tmp_path: Path) -> None:
    import io
    import urllib.error

    from vidtheque_worker.backends.base import InvalidMediaError

    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF")
    client = _MistralClient("secret", "https://api.mistral.ai/v1")
    refusal = urllib.error.HTTPError(
        "https://api.mistral.ai/v1/audio/transcriptions",
        422,
        "Unprocessable",
        {},  # type: ignore[arg-type]
        io.BytesIO(b'{"message": "List should have at most 1 item"}'),
    )
    client._opener = _RecordingOpener(refusal)
    with pytest.raises(InvalidMediaError) as caught:
        client.transcribe(str(audio), model="voxtral-mini-latest", context_bias=[])
    assert "at most 1 item" in str(caught.value)
    assert "secret" not in str(caught.value)


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
