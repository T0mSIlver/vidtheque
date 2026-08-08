"""HTTP surface, driven against fake backends through the real app."""

from __future__ import annotations

import pytest
from conftest import FakeBackend, FakeHooks, FakeVram, Recorder
from fastapi.testclient import TestClient

from vidtheque_worker.app import create_app
from vidtheque_worker.backends.base import BackendUnavailable
from vidtheque_worker.config import Settings
from vidtheque_worker.lifecycle import LifecycleManager


@pytest.fixture
def parts(recorder: Recorder, transcription, embeddings, ocr_items):
    backends = {
        "stt": FakeBackend(
            "stt",
            name="fake-stt",
            model_id="large-v3",
            vram_estimate_mb=3000,
            recorder=recorder,
            result=transcription,
        ),
        "embed": FakeBackend(
            "embed",
            name="fake-embed",
            model_id="BAAI/bge-m3",
            vram_estimate_mb=2000,
            recorder=recorder,
            result=embeddings,
        ),
        "ocr": FakeBackend(
            "ocr",
            name="fake-ocr",
            model_id="rapidocr-default",
            vram_estimate_mb=0,
            recorder=recorder,
            result=ocr_items,
        ),
    }
    manager = LifecycleManager(
        backends,
        idle_unload_seconds=0,
        vram_probe=FakeVram(backends),
        hook_runner=FakeHooks(recorder),
        idle_poll_interval=0.01,
    )
    return backends, manager


@pytest.fixture
def client(parts):
    _, manager = parts
    app = create_app(settings=Settings(_env_file=None), manager=manager)
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------
# transcriptions
# --------------------------------------------------------------------------


def test_verbose_json_carries_word_timestamps(client, parts):
    backends, _ = parts
    response = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", b"RIFFfake", "audio/wav")},
        data={"model": "whisper-1", "response_format": "verbose_json"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["task"] == "transcribe"
    assert body["language"] == "en"
    assert body["duration"] == 4.2
    assert body["model"] == "large-v3"
    assert body["backend"] == "fake-stt"
    assert [s["text"] for s in body["segments"]] == ["hello there", "general kenobi"]
    words = body["segments"][0]["words"]
    assert [w["word"] for w in words] == ["hello", "there"]
    assert words[0]["start"] == 0.0 and words[0]["end"] == 0.8

    # The upload reached the backend as a real path, and was cleaned up after.
    (args, kwargs) = backends["stt"].infer_calls[0]
    assert args[0].endswith(".wav")
    assert kwargs == {"language": None, "align": True}


def test_default_json_is_just_text(client):
    response = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.mp3", b"fake", "audio/mpeg")},
    )
    assert response.status_code == 200
    assert response.json() == {"text": "hello there general kenobi"}


def test_text_response_format(client):
    response = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.mp3", b"fake", "audio/mpeg")},
        data={"response_format": "text"},
    )
    assert response.status_code == 200
    assert response.text == "hello there general kenobi"


def test_segment_granularity_turns_alignment_off(client, parts):
    backends, _ = parts
    client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", b"fake", "audio/wav")},
        data={
            "response_format": "verbose_json",
            "timestamp_granularities[]": ["segment"],
            "language": "fr",
        },
    )
    _, kwargs = backends["stt"].infer_calls[0]
    assert kwargs == {"language": "fr", "align": False}


def test_unsupported_response_format_is_rejected(client):
    response = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", b"fake", "audio/wav")},
        data={"response_format": "srt"},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------
# embeddings
# --------------------------------------------------------------------------


def test_embeddings_accepts_a_string(client, parts):
    backends, _ = parts
    response = client.post("/v1/embeddings", json={"input": "one text"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["object"] == "list"
    assert body["model"] == "fake-embed"
    assert body["dimensions"] == 3
    assert [d["index"] for d in body["data"]] == [0, 1]
    assert body["data"][0]["embedding"] == [0.1, 0.2, 0.3]
    assert backends["embed"].infer_calls[0][0][0] == ["one text"]


def test_embeddings_accepts_a_list(client, parts):
    backends, _ = parts
    response = client.post(
        "/v1/embeddings", json={"input": ["a", "b"], "model": "BAAI/bge-m3"}
    )
    assert response.status_code == 200
    assert backends["embed"].infer_calls[0][0][0] == ["a", "b"]


def test_empty_input_is_rejected(client):
    assert client.post("/v1/embeddings", json={"input": []}).status_code == 400


def test_oversized_batch_is_rejected(client):
    response = client.post("/v1/embeddings", json={"input": ["x"] * 513})
    assert response.status_code == 413


# --------------------------------------------------------------------------
# ocr
# --------------------------------------------------------------------------


def test_ocr_returns_text_confidence_bbox(client, parts):
    backends, _ = parts
    response = client.post(
        "/v1/ocr", files={"file": ("frame.jpg", b"\xff\xd8fakejpeg", "image/jpeg")}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["backend"] == "fake-ocr"
    assert len(body["data"]) == 1
    image = body["data"][0]
    assert image["filename"] == "frame.jpg"
    assert image["items"][0] == {
        "text": "uv sync",
        "confidence": 0.93,
        "bbox": [10.0, 20.0, 110.0, 44.0],
    }
    assert backends["ocr"].infer_calls[0][0][0] == [b"\xff\xd8fakejpeg"]


def test_ocr_passes_min_confidence_through(client, parts):
    backends, _ = parts
    client.post(
        "/v1/ocr",
        files={"file": ("frame.jpg", b"data", "image/jpeg")},
        data={"min_confidence": "0.5"},
    )
    assert backends["ocr"].infer_calls[0][1] == {"min_confidence": 0.5}


def test_ocr_rejects_an_empty_upload(client):
    response = client.post("/v1/ocr", files={"file": ("frame.jpg", b"", "image/jpeg")})
    assert response.status_code == 400


# --------------------------------------------------------------------------
# status / health
# --------------------------------------------------------------------------


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_status_reports_models_vram_and_queue(client):
    client.post("/v1/embeddings", json={"input": "warm the model"})
    body = client.get("/status").json()
    by_task = {b["task"]: b for b in body["backends"]}
    assert by_task["embed"]["loaded"] is True
    assert by_task["embed"]["backend"] == "fake-embed"
    assert by_task["embed"]["model"] == "BAAI/bge-m3"
    assert by_task["stt"]["loaded"] is False
    assert body["vram"]["available"] is True
    assert body["vram"]["used_mb"] == 2000
    assert body["vram"]["free_mb"] == 22000
    assert body["queue"] == {"depth": 0, "in_flight": 0, "running": None}


# --------------------------------------------------------------------------
# error mapping
# --------------------------------------------------------------------------


def test_missing_optional_dependency_maps_to_503(parts, recorder):
    backends, manager = parts
    backends["embed"].load_error = BackendUnavailable("sentence-transformers missing")
    app = create_app(settings=Settings(_env_file=None), manager=manager)
    with TestClient(app) as c:
        response = c.post("/v1/embeddings", json={"input": "x"})
    assert response.status_code == 503
    assert response.headers["retry-after"] == "60"
    assert response.json()["error"]["type"] == "backend_unavailable"


def test_openapi_documents_the_contract(client):
    schema = client.get("/openapi.json").json()
    assert set(schema["paths"]) == {
        "/v1/audio/transcriptions",
        "/v1/embeddings",
        "/v1/ocr",
        "/status",
        "/healthz",
    }
