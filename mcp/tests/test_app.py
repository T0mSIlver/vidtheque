"""Application construction and lifespan configuration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from vidtheque_mcp.app import assemble
from vidtheque_mcp.config import ConfigError, Settings
from vidtheque_mcp.dashboard import DashboardSettings
from vidtheque_mcp.jobs.runner import NotImplementedPipeline
from vidtheque_mcp.public import PublicSettings

from .conftest import FakeEmbeddings


def settings_from_env(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path, run_pipeline: str | None
) -> Settings:
    monkeypatch.setenv("VIDTHEQUE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("VIDTHEQUE_SECRET", "test-secret-not-for-production")
    monkeypatch.delenv("VIDTHEQUE_PUBLIC_HOSTNAME", raising=False)
    if run_pipeline is None:
        monkeypatch.delenv("VIDTHEQUE_RUN_PIPELINE", raising=False)
    else:
        monkeypatch.setenv("VIDTHEQUE_RUN_PIPELINE", run_pipeline)
    return Settings.from_env()


@pytest.mark.parametrize(("value", "expected"), [("0", False), (None, True)])
@pytest.mark.asyncio
async def test_pipeline_runner_follows_environment_setting(
    monkeypatch: pytest.MonkeyPatch,
    data_dir: Path,
    value: str | None,
    expected: bool,
) -> None:
    settings = settings_from_env(monkeypatch, data_dir, value)
    parts = assemble(
        settings,
        embeddings=FakeEmbeddings(),
        pipeline=NotImplementedPipeline(),
        public=PublicSettings(),
        dashboard=DashboardSettings(),
    )
    parts.db.open = AsyncMock()
    parts.db.close = AsyncMock()
    parts.runner.start = AsyncMock()
    parts.runner.stop = AsyncMock()

    async with parts.app.router.lifespan_context(parts.app):
        pass

    assert parts.runner.start.await_count == int(expected)
    assert parts.runner.stop.await_count == int(expected)
    parts.db.open.assert_awaited_once()
    parts.db.close.assert_awaited_once()


def test_run_pipeline_rejects_invalid_boolean(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    with pytest.raises(ConfigError, match="VIDTHEQUE_RUN_PIPELINE"):
        settings_from_env(monkeypatch, data_dir, "sometimes")
