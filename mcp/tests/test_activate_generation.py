from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is not installed")

SCRIPT = Path(__file__).parents[2] / "deploy" / "publish" / "activate-generation.sh"


def _generation(root: Path, generation_id: str, videos: int, *, ready: bool = False) -> Path:
    directory = root / "generations" / generation_id
    directory.mkdir(parents=True)
    (directory / "MANIFEST.json").write_text(
        json.dumps({"id": generation_id, "alignment": [], "videos": {"total": videos}})
    )
    if ready:
        (directory / "READY").touch()
    return directory


class Box:
    """A temp public box: data root, state dir, and fake `docker` and `curl` on PATH."""

    def __init__(self, tmp_path: Path) -> None:
        self.data = tmp_path / "data"
        self.state = tmp_path / "state"
        deploy = tmp_path / "deploy"
        fake_bin = tmp_path / "bin"
        (self.data / "generations").mkdir(parents=True)
        self.data.chmod(0o755)
        deploy.mkdir()
        fake_bin.mkdir()
        docker = fake_bin / "docker"
        docker.write_text('#!/bin/sh\n[ -z "${FAKE_DOCKER_FAIL:-}" ]\n')
        docker.chmod(0o755)
        curl = fake_bin / "curl"
        curl.write_text(
            "#!/bin/sh\n"
            'if [ -n "${FAKE_CURL_FAIL:-}" ]; then exit 22; fi\n'
            'case "$*" in *api/meta*) printf \'{"videos":%s}\\n\' "${FAKE_VIDEOS:-0}";; '
            "*) printf '{\"ok\":true}\\n';; esac\n"
        )
        curl.chmod(0o755)
        bash_env = tmp_path / "bash_env"
        bash_env.write_text("sleep() { SECONDS=$((SECONDS + 61)); }\n")
        self.env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DATA_ROOT": str(self.data),
            "DEPLOY_DIR": str(deploy),
            "STATE_DIR": str(self.state),
            "EDGE": "http://edge.test",
            "BASH_ENV": str(bash_env),
        }

    def run(self, *args: str, check: bool = True, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [BASH, str(SCRIPT), *args],
            env={**self.env, **env},
            check=check,
            capture_output=True,
            text=True,
        )

    def current(self) -> str:
        return os.readlink(self.data / "current")

    def marker(self, generation_id: str, kind: str) -> Path:
        return self.state / "markers" / f"{generation_id}.{kind}"

    def previous(self) -> str:
        return (self.state / "previous").read_text().strip()


def test_first_activation_has_nothing_to_roll_back_to_or_prune(tmp_path: Path) -> None:
    box = Box(tmp_path)
    first = _generation(box.data, "2026-01-01-first", 1, ready=True)
    stray = _generation(box.data, "2025-11-01-stray", 1)

    box.run("--ready", FAKE_VIDEOS="1")

    assert box.current() == f"generations/{first.name}"
    assert box.marker(first.name, "ACTIVATED").is_file()
    assert not (first / "READY").exists()
    assert not (box.state / "previous").exists()
    assert stray.exists()
    assert box.run("--rollback", check=False).returncode != 0


