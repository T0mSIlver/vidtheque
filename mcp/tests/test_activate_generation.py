from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is not installed")


def _generation(root: Path, generation_id: str, videos: int, *, ready: bool = False) -> Path:
    directory = root / "generations" / generation_id
    directory.mkdir(parents=True)
    (directory / "MANIFEST.json").write_text(
        json.dumps({"id": generation_id, "alignment": [], "videos": {"total": videos}})
    )
    if ready:
        (directory / "READY").touch()
    return directory


def test_activate_failure_rollback_and_pruning(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    deploy_dir = tmp_path / "deploy"
    fake_bin = tmp_path / "bin"
    deploy_dir.mkdir()
    fake_bin.mkdir()
    first = _generation(data_root, "2026-01-01-first", 1)
    old = _generation(data_root, "2025-12-01-old", 1)
    (old / "ACTIVATED").touch()
    # No marker: a transfer still arriving, which pruning must leave alone.
    arriving = _generation(data_root, "2025-12-15-arriving", 1)
    second = _generation(data_root, "2026-02-01-second", 2, ready=True)
    (data_root / "current").symlink_to("generations/2026-01-01-first")

    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\n[ -z \"${FAKE_DOCKER_FAIL:-}\" ]\n")
    docker.chmod(0o755)
    curl = fake_bin / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        "if [ -n \"${FAKE_CURL_FAIL:-}\" ]; then exit 22; fi\n"
        "case \"$*\" in *api/meta*) printf '{\"videos\":%s}\\n' \"${FAKE_VIDEOS:-0}\";; "
        "*) printf '{\"ok\":true}\\n';; esac\n"
    )
    curl.chmod(0o755)
    bash_env = tmp_path / "bash_env"
    bash_env.write_text("sleep() { SECONDS=$((SECONDS + 61)); }\n")

    script = Path(__file__).parents[2] / "deploy" / "publish" / "activate-generation.sh"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DATA_ROOT": str(data_root),
        "DEPLOY_DIR": str(deploy_dir),
        "EDGE": "http://edge.test",
        "FAKE_VIDEOS": "2",
        "BASH_ENV": str(bash_env),
    }

    subprocess.run([BASH, str(script), second.name], env=env, check=True)
    assert os.readlink(data_root / "current") == f"generations/{second.name}"
    assert (data_root / "generations" / ".previous").read_text().strip() == first.name
    assert (second / "ACTIVATED").is_file()
    assert not (second / "READY").exists()
    assert not old.exists()
    assert first.exists() and second.exists() and arriving.exists()

    third = _generation(data_root, "2026-03-01-third", 3, ready=True)
    failed_env = {**env, "FAKE_CURL_FAIL": "1", "FAKE_VIDEOS": "3"}
    failed = subprocess.run([BASH, str(script), third.name], env=failed_env, check=False)
    assert failed.returncode != 0
    assert os.readlink(data_root / "current") == f"generations/{second.name}"
    assert (third / "FAILED").is_file()
    assert "health-check" in (third / "FAILED").read_text()
    assert not (third / "READY").exists()
    assert (data_root / "generations" / ".previous").read_text().strip() == first.name

    rejected = _generation(data_root, "2026-04-01-rejected", 4, ready=True)
    rejected_run = subprocess.run(
        [BASH, str(script), "--ready"],
        env={**env, "FAKE_DOCKER_FAIL": "1", "FAKE_VIDEOS": "4"},
        check=False,
    )
    assert rejected_run.returncode != 0
    assert (rejected / "REJECTED").is_file()
    assert "verification failed" in (rejected / "REJECTED").read_text()
    assert not (rejected / "READY").exists()
    assert os.readlink(data_root / "current") == f"generations/{second.name}"

    subprocess.run(
        [BASH, str(script), "--rollback"],
        env={**env, "FAKE_VIDEOS": "1"},
        check=True,
    )
    assert os.readlink(data_root / "current") == f"generations/{first.name}"
    assert (data_root / "generations" / ".previous").read_text().strip() == second.name
