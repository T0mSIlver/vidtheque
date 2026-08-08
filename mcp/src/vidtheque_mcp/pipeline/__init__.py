"""The indexing pipeline — the thing an ``index-video`` job executes.

``build_pipeline`` is the only entry point ``app.py`` needs: it assembles the
layout, the yt-dlp source and the worker client into the object
``PipelineRunner`` drives one item at a time.
"""

from __future__ import annotations

from ..config import Settings
from ..db import Database
from .paths import Layout
from .runner import IndexingPipeline
from .settings import PipelineSettings
from .sources import RecordedSource, Source, YtDlpSource
from .worker_client import HTTPWorkerClient, WorkerAPI

__all__ = [
    "HTTPWorkerClient",
    "IndexingPipeline",
    "Layout",
    "PipelineSettings",
    "RecordedSource",
    "Source",
    "WorkerAPI",
    "YtDlpSource",
    "build_pipeline",
    "worker_client",
]


def build_pipeline(
    settings: Settings,
    db: Database,
    *,
    worker: WorkerAPI | None = None,
    source: Source | None = None,
    pipeline_settings: PipelineSettings | None = None,
) -> IndexingPipeline:
    """Wire the real pipeline. Every dependency is overridable for tests."""
    resolved = pipeline_settings or PipelineSettings.from_env()
    return IndexingPipeline(
        db=db,
        layout=Layout(settings.data_dir),
        settings=resolved,
        source=source or YtDlpSource(resolved),
        worker=worker or worker_client(settings.worker_url, resolved),
    )


def worker_client(worker_url: str, resolved: PipelineSettings) -> HTTPWorkerClient:
    """The shared client: query-time embedding budget, indexing-time everything else."""
    return HTTPWorkerClient(
        worker_url,
        op_timeout_s=resolved.worker_timeout_s,
        stt_timeout_s=resolved.stt_timeout_s,
        retries=resolved.worker_retries,
        retry_max_wait_s=resolved.worker_retry_max_wait_s,
    )
