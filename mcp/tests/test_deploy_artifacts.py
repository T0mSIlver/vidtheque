"""The deployment artifacts, checked as artifacts.

`make test` is pytest and CI has no Docker, so nothing here can run
`docker compose config` — and that command is still the only real proof, which
is why docs/deploy-public.md §2 makes an operator run it. What these tests can
do is guard the *shape* the merge depends on, because the 2026-08-10 audit's
B-1 was exactly a shape regression that no test could see:
`compose.public.example.yml` restated `ports:` intending to replace the base
file's wildcard publication, Compose appends sequence fields rather than
replacing them, and the overlay's whole purpose — the origin being reachable
only through the tunnel — silently did not hold for as long as it existed.

So: if someone drops the merge tags, this fails in CI rather than on the box.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
BASE = DEPLOY / "docker-compose.yml"
OVERLAY = DEPLOY / "compose.public.example.yml"


def _strip_comments(text: str) -> str:
    """Comments explain these rules and would otherwise satisfy them.

    Every assertion here looks for a literal, and the lines above each literal
    say why it is there — so a naive grep matches the prose and passes whatever
    the YAML actually says. Directives only.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _service_block(text: str, service: str) -> str:
    """The lines of one service, from its key to the next same-indent key."""
    text = _strip_comments(text)
    match = re.search(rf"^  {service}:$", text, re.M)
    assert match, f"no {service} service"
    rest = text[match.end() :]
    end = re.search(r"^  \S", rest, re.M)
    return rest[: end.start()] if end else rest


def test_the_public_overlay_replaces_the_ports_it_means_to_replace() -> None:
    text = OVERLAY.read_text(encoding="utf-8")

    mcp = _service_block(text, "mcp")
    assert "ports: !override" in mcp, (
        "without !override Compose APPENDS, so the base file's 0.0.0.0 "
        "publication survives beside the loopback one and the origin stays "
        "reachable off-box (audit B-1)"
    )
    assert "127.0.0.1:" in mcp

    worker = _service_block(text, "worker")
    assert "ports: !reset" in worker, (
        "the worker answers an unauthenticated OpenAI-compatible API on a GPU; "
        "mcp reaches it over the compose network, so it needs no host "
        "publication at all (audit F-12)"
    )


def test_the_env_gap_is_closed_in_the_base_file() -> None:
    """`.env` is compose's interpolation source, not the container's
    environment. Without `env_file` on mcp, VIDTHEQUE_AUTH=token in .env is
    read by nobody and a private box boots AUTH=none with the write tools
    registered — silently (field report, 2026-08-12). The fix lives in the
    BASE file so every mode gets it, not just the public overlay."""
    mcp = _service_block(BASE.read_text(encoding="utf-8"), "mcp")
    assert "env_file" in mcp, "mcp must read the whole .env in every mode"
    assert 'TUNNEL_TOKEN: ""' in mcp, "mcp has no use for the tunnel credential"


def test_the_worker_is_handed_neither_secret() -> None:
    """`env_file` hands over the whole .env, and the worker needs none of it.

    The base file already names every variable the worker reads, so an
    env_file there would add nothing it needs and two things it must never
    hold — the OpenRouter key and the tunnel token — on the service whose job
    is running untrusted model weights (audit F-7). Checked in both files so
    an overlay cannot quietly add it back.
    """
    for text in (BASE.read_text(encoding="utf-8"), OVERLAY.read_text(encoding="utf-8")):
        worker = _service_block(text, "worker")
        assert "env_file" not in worker


@pytest.mark.parametrize("image", ["cloudflared"])
def test_no_deployment_image_floats_on_latest(image: str) -> None:
    """`:latest` on the container terminating the public hostname is whatever
    the registry serves next time somebody pulls."""
    block = _service_block(BASE.read_text(encoding="utf-8"), image)
    assert ":latest" not in block, f"{image} must be pinned"


LOCAL = DEPLOY / "compose.local.example.yml"


def test_the_local_overlay_replaces_the_volume_it_means_to_replace() -> None:
    """Same shape rule as the public overlay's ports (audit B-1): without
    `!override` Compose APPENDS, and /data gets both the named volume and the
    bind — silently."""
    mcp = _service_block(LOCAL.read_text(encoding="utf-8"), "mcp")
    assert "volumes: !override" in mcp
    assert ":/data" in mcp
