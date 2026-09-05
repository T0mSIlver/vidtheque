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

    # The edge owns the publication since the front end moved to Next.js: caddy
    # is the one origin and the only service the base file publishes at all.
    caddy = _service_block(text, "caddy")
    assert "ports: !override" in caddy, (
        "without !override Compose APPENDS, so the base file's 0.0.0.0 "
        "publication survives beside the loopback one and the origin stays "
        "reachable off-box (audit B-1)"
    )
    assert "127.0.0.1:" in caddy

    worker = _service_block(text, "worker")
    assert "ports: !reset" in worker, (
        "the worker answers an unauthenticated OpenAI-compatible API on a GPU; "
        "mcp reaches it over the compose network, so it needs no host "
        "publication at all (audit F-12)"
    )


def test_only_the_edge_is_published() -> None:
    """The trusted-header argument rests on there being ONE way in.

    `mcp` published a host port until the edge existed, and the public overlay
    bound it to loopback. Now caddy is the only client either server has, so
    neither is published in the base file at all — a second listener would be a
    second way to reach Python and forge `CF-Connecting-IP`, which is exactly
    what docs/deploy-public.md §4 says must not exist.
    """
    base = BASE.read_text(encoding="utf-8")
    for service in ("mcp", "web"):
        assert "ports:" not in _service_block(base, service), (
            f"{service} must not publish a host port: caddy reaches it on the "
            "compose network, and the edge is the only listener"
        )
    assert "ports:" in _service_block(base, "caddy"), "the edge is published"


def test_the_front_end_is_handed_neither_secret() -> None:
    """The same rule as the worker's, for the same reason (audit F-7).

    The front end needs two variables and the whole `.env` would give it the
    OpenRouter key and the tunnel token, readable from `docker inspect` on a
    process the internet talks to. Its client-IP header is interpolated from
    the instance's trusted header rather than written twice, because the two
    must be equal (frontend-migration.md §1c).
    """
    web = _service_block(BASE.read_text(encoding="utf-8"), "web")
    assert "env_file" not in web
    assert "VIDTHEQUE_API_URL: http://mcp:8080" in web
    assert "VIDTHEQUE_CLIENT_IP_HEADER: ${VIDTHEQUE_TRUSTED_IP_HEADER" in web


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


@pytest.mark.parametrize("image", ["cloudflared", "caddy"])
def test_no_deployment_image_floats_on_latest(image: str) -> None:
    """`:latest` on the container terminating the public hostname is whatever
    the registry serves next time somebody pulls. The edge is the same class of
    decision one hop in: it decides which process answers a request."""
    block = _service_block(BASE.read_text(encoding="utf-8"), image)
    assert ":latest" not in block, f"{image} must be pinned"


def test_the_edge_image_is_pinned_by_digest() -> None:
    """A tag is a name the registry may repoint; the digest is the artifact."""
    caddy = _service_block(BASE.read_text(encoding="utf-8"), "caddy")
    assert re.search(r"image: caddy:[^\s]*@sha256:[0-9a-f]{64}", caddy), (
        "the edge decides which of two processes answers a request — pin it "
        "by digest, not only by tag"
    )


LOCAL = DEPLOY / "compose.local.example.yml"


def test_the_local_overlay_replaces_the_volume_it_means_to_replace() -> None:
    """Same shape rule as the public overlay's ports (audit B-1): without
    `!override` Compose APPENDS, and /data gets both the named volume and the
    bind — silently."""
    mcp = _service_block(LOCAL.read_text(encoding="utf-8"), "mcp")
    assert "volumes: !override" in mcp
    assert ":/data" in mcp


CADDYFILE = DEPLOY / "Caddyfile"


def test_the_edge_routes_every_path_python_owns() -> None:
    """The route table is a contract (frontend-migration.md §1a), and the only
    place it is executable is a file no test in this repo can run. So the shape
    is guarded the way the merge tags above are: a path dropped from this
    matcher is a path served by the front end, which has no route for it.
    """
    text = _strip_comments(CADDYFILE.read_text(encoding="utf-8"))
    matcher = re.search(r"^\s*@python path (.+)$", text, re.M)
    assert matcher, "the @python matcher is the whole of §1a"
    paths = set(matcher.group(1).split())
    for path in (
        "/api/*",
        "/frames/*",
        "/mcp*",
        "/auth/*",
        "/.well-known/*",
        "/healthz",
        "/dashboard/api/*",
        "/dashboard/logout",
        "/videos/*/export.md",
        # The SDK registers these at the root under oauth, not under /auth/
        # (auth/modes.py's create_auth_routes) — frontend-migration.md §10.
        "/authorize",
        "/token",
        "/register",
        "/revoke",
    ):
        assert path in paths, f"{path} is Python's and the edge must route it"


def test_the_edge_routes_dashboard_writes_by_method() -> None:
    """§1d's three collisions — POST /dashboard/{following,index,login} — share
    a path with a page the front end serves, and every other dashboard write is
    a segment deeper. One method matcher over the prefix covers all of them; a
    proxy that resolved any of them on path alone would answer a write with a
    document and nothing would say so.
    """
    text = _strip_comments(CADDYFILE.read_text(encoding="utf-8"))
    block = re.search(r"@dashboard_writes \{(.+?)\}", text, re.S)
    assert block, "the method split is what nothing else in the repo can express"
    body = block.group(1).split()
    assert "method" in body and "POST" in body
    assert "/dashboard" in body and "/dashboard/*" in body


def test_the_edge_sends_no_document_headers_of_its_own() -> None:
    """The CSP and its three companions are `web/src/proxy.ts`'s, per request
    and with a nonce the edge cannot see (§1b). One set of headers, one sender.
    """
    text = _strip_comments(CADDYFILE.read_text(encoding="utf-8")).lower()
    for directive in ("content-security-policy", "x-frame-options", "header "):
        assert (
            directive not in text
        ), "the document policy belongs to whatever renders the document"
