"""Fixtures: a seeded corpus, a fake embedding client, and assembled apps.

Nothing here downloads a model, needs a GPU, or reaches the network. The
embedding client is faked at the HTTP seam — CLAUDE.md's boundary rule says no
Python import ever crosses into ``vidtheque_worker``, not even in tests, so the
fake implements the same tiny interface rather than sharing code with it.
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Sequence

import pytest

from .pipeline_fakes import clip  # noqa: F401 — session fixture for the frame tests
from vidtheque_mcp.app import Assembled, assemble
from vidtheque_mcp.config import Settings
from vidtheque_mcp.db import migrations
from vidtheque_mcp.db.connection import open_write_connection
from vidtheque_mcp.db.queries import pack_f32

TEXT_DIM = 1024
FRAME_DIM = 1152


class FakeEmbeddings:
    """Deterministic unit vectors keyed on the text, no network.

    Stands in for the worker at the HTTP seam. ``/v1/embeddings`` always
    answers with the transcript model (as the real worker does);
    ``embed_frame_query`` plays ``POST /v1/embeddings/frame-query`` — flip
    ``serves_frame_text`` off to play a worker that predates the endpoint
    (404 → ``FrameQueryUnsupported``), the case the frame leg degrades
    through.
    """

    FRAME_MODEL = "google/siglip2-so400m-patch16-naflex"

    def __init__(
        self,
        dim: int = TEXT_DIM,
        model: str = "Qwen/Qwen3-Embedding-0.6B",
        serves_frame_text: bool = True,
    ) -> None:
        self.dim = dim
        self.model = model
        self.serves_frame_text = serves_frame_text
        self.calls: list[tuple[str | None, list[str]]] = []
        self.fail = False

    async def embed(
        self, texts: Sequence[str], model: str | None = None, input_type: str = "query"
    ) -> tuple[list[list[float]], str | None, int | None]:
        from vidtheque_mcp.embeddings import EmbeddingUnavailable

        self.calls.append((model, list(texts)))
        if self.fail:
            raise EmbeddingUnavailable("fake worker is down")
        # The real /v1/embeddings answers with the transcript model whatever
        # `model` asks for — frame-space queries have their own endpoint.
        return [vector_for(t, self.dim) for t in texts], self.model, self.dim

    async def embed_frame_query(
        self, texts: Sequence[str], model: str | None = None
    ) -> tuple[list[list[float]], str | None, int | None]:
        from vidtheque_mcp.embeddings import EmbeddingUnavailable, FrameQueryUnsupported

        self.calls.append((model, list(texts)))
        if self.fail:
            raise EmbeddingUnavailable("fake worker is down")
        if not self.serves_frame_text:
            raise FrameQueryUnsupported("fake worker predates /v1/embeddings/frame-query")
        return [vector_for(t, FRAME_DIM) for t in texts], self.FRAME_MODEL, FRAME_DIM

    async def aclose(self) -> None:
        return None


def vector_for(text: str, dim: int = TEXT_DIM) -> list[float]:
    """A stable pseudo-embedding: same text -> same L2-normalized vector."""
    seed = sum((i + 1) * ord(c) for i, c in enumerate(text)) or 1
    raw = [math.sin(seed * (i + 1) * 0.001) for i in range(dim)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]


# --------------------------------------------------------------------- fixture


VIDEOS = [
    {
        "source_id": "kCc8FmEb1nY",
        "title": "Let's build GPT: from scratch",
        "channel": "Andrej Karpathy",
        "published_at": 1_673_913_600,  # 2023-01-17
        "duration_s": 7000.0,
        "stages": ("stt", "chunk", "text_embed", "keyframe", "ocr", "frame_embed"),
        "cues": [
            "we cache the keys and the values at every new token",
            "otherwise you would recompute attention over the entire prefix",
            "which is quadratic in the sequence length",
            "the cache makes it linear in the number of new tokens",
            "and the price you pay for that is memory",
            "much later we talk about tokenization instead",
        ],
        # deliberately: the first five cues are contiguous, the sixth is 400 s
        # later so the clustering bound has something to bind on.
        "cue_times": [(0.0, 2.8), (3.0, 5.8), (6.0, 8.8), (9.0, 11.8), (12.0, 14.8), (420.0, 423.0)],
        "ocr": [(5.0, "kv cache size = 2 * n_layers * n_heads"), (430.0, "nvidia-smi 18304MiB")],
    },
    {
        "source_id": "zduSFxRajkE",
        "title": "Making LLMs go brrr",
        "channel": "GPU MODE",
        "published_at": 1_708_387_200,  # 2024-02-20
        "duration_s": 3600.0,
        "stages": ("stt", "chunk", "text_embed", "keyframe", "ocr"),
        "cues": [
            "paged attention keeps a block table for the kv cache",
            "fragmentation drops from sixty percent to four percent",
            "caching is the whole trick here",
        ],
        "cue_times": [(10.0, 13.0), (13.5, 16.0), (200.0, 203.0)],
        "ocr": [(12.0, "paged kv cache | block table | 4% fragmentation")],
    },
    {
        "source_id": "eMlx5fFNoYc",
        "title": "Visualizing transformers",
        "channel": "3Blue1Brown",
        "published_at": 1_712_000_000,
        "duration_s": 1200.0,
        "stages": ("stt", "chunk", "text_embed"),  # no OCR, no frames
        "cues": ["the attention pattern is a communication mechanism"],
        "cue_times": [(30.0, 34.0)],
        "ocr": [],
    },
]


def seed(db_path: Path, keyframes_dir: Path, *, with_vectors: bool = True) -> None:
    conn = open_write_connection(db_path)
    try:
        migrations.migrate(conn)
        conn.execute("BEGIN IMMEDIATE")
        for spec in VIDEOS:
            _seed_video(conn, spec, keyframes_dir, with_vectors)
        _seed_tags(conn)
        conn.execute("COMMIT")
    finally:
        conn.close()


def _seed_video(
    conn: sqlite3.Connection, spec: dict, keyframes_dir: Path, with_vectors: bool
) -> None:
    cursor = conn.execute(
        "INSERT INTO videos (owner_id, source_id, url, title, description, channel_name, "
        "published_at, duration_s, index_state, indexed_at) "
        "VALUES (1, ?, ?, ?, ?, ?, ?, ?, 'ready', 1750000000)",
        (
            spec["source_id"],
            f"https://youtu.be/{spec['source_id']}",
            spec["title"],
            f"description of {spec['title']}",
            spec["channel"],
            spec["published_at"],
            spec["duration_s"],
        ),
    )
    vid = int(cursor.lastrowid or 0)
    for stage in spec["stages"]:
        conn.execute(
            "INSERT INTO video_stages (video_id, stage, state, model_key) "
            "VALUES (?, ?, 'done', 'seed')",
            (vid, stage),
        )
    conn.execute(
        "INSERT INTO chapters (video_id, seq, start_s, end_s, title) VALUES (?, 0, 0, ?, ?)",
        (vid, spec["duration_s"], "intro"),
    )
    conn.execute(
        "INSERT INTO video_links (video_id, seq, t_s, url, title) VALUES (?, 0, 5.0, ?, ?)",
        (vid, "https://example.com/paper", "the paper"),
    )

    cue_ids: list[int] = []
    for seq, (text, (start, end)) in enumerate(zip(spec["cues"], spec["cue_times"], strict=True)):
        cur = conn.execute(
            "INSERT INTO cues (video_id, seq, start_s, end_s, text, words_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (vid, seq, start, end, text, None),
        )
        cue_ids.append(int(cur.lastrowid or 0))

    # One chunk per video, spanning every cue (chunks_span is what turns a
    # vector hit back into cues).
    chunk_text = " ".join(spec["cues"])
    cur = conn.execute(
        "INSERT INTO chunks (video_id, seq, start_s, end_s, first_cue_id, last_cue_id, "
        "text, n_chars) VALUES (?, 0, ?, ?, ?, ?, ?, ?)",
        (
            vid,
            spec["cue_times"][0][0],
            spec["cue_times"][-1][1],
            cue_ids[0],
            cue_ids[-1],
            chunk_text,
            len(chunk_text),
        ),
    )
    chunk_id = int(cur.lastrowid or 0)
    if with_vectors:
        conn.execute(
            "INSERT INTO vec_chunks (chunk_id, video_id, start_s, embedding) VALUES (?, ?, ?, ?)",
            (chunk_id, vid, spec["cue_times"][0][0], pack_f32(vector_for(chunk_text))),
        )

    frame_dir = keyframes_dir / spec["source_id"]
    frame_dir.mkdir(parents=True, exist_ok=True)
    for ordinal, (t_s, text) in enumerate(spec["ocr"]):
        relative = f"keyframes/{spec['source_id']}/{ordinal:05d}-{int(t_s * 1000):09d}.jpg"
        payload = _fake_jpeg(ordinal)
        (keyframes_dir.parent / relative).write_bytes(payload)
        cur = conn.execute(
            "INSERT INTO keyframes (video_id, ord, t_s, shot_id, shot_start_s, shot_end_s, "
            "phash, sharpness, width, height, jpeg_path, jpeg_bytes, ocr_state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1280, 720, ?, ?, 'done')",
            (vid, ordinal, t_s, ordinal, t_s, t_s + 5, 1234 + ordinal, 10.0, relative, len(payload)),
        )
        kf = int(cur.lastrowid or 0)
        conn.execute(
            "INSERT INTO ocr_lines (keyframe_id, video_id, t_s, line_no, text, conf, "
            "x0, y0, x1, y1) VALUES (?, ?, ?, 0, ?, 0.9, 0, 0, 1, 1)",
            (kf, vid, t_s, text),
        )
        if with_vectors and "frame_embed" in spec["stages"]:
            conn.execute(
                "INSERT INTO vec_frames (keyframe_id, video_id, t_s, embedding) VALUES (?, ?, ?, ?)",
                (kf, vid, t_s, pack_f32(vector_for(text, FRAME_DIM))),
            )


def _seed_tags(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO tags (owner_id, ns, name) VALUES (1, 'topic', 'attention')")
    conn.execute("INSERT INTO tags (owner_id, ns, name) VALUES (1, 'series', 'gpu-mode')")
    conn.execute(
        "INSERT INTO video_tags (video_id, tag_id) SELECT v.id, t.id FROM videos v, tags t "
        "WHERE t.full = 'topic:attention'"
    )
    conn.execute(
        "INSERT INTO video_tags (video_id, tag_id) SELECT v.id, t.id FROM videos v, tags t "
        "WHERE t.full = 'series:gpu-mode' AND v.source_id = 'zduSFxRajkE'"
    )


def _fake_jpeg(seed_byte: int) -> bytes:
    """Enough of a JPEG to be a real file with the right magic bytes."""
    return b"\xff\xd8\xff\xe0" + bytes([seed_byte % 251]) * 64 + b"\xff\xd9"


# --------------------------------------------------------------------- pytest


PROTOCOL_VERSION = "2026-07-28"


def rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    """A 2026-07-28 sessionless request envelope.

    The protocol went sessionless: there is no `Mcp-Session-Id`, and every
    request carries its protocol version and client capabilities in `_meta`.
    """
    body = dict(params or {})
    body["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body}


def rpc_headers(method: str, token: str | None = None, name: str | None = None) -> dict[str, str]:
    """The modern transport cross-checks `mcp-method`/`mcp-name` against the body."""
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "mcp-method": method,
    }
    if name:
        headers["mcp-name"] = name
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    (root / "keyframes").mkdir(parents=True)
    return root


@pytest.fixture
def settings(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key in list(_ENV_KEYS):
        monkeypatch.delenv(key, raising=False)
    return Settings(
        data_dir=data_dir,
        public_url="http://localhost:8080",
        worker_url="http://worker:8081",
        auth_mode="none",
        secret="test-secret-not-for-production",
    )


_ENV_KEYS = (
    "VIDTHEQUE_AUTH",
    "VIDTHEQUE_TOKEN",
    "VIDTHEQUE_PASSWORD",
    "VIDTHEQUE_SECRET",
    "VIDTHEQUE_PUBLIC_HOSTNAME",
    "VIDTHEQUE_DATA_DIR",
    "VIDTHEQUE_PUBLIC_URL",
    "VIDTHEQUE_WORKER_URL",
    "PUBLIC_URL",
    "WORKER_URL",
)


@pytest.fixture
def seeded(settings: Settings) -> Settings:
    seed(settings.db_path, settings.keyframes_dir)
    return settings


@pytest.fixture
def fake_embeddings() -> FakeEmbeddings:
    return FakeEmbeddings()


@pytest.fixture
async def assembled(seeded: Settings, fake_embeddings: FakeEmbeddings):
    """An assembled app with the lifespan entered, pipeline runner off.

    The pipeline is the *placeholder* on purpose. These tests are about the
    tool surface and the job bookkeeping around the seam; injecting the real
    indexing pipeline here would put yt-dlp behind a `run_once()` call, and a
    test suite that can reach YouTube is a test suite that fails on a plane.
    The real pipeline is exercised in `test_pipeline_*.py`, against fakes.
    """
    from vidtheque_mcp.jobs.runner import NotImplementedPipeline

    parts: Assembled = assemble(
        seeded,
        embeddings=fake_embeddings,
        run_pipeline=False,
        pipeline=NotImplementedPipeline(),
    )
    await parts.db.open()
    try:
        yield parts
    finally:
        await parts.db.close()
        parts.auth.close()
