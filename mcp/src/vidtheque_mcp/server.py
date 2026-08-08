"""The `MCPServer` instance: tools, resources, and the auth wiring.

We pass ``token_verifier=`` (resource-server role) and never
``auth_server_provider=``. That is deliberate: the SDK's own docs say new
servers should not reach for the embedded-AS constructor, and wiring the
provider through it would also register the SDK's
``/.well-known/oauth-authorization-server`` — the one whose metadata omits the
two fields CIMD selection depends on. We mount the SDK's *handlers* on our own
root app instead (``auth/modes.py``).
"""

from __future__ import annotations

from mcp.server import MCPServer

from . import __version__
from .auth.modes import AuthBundle
from .config import Settings
from .tools import Deps, register


def build_mcp_server(
    settings: Settings,
    deps: Deps,
    auth: AuthBundle,
    hidden_tools: frozenset[str] = frozenset(),
) -> MCPServer:
    """``hidden_tools`` are never registered — the read-only public deployment."""
    mcp = MCPServer(
        name="vidtheque",
        title="vidtheque",
        description=(
            "A searchable multimodal index of videos you have watched: transcripts, "
            "on-screen text and frame imagery, with timestamped deep links."
        ),
        instructions=(
            "Start with corpus-summary, then search, then drill down with "
            "video-summary and get-segment-context. Read vidtheque://guide for "
            "the shared rules; never fabricate video, frame or cue ids."
        ),
        version=__version__,
        website_url="https://github.com/T0mSIlver/vidtheque",
        token_verifier=auth.token_verifier,
        auth=auth.auth_settings,
        log_level=settings.log_level.upper(),  # type: ignore[arg-type]
    )
    register(mcp, deps, hidden_tools)
    return mcp
