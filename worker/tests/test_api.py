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
def parts(
    recorder: Recorder,
    transcription,
    embeddings,
    image_embeddings,
    frame_query_embeddings,
    ocr_items,
):
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
            model_id="Qwen/Qwen3-Embedding-0.6B",
            vram_estimate_mb=2000,
            recorder=recorder,
            result=embeddings,
        ),
        "image_embed": FakeBackend(
            "image_embed",
            name="fake-frame-embed",
            model_id="google/siglip2-so400m-patch16-naflex",
            vram_estimate_mb=5000,
            recorder=recorder,
            result=image_embeddings,
            text_result=frame_query_embeddings,
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


def test_embeddings_default_to_the_document_side(client, parts):
    backends, _ = parts
    client.post("/v1/embeddings", json={"input": "indexed text"})
    assert backends["embed"].infer_calls[0][1] == {"input_type": "document"}


def test_embeddings_pass_the_query_side_through(client, parts):
    backends, _ = parts
    response = client.post(
        "/v1/embeddings", json={"input": "how do I lease the gpu", "input_type": "query"}
    )
    assert response.status_code == 200
    assert backends["embed"].infer_calls[0][1] == {"input_type": "query"}


def test_unknown_input_type_is_rejected(client):
    response = client.post(
        "/v1/embeddings", json={"input": "x", "input_type": "passage"}
    )
    assert response.status_code == 422


def test_empty_input_is_rejected(client):
    assert client.post("/v1/embeddings", json={"input": []}).status_code == 400


def test_oversized_batch_is_rejected(client):
    response = client.post("/v1/embeddings", json={"input": ["x"] * 513})
    assert response.status_code == 413


# --------------------------------------------------------------------------
# image embeddings
# --------------------------------------------------------------------------


def _jpegs(count: int) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [
        ("file", (f"frame_{i}.jpg", b"\xff\xd8fakejpeg%d" % i, "image/jpeg"))
        for i in range(count)
    ]


def test_image_embeddings_return_one_vector_per_image(client, parts):
    backends, _ = parts
    response = client.post("/v1/embeddings/image", files=_jpegs(2))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["object"] == "list"
    assert body["model"] == "fake-frame-embed"
    # The frame space, not the transcript one.
    assert body["dimensions"] == 4
    assert [d["index"] for d in body["data"]] == [0, 1]
    assert body["data"][0]["embedding"] == [0.1, 0.2, 0.3, 0.4]
    # Upload order, as raw bytes, in a single batched call.
    assert backends["image_embed"].infer_calls[0][0][0] == [
        b"\xff\xd8fakejpeg0",
        b"\xff\xd8fakejpeg1",
    ]
    assert backends["image_embed"].infer_calls[0][1] == {}


def test_image_embeddings_do_not_touch_the_text_embedder(client, parts):
    backends, _ = parts
    client.post("/v1/embeddings/image", files=_jpegs(1))
    assert backends["image_embed"].loaded
    assert not backends["embed"].loaded
    assert backends["embed"].infer_calls == []


def test_image_embeddings_pass_the_patch_budget_through(client, parts):
    backends, _ = parts
    response = client.post(
        "/v1/embeddings/image", files=_jpegs(1), data={"max_num_patches": "1024"}
    )
    assert response.status_code == 200
    assert backends["image_embed"].infer_calls[0][1] == {"max_num_patches": 1024}


def test_image_embeddings_reject_an_absurd_patch_budget(client):
    response = client.post(
        "/v1/embeddings/image", files=_jpegs(1), data={"max_num_patches": "99999"}
    )
    assert response.status_code == 400


def test_image_embeddings_reject_an_empty_upload(client):
    response = client.post(
        "/v1/embeddings/image", files={"file": ("frame.jpg", b"", "image/jpeg")}
    )
    assert response.status_code == 400


def test_image_embeddings_reject_an_oversized_batch(client):
    response = client.post("/v1/embeddings/image", files=_jpegs(65))
    assert response.status_code == 413


def test_image_embed_missing_dependency_maps_to_503(parts, recorder):
    backends, manager = parts
    backends["image_embed"].load_error = BackendUnavailable("transformers missing")
    app = create_app(settings=Settings(_env_file=None), manager=manager)
    with TestClient(app) as c:
        response = c.post("/v1/embeddings/image", files=_jpegs(1))
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "backend_unavailable"


# --------------------------------------------------------------------------
# frame queries (the frame model's text tower)
# --------------------------------------------------------------------------


def test_frame_query_answers_in_the_frame_space(client, parts):
    backends, _ = parts
    response = client.post(
        "/v1/embeddings/frame-query", json={"input": "a terminal with a stack trace"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model"] == "fake-frame-embed"
    # 4, the frame width — not 3, the transcript one.
    assert body["dimensions"] == 4
    assert body["data"][0]["embedding"] == [0.9, 0.8, 0.7, 0.6]
    # The text tower, not the image one.
    assert backends["image_embed"].embed_text_calls[0][0][0] == [
        "a terminal with a stack trace"
    ]
    assert backends["image_embed"].infer_calls == []


def test_frame_query_accepts_a_list(client, parts):
    backends, _ = parts
    response = client.post(
        "/v1/embeddings/frame-query", json={"input": ["slide with a bar chart", "a face"]}
    )
    assert response.status_code == 200
    assert backends["image_embed"].embed_text_calls[0][0][0] == [
        "slide with a bar chart",
        "a face",
    ]


def test_frame_query_reuses_the_already_loaded_frame_model(client, parts, recorder):
    """Both towers are one checkpoint in one slot: the query path must not
    reload the 5 GB model that the image path already has resident."""
    backends, manager = parts
    client.post("/v1/embeddings/image", files=_jpegs(1))
    client.post("/v1/embeddings/frame-query", json={"input": "a terminal"})
    assert manager.slot("image_embed").load_count == 1
    assert recorder.names("load", "unload") == ["image_embed"]
    assert recorder.names("infer", "embed_text") == ["image_embed", "image_embed"]
    assert backends["image_embed"].loaded


def test_frame_query_does_not_touch_the_text_embedder(client, parts):
    backends, _ = parts
    client.post("/v1/embeddings/frame-query", json={"input": "a terminal"})
    assert not backends["embed"].loaded
    assert backends["embed"].infer_calls == []


def test_frame_query_is_not_the_openai_embeddings_endpoint(client, parts):
    """/v1/embeddings stays the transcript model whatever is asked of it."""
    backends, _ = parts
    response = client.post(
        "/v1/embeddings",
        json={"input": "a terminal", "model": "google/siglip2-so400m-patch16-naflex"},
    )
    assert response.json()["dimensions"] == 3
    assert backends["image_embed"].embed_text_calls == []


def test_frame_query_rejects_empty_input(client):
    response = client.post("/v1/embeddings/frame-query", json={"input": []})
    assert response.status_code == 400


def test_frame_query_rejects_an_oversized_batch(client):
    response = client.post("/v1/embeddings/frame-query", json={"input": ["q"] * 33})
    assert response.status_code == 413


def test_frame_query_ignores_the_text_endpoint_s_input_type(client, parts):
    """No ``input_type`` here: the frame model is symmetric and this path is the
    query side by construction. A caller that sends it anyway (the same client
    talks to both endpoints) is answered, not 422'd — the field would have
    meant ``query``, which is what this endpoint already is."""
    backends, _ = parts
    response = client.post(
        "/v1/embeddings/frame-query", json={"input": "a terminal", "input_type": "query"}
    )
    assert response.status_code == 200
    assert backends["image_embed"].embed_text_calls[0][1] == {}


def test_frame_query_missing_dependency_maps_to_503(parts):
    backends, manager = parts
    backends["image_embed"].load_error = BackendUnavailable("transformers missing")
    app = create_app(settings=Settings(_env_file=None), manager=manager)
    with TestClient(app) as c:
        response = c.post("/v1/embeddings/frame-query", json={"input": "a terminal"})
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "backend_unavailable"


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
    assert by_task["embed"]["model"] == "Qwen/Qwen3-Embedding-0.6B"
    assert by_task["stt"]["loaded"] is False
    assert by_task["image_embed"]["loaded"] is False
    assert by_task["image_embed"]["vram_estimate_mb"] == 5000
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
        "/v1/embeddings/image",
        "/v1/embeddings/frame-query",
        "/v1/ocr",
        "/status",
        "/healthz",
    }