def test_activate_failure_rollback_and_pruning(tmp_path: Path) -> None:
    box = Box(tmp_path)
    first = _generation(box.data, "2026-01-01-first", 1)
    (box.data / "current").symlink_to(f"generations/{first.name}")
    old = _generation(box.data, "2025-12-01-old", 1)
    (box.state / "markers").mkdir(parents=True)
    box.marker(old.name, "ACTIVATED").touch()
    # No outcome recorded: a transfer still arriving, which pruning must leave alone.
    arriving = _generation(box.data, "2025-12-15-arriving", 1)
    second = _generation(box.data, "2026-02-01-second", 2, ready=True)

    box.run(second.name, FAKE_VIDEOS="2")
    assert box.current() == f"generations/{second.name}"
    assert box.previous() == first.name
    assert box.marker(second.name, "ACTIVATED").is_file()
    assert not (second / "READY").exists()
    assert not old.exists() and not box.marker(old.name, "ACTIVATED").exists()
    assert first.exists() and second.exists() and arriving.exists()

    third = _generation(box.data, "2026-03-01-third", 3, ready=True)
    failed = box.run(third.name, check=False, FAKE_CURL_FAIL="1", FAKE_VIDEOS="3")
    assert failed.returncode != 0
    assert box.current() == f"generations/{second.name}"
    assert "health-check" in box.marker(third.name, "FAILED").read_text()
    assert not (third / "READY").exists()
    assert box.previous() == first.name

    rejected = _generation(box.data, "2026-04-01-rejected", 4, ready=True)
    rejected_run = box.run("--ready", check=False, FAKE_DOCKER_FAIL="1", FAKE_VIDEOS="4")
    assert rejected_run.returncode != 0
    assert "verification failed" in box.marker(rejected.name, "REJECTED").read_text()
    assert not (rejected / "READY").exists()
    assert box.current() == f"generations/{second.name}"

    box.run("--rollback", FAKE_VIDEOS="1")
    assert box.current() == f"generations/{first.name}"
    assert box.previous() == second.name


def test_a_return_to_a_served_generation_skips_the_hash(tmp_path: Path) -> None:
    box = Box(tmp_path)
    first = _generation(box.data, "2026-01-01-first", 1)
    second = _generation(box.data, "2026-02-01-second", 2)
    (box.data / "current").symlink_to(f"generations/{second.name}")
    (box.state / "markers").mkdir(parents=True)
    # A marker planted inside the generation is not this script's record and must not count.
    (second / "ACTIVATED").touch()
    (first / "ACTIVATED").touch()

    dry = box.run("--dry-run", first.name)
    assert "--served" not in dry.stdout

    box.marker(first.name, "ACTIVATED").touch()
    dry = box.run("--dry-run", first.name)
    assert "--served" in dry.stdout


def test_two_ready_generations_end_on_the_newest(tmp_path: Path) -> None:
    box = Box(tmp_path)
    older = _generation(box.data, "2026-05-01-older", 5, ready=True)
    newer = _generation(box.data, "2026-05-02-newer", 6, ready=True)

    box.run("--ready", FAKE_VIDEOS="6")

    assert box.current() == f"generations/{newer.name}"
    assert "superseded" in box.marker(older.name, "REJECTED").read_text()
    assert not (older / "READY").exists()
    # Nothing is left to trigger the path unit again.
    assert "no ready generation" in box.run("--ready").stdout


def test_a_planted_symlink_is_never_written_through(tmp_path: Path) -> None:
    box = Box(tmp_path)
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    bad = _generation(box.data, "2026-06-01-bad", 1, ready=True)
    for name in ("FAILED", "REJECTED", "ACTIVATED"):
        (bad / name).symlink_to(victim)

    box.run("--ready", check=False, FAKE_DOCKER_FAIL="1")
    assert victim.read_text() == "untouched"

    box.run(bad.name, check=False, FAKE_CURL_FAIL="1", FAKE_VIDEOS="1")
    assert victim.read_text() == "untouched"

    linked = box.data / "generations" / "2026-06-02-linked"
    linked.symlink_to(bad)
    assert box.run(linked.name, check=False).returncode != 0
    assert victim.read_text() == "untouched"


def test_a_data_root_someone_else_can_write_is_refused(tmp_path: Path) -> None:
    box = Box(tmp_path)
    _generation(box.data, "2026-07-01-any", 1, ready=True)
    box.data.chmod(0o777)
    refused = box.run("--ready", check=False, FAKE_VIDEOS="1")
    assert refused.returncode != 0
    assert "must be owned by" in refused.stdout
    assert not (box.data / "current").exists()
