"""The public demo's environment, resolved once — `docs/design/demo-site.md` §1.

Separate from :class:`~vidtheque_mcp.config.Settings` because this is a *mode*
that most deployments never turn on, and folding a dozen demo knobs into the
core settings object would make the private server carry them for nothing. The
two-spelling convention (bare name, `VIDTHEQUE_`-prefixed wins) is shared: the
readers come from ``config`` rather than being re-implemented here, so a
`VIDTHEQUE_OPENROUTER_MODEL` behaves the way `deploy/.env.example` promises.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..config import _bool_env, _env, _float_env, _int_env

# Verified against https://openrouter.ai/api/v1/models on 2026-08-08: no
# `deepseek/*:free` model exists on OpenRouter any more, so the brief's "current
# free DeepSeek" has no id to pin. Of the fourteen `:free` ids that day, this is
# the one with tool-calling support, a 131k context and a year-stable id.
# demo-site.md §3.1 lists the alternatives; swapping is an env change.
DEFAULT_MODEL = "deepseek/deepseek-v4-flash-0731"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

REPO_URL = "https://github.com/T0mSIlver/vidtheque"


@dataclass(frozen=True)
class PublicSettings:
    """Everything the public surface needs. Built only when `enabled` is true."""

    enabled: bool = False

    # ---------------------------------------------------------------- ask
    openrouter_key: str | None = None
    openrouter_model: str = DEFAULT_MODEL
    openrouter_base_url: str = DEFAULT_BASE_URL
    ask_max_rounds: int = 4
    ask_timeout_s: float = 90.0

    # ---------------------------------------------------------------- limits
    search_per_min: int = 30
    ask_per_min: int = 5
    ask_per_day: int = 50
    frames_per_min: int = 120
    # `/mcp`, per IP. Loose on purpose: one question answered by somebody's
    # agent is legitimately a burst of tool calls, and this is the product
    # rather than a page. It exists because the alternative was no ceiling at
    # all on the surface that reaches the GPU (2026-08-10 audit, F-1).
    mcp_per_min: int = 120
    rate_max_keys: int = 10_000

    # Trust-on-configuration: behind Cloudflare the edge overwrites this header,
    # so it is authoritative. Exposed directly, a client can forge it — set the
    # var to empty and the socket address is used instead (demo-site.md §4.3).
    trusted_ip_header: str = "CF-Connecting-IP"

    @property
    def ask_enabled(self) -> bool:
        return bool(self.openrouter_key)

    @classmethod
    def from_env(cls) -> "PublicSettings":
        return cls(
            enabled=_bool_env("VIDTHEQUE_PUBLIC_READONLY", False),
            openrouter_key=_env("OPENROUTER_API_KEY"),
            openrouter_model=_env("OPENROUTER_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL,
            openrouter_base_url=(
                _env("OPENROUTER_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
            ).rstrip("/"),
            ask_max_rounds=_int_env("VIDTHEQUE_ASK_MAX_ROUNDS", 4),
            ask_timeout_s=_float_env("VIDTHEQUE_ASK_TIMEOUT_S", 90.0),
            search_per_min=_int_env("VIDTHEQUE_RATE_SEARCH_PER_MIN", 30),
            ask_per_min=_int_env("VIDTHEQUE_RATE_ASK_PER_MIN", 5),
            ask_per_day=_int_env("VIDTHEQUE_RATE_ASK_PER_DAY", 50),
            frames_per_min=_int_env("VIDTHEQUE_RATE_FRAMES_PER_MIN", 120),
            mcp_per_min=_int_env("VIDTHEQUE_RATE_MCP_PER_MIN", 120),
            rate_max_keys=_int_env("VIDTHEQUE_RATE_MAX_KEYS", 10_000),
            trusted_ip_header=_trusted_ip_header(),
        )


def _trusted_ip_header() -> str:
    """Read directly, because *empty* is a meaningful value here.

    ``config._env`` treats an empty string as unset and falls back to the
    default — right for a URL, wrong for the one setting whose documented way
    to say "trust nothing but the socket" is to set it empty.
    """
    for name in ("VIDTHEQUE_TRUSTED_IP_HEADER", "TRUSTED_IP_HEADER"):
        if name in os.environ:
            return os.environ[name].strip()
    return "CF-Connecting-IP"
