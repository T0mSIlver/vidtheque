"""Fakes and fixtures for the indexing pipeline tests.

Same rule as ``conftest.py``: nothing here downloads a model, needs a GPU, or
reaches the network. yt-dlp is replaced by canned info dicts captured from real
extractions (research §5.1 lists the fields they carry), and the worker is
replaced at the HTTP seam by an object implementing the same ``WorkerAPI``
protocol — no import ever crosses into ``vidtheque_worker``, not even here.

The one thing that *is* real is the video: scene detection and perceptual
hashing are the two stages whose bugs are invisible to a mock, so the keyframe
tests run against a clip ffmpeg synthesizes on the spot. It is 8 seconds of
test patterns at 10 fps — about 30 KB and under a second to make.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Sequence

import pytest

from vidtheque_mcp.pipeline.sources import RecordedSource
from vidtheque_mcp.pipeline.worker_client import OcrLine

# --------------------------------------------------------------- canned yt-dlp

VIDEO_URL = "https://youtu.be/aB3dEfG7hIj"
PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL9tOrKPmQ4nAbC"

INFO: dict[str, Any] = {
    "_type": "video",
    "id": "aB3dEfG7hIj",
    "title": "Paged attention, end to end",
    "description": (
        "The block table walkthrough.\n"
        "00:15 the fragmentation number https://example.com/paper\n"
        "Sponsor: https://example.com/sponsor\n"
    ),
    "webpage_url": VIDEO_URL,
    "duration": 620.0,
    "language": "en",
    "channel": "GPU MODE",
    "channel_id": "UC_gpumode",
    "uploader": "GPU MODE",
    "upload_date": "20260210",
    "timestamp": 1_770_000_000,
    "live_status": "not_live",
    "availability": "public",
    "extractor_key": "Youtube",
    "chapters": [
        {"start_time": 0.0, "end_time": 60.0, "title": "intro"},
        {"start_time": 60.0, "end_time": 620.0, "title": "the block table"},
    ],
    "heatmap": [
        {"start_time": 0.0, "end_time": 6.2, "value": 0.91},
        {"start_time": 6.2, "end_time": 12.4, "value": 0.23},
    ],
    "subtitles": {
        "en": [{"ext": "json3", "url": "https://timedtext.example/manual.json3"}],
    },
    "automatic_captions": {
        "en": [
            {"ext": "json3", "url": "https://timedtext.example/auto.json3"},
            {"ext": "vtt", "url": "https://timedtext.example/auto.vtt"},
        ],
    },
}

SECOND_URL = "https://youtu.be/zZ9yY8xX7wV"

INFO_2: dict[str, Any] = {
    "_type": "video",
    "id": "zZ9yY8xX7wV",
    "title": "Fused softmax, one kernel",
    "description": "no links here",
    "webpage_url": SECOND_URL,
    "duration": 300.0,
    "language": "en",
    "channel": "GPU MODE",
    "channel_id": "UC_gpumode",
    "upload_date": "20260301",
    "live_status": "not_live",
    "availability": "public",
    "extractor_key": "Youtube",
    "automatic_captions": {"en": [{"ext": "json3", "url": "https://timedtext.example/auto.json3"}]},
}

PLAYLIST_INFO: dict[str, Any] = {
    "_type": "playlist",
    "id": "PL9tOrKPmQ4nAbC",
    "title": "Kernels, start to finish",
    "entries": [
        {"_type": "url", "id": "aB3dEfG7hIj", "url": VIDEO_URL, "title": "Paged attention"},
        {
            "_type": "url",
            "id": "zZ9yY8xX7wV",
            "url": "https://youtu.be/zZ9yY8xX7wV",
            "title": "Fused softmax",
        },
        # The same video twice: a real playlist does this, and the fan-out has
        # to survive it.
        {"_type": "url", "id": "aB3dEfG7hIj", "url": VIDEO_URL, "title": "Paged attention"},
    ],
}

# The rolling window is the trap: `aAppend` events repeat the growing caption
# line, so an implementation that concatenates event text indexes every sentence
# two or three times. Only events with `segs` count.
AUTO_JSON3 = json.dumps(
    {
        "wireMagic": "pb3",
        "events": [
            {"tStartMs": 0, "dDurationMs": 1000, "aAppend": 1, "segs": [{"utf8": "\n"}]},
            {
                "tStartMs": 4080,
                "dDurationMs": 4200,
                "wWinId": 1,
                "segs": [
                    {"utf8": "paged", "acAsrConf": 0},
                    {"utf8": " attention", "tOffsetMs": 239},
                    {"utf8": " keeps", "tOffsetMs": 520},
                    {"utf8": " a", "tOffsetMs": 900},
                    {"utf8": " block", "tOffsetMs": 1480},
                    {"utf8": " table", "tOffsetMs": 1840},
                ],
            },
            {"tStartMs": 8300, "dDurationMs": 10, "segs": [{"utf8": "\n"}]},
            {
                "tStartMs": 8400,
                "dDurationMs": 3000,
                "segs": [
                    {"utf8": "fragmentation"},
                    {"utf8": " drops", "tOffsetMs": 400},
                    {"utf8": " to", "tOffsetMs": 900},
                    {"utf8": " four", "tOffsetMs": 1200},
                    {"utf8": " percent", "tOffsetMs": 1600},
                ],
            },
        ],
    }
)

# Manual tracks carry no per-word offsets — verified both ways in research §5.4.
MANUAL_JSON3 = json.dumps(
    {
        "events": [
            {
                "tStartMs": 4000,
                "dDurationMs": 4000,
                "segs": [{"utf8": "Paged attention, properly."}],
            },
            {
                "tStartMs": 8400,
                "dDurationMs": 3000,
                "segs": [{"utf8": "Fragmentation drops to 4%."}],
            },
        ]
    }
)

WHISPERX_RESPONSE: dict[str, Any] = {
    "task": "transcribe",
    "language": "en",
    "duration": 12.0,
    "text": "paged attention keeps a block table fragmentation drops to four percent",
    "model": "whisperx-large-v3",
    "segments": [
        {
            "id": 0,
            "start": 4.08,
            "end": 8.28,
            "text": " paged attention keeps a block table",
            "avg_logprob": -0.21,
            "words": [
                {"word": "paged", "start": 4.08, "end": 4.31},
                {"word": "attention", "start": 4.32, "end": 4.6},
                {"word": "keeps", "start": 4.6, "end": 4.98},
                {"word": "a", "start": 4.98, "end": 5.05},
                {"word": "block", "start": 5.56, "end": 5.9},
                {"word": "table", "start": 5.92, "end": 6.4},
            ],
        },
        {
            "id": 1,
            "start": 8.4,
            "end": 11.4,
            "text": " fragmentation drops to four percent",
            "avg_logprob": -0.18,
            "words": [
                {"word": "fragmentation", "start": 8.4, "end": 8.8},
                {"word": "drops", "start": 8.8, "end": 9.3},
                {"word": "to", "start": 9.3, "end": 9.6},
                {"word": "four", "start": 9.6, "end": 10.0},
                {"word": "percent", "start": 10.0, "end": 11.4},
            ],
        },
    ],
}


def canned_source(clip: Path | None = None) -> "ClipSource":
    return ClipSource(
        infos={VIDEO_URL: INFO, SECOND_URL: INFO_2, PLAYLIST_URL: PLAYLIST_INFO},
        subtitles={
            "https://timedtext.example/auto.json3": AUTO_JSON3,
            "https://timedtext.example/manual.json3": MANUAL_JSON3,
        },
        clip=clip,
    )


class ClipSource(RecordedSource):
    """`RecordedSource` that hands out a real video file for the frame stages."""

    def __init__(self, *, clip: Path | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.clip = clip
        self.downloads: list[str] = []

    def download_audio(self, url, source_id, dest_dir, codec):  # type: ignore[no-untyped-def]
        self.downloads.append("audio")
        return super().download_audio(url, source_id, dest_dir, codec)

    def download_video(self, url, source_id, dest_dir, max_height):  # type: ignore[no-untyped-def]
        from vidtheque_mcp.pipeline.sources import MediaFile

        self.downloads.append("video")
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"{source_id}.mp4"
        if self.clip is None:
            return super().download_video(url, source_id, dest_dir, max_height)
        shutil.copyfile(self.clip, target)
        return MediaFile(path=target)


# ------------------------------------------------------------------ fake worker


class FakeWorker:
    """The worker at the HTTP seam: same protocol, no HTTP, no GPU.

    Vectors are deterministic unit vectors keyed on their input, so a chunk's
    stored vector and the same text embedded at query time land on each other —
    which is what makes the end-to-end search assertion meaningful.
    """

    def __init__(
        self,
        *,
        healthy: bool = True,
        text_dim: int = 1024,
        frame_dim: int = 1152,
        text_model: str = "qwen3-embedding-0.6b",
        frame_model: str = "siglip2-so400m-patch16-naflex",
        transcript: dict[str, Any] | None = None,
        # Deliberately screen text that is *not* in the transcript: the OCR leg
        # drops a line when a longer transcript cue matching the same query sits
        # within 5 s of it (index-schema §4.6), so an on-screen assertion has to
        # use words nobody said out loud.
        ocr_text: str = "nvidia-smi 18304MiB / 24576MiB",
        fail: set[str] | None = None,
        on_transcribe: Any = None,
    ) -> None:
        # Lets a test reach in mid-item — the only way to exercise a
        # cancellation that arrives *between stages* rather than before one.
        self.on_transcribe = on_transcribe
        self._healthy = healthy
        self.text_dim = text_dim
        self.frame_dim = frame_dim
        self.text_model = text_model
        self.frame_model = frame_model
        self.transcript = transcript if transcript is not None else WHISPERX_RESPONSE
        self.ocr_text = ocr_text
        self.fail = fail or set()
        self.calls: list[str] = []

    async def healthy(self) -> bool:
        return self._healthy

    async def transcribe(self, audio: Path, *, language=None, model=None) -> dict[str, Any]:
        self.calls.append("transcribe")
        self._maybe_fail("transcribe")
        if self.on_transcribe is not None:
            await self.on_transcribe()
        return dict(self.transcript)

    async def ocr(self, images: Sequence[Path], *, min_confidence=None):
        self.calls.append(f"ocr:{len(images)}")
        self._maybe_fail("ocr")
        return (
            [
                [OcrLine(text=self.ocr_text, confidence=0.94, bbox=(10.0, 20.0, 300.0, 60.0))]
                for _ in images
            ],
            "rapidocr-v2",
        )

    async def embed_images(self, images: Sequence[Path], *, model=None, max_num_patches=None):
        self.calls.append(f"embed_images:{len(images)}")
        self._maybe_fail("embed_images")
        return (
            [unit_vector(str(path.name), self.frame_dim) for path in images],
            self.frame_model,
            self.frame_dim,
        )

    async def embed(self, texts: Sequence[str], model=None, input_type: str = "query"):
        self.calls.append(f"embed:{input_type}:{len(texts)}")
        self._maybe_fail("embed")
        if model == self.frame_model:
            return [unit_vector(t, self.frame_dim) for t in texts], self.frame_model, self.frame_dim
        return [unit_vector(t, self.text_dim) for t in texts], self.text_model, self.text_dim

    async def embed_frame_query(self, texts: Sequence[str], *, model=None):
        self.calls.append("embed_frame_query")
        self._maybe_fail("embed_frame_query")
        return [unit_vector(t, self.frame_dim) for t in texts], self.frame_model, self.frame_dim

    async def aclose(self) -> None:
        return None

    def _maybe_fail(self, name: str) -> None:
        from vidtheque_mcp.pipeline.worker_client import WorkerUnavailable

        if name in self.fail:
            raise WorkerUnavailable(f"fake worker refuses {name}")


def unit_vector(text: str, dim: int) -> list[float]:
    import math

    seed = sum((i + 1) * ord(c) for i, c in enumerate(text)) or 1
    raw = [math.sin(seed * (i + 1) * 0.001) for i in range(dim)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]


# ------------------------------------------------------------------ the clip


def synth_clip(path: Path) -> Path:
    """Four shots, two of them pixel-identical.

    The repeat is the point: it is what the perceptual dedup has to catch, and
    a synthetic clip is the only fixture where "these two frames are the same
    screen" is known rather than asserted by eye.
    """
    if shutil.which("ffmpeg") is None:  # pragma: no cover - environment
        pytest.skip("ffmpeg is not installed")
    path.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    for pattern in ("testsrc", "smptebars", "testsrc2", "smptebars"):
        inputs += ["-f", "lavfi", "-i", f"{pattern}=size=320x240:rate=10:duration=2"]
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        *inputs,
        "-filter_complex",
        "[0:v][1:v][2:v][3:v]concat=n=4:v=1[out]",
        "-map",
        "[out]",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0 or not path.exists():  # pragma: no cover - environment
        pytest.skip(f"ffmpeg could not synthesize the fixture: {result.stderr[-200:]}")
    return path


@pytest.fixture(scope="session")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return synth_clip(tmp_path_factory.mktemp("clip") / "fixture.mp4")
