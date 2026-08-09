"""Configuration, read from the environment exactly once.

Every knob here has an entry in ``deploy/.env.example`` — CLAUDE.md says an env
var without one is a bug.

The auth mode is resolved here and nowhere else: the research doc's design rule
is that the mode is *one branch at app-construction time*, not a per-route
conditional sprinkled through the codebase.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

AuthMode = Literal["none", "token", "oauth"]

READ_SCOPE = "vidtheque:read"
WRITE_SCOPE = "vidtheque:write"
OFFLINE_SCOPE = "offline_access"


class ConfigError(RuntimeError):
    """A configuration mistake that must fail at boot, not at request time."""


def _env(name: str, default: str | None = None) -> str | None:
    """Read `name`, honouring the repo's two-spelling convention.

    deploy/.env.example: a setting accepts the bare name and a
    `VIDTHEQUE_`-prefixed one, and **the prefixed one wins**. The compose file
    relies on this — it passes `VIDTHEQUE_PUBLIC_URL: ${PUBLIC_URL:-…}`.
    """
    candidates = [name] if name.startswith("VIDTHEQUE_") else [f"VIDTHEQUE_{name}", name]
    for candidate in candidates:
        value = os.environ.get(candidate)
        if value:
            return value
    return default


def _int_env(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - operator typo
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _float_env(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:  # pragma: no cover - operator typo
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _bool_env(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Everything the server needs to boot, resolved from the environment."""

    data_dir: Path
    public_url: str
    worker_url: str

    auth_mode: AuthMode = "none"
    static_token: str | None = None
    password: str | None = None
    secret: str = ""

    # DNS-rebinding allowlist. Behind a tunnel the public hostname must be here
    # or every request is 421 Misdirected Request (research doc §3.3).
    public_hostnames: tuple[str, ...] = ()

    frame_url_ttl_s: int = 86_400
    access_token_ttl_s: int = 3_600
    refresh_token_ttl_s: int = 30 * 86_400
    login_session_ttl_s: int = 12 * 3_600

    # Token discipline (tool-surface §3.3, §3.4).
    response_max_chars: int = 60_000
    candidate_cap: int = 5_000
    deeplink_lead_s: int = 2
    count_probe_headroom: int = 30

    # Relevance floors for the two KNN legs — cosine distance, so LOWER is
    # closer and these are ceilings. The reasoning, and why they are
    # deliberately loose, is on db.queries.VEC_MAX_DISTANCE.
    #
    # The measured values (0.72 text / 0.96 frame) belong to the SigLIP 2 +
    # Qwen3-Embedding-0.6B spaces. Migration 0004 moved both legs into
    # Qwen3-VL-Embedding-2B's, where an absolute cosine distance measured
    # elsewhere means nothing — so these sit effectively open until the GPU
    # bench re-measures them, and the old values are documented beside them for
    # anyone still on that pair.
    vec_max_distance: float = 1.0
    frame_max_distance: float = 1.0

    # Crash recovery (index-schema §1.9). A claim quieter than this belonged to
    # a process that is gone; the runner requeues it and resumes per stage.
    stale_claim_s: int = 300

    # Admission control and cancellation (index-schema §5.3, §5.4).
    query_timeout_s: float = 30.0
    max_concurrent_searches: int = 2
    read_pool_size: int = 4

    # get-frames inline budget, independent of `limit` (tool-surface §4.6).
    inline_frame_max: int = 4
    inline_frame_bytes: int = 6 * 1024 * 1024

    # The `derived/` variant cache (index-schema §6) and how long a client may
    # keep a frame. Both disposable: the cache is a directory you can delete,
    # the header is bounded by the signed URL's own expiry.
    derived_cache_mb: int = 256
    frame_cache_max_age_s: int = 86_400

    timezone: str = "UTC"
    log_level: str = "info"

    # Populated by __post_init__.
    _resolved: dict[str, object] = field(default_factory=dict, repr=False, compare=False)

    # ---------------------------------------------------------------- paths

    @property
    def db_path(self) -> Path:
        return self.data_dir / "vidtheque.db"

    @property
    def auth_db_path(self) -> Path:
        """Auth lives in its own file so a corpus rebuild never touches credentials."""
        return self.data_dir / "auth.db"

    @property
    def keyframes_dir(self) -> Path:
        return self.data_dir / "keyframes"

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"

    # ---------------------------------------------------------------- urls

    @property
    def issuer_url(self) -> str:
        return self.public_url.rstrip("/")

    @property
    def resource_url(self) -> str:
        """The PRM `resource` — must byte-match what the user typed into Claude."""
        return f"{self.issuer_url}/mcp"

    @property
    def allowed_hosts(self) -> list[str]:
        hosts: list[str] = ["127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost"]
        for host in self.public_hostnames:
            if host not in hosts:
                hosts.append(host)
                hosts.append(f"{host}:*")
        return hosts

    @property
    def scopes_supported(self) -> list[str]:
        return [READ_SCOPE, WRITE_SCOPE]

    # ---------------------------------------------------------------- boot

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Settings":
        if environ is not None:  # pragma: no cover - test convenience
            os.environ.update(environ)

        data_dir = Path(_env("VIDTHEQUE_DATA_DIR", "/data") or "/data")
        public_url = (_env("PUBLIC_URL", "http://localhost:8080") or "").rstrip("/")
        mode_raw = (_env("VIDTHEQUE_AUTH", "none") or "none").strip().lower()
        if mode_raw not in {"none", "token", "oauth"}:
            raise ConfigError(
                f"VIDTHEQUE_AUTH must be one of none|token|oauth, got {mode_raw!r}"
            )
        mode: AuthMode = mode_raw  # type: ignore[assignment]

        hostnames = tuple(
            h.strip()
            for h in (_env("VIDTHEQUE_PUBLIC_HOSTNAME", "") or "").split(",")
            if h.strip()
        )

        settings = cls(
            data_dir=data_dir,
            public_url=public_url,
            worker_url=(_env("WORKER_URL", "http://worker:8081") or "").rstrip("/"),
            auth_mode=mode,
            static_token=_env("VIDTHEQUE_TOKEN"),
            password=_env("VIDTHEQUE_PASSWORD"),
            secret=_env("VIDTHEQUE_SECRET", "") or "",
            public_hostnames=hostnames,
            frame_url_ttl_s=_int_env("VIDTHEQUE_FRAME_URL_TTL", 86_400),
            access_token_ttl_s=_int_env("VIDTHEQUE_ACCESS_TOKEN_TTL", 3_600),
            refresh_token_ttl_s=_int_env("VIDTHEQUE_REFRESH_TOKEN_TTL", 30 * 86_400),
            response_max_chars=_int_env("VIDTHEQUE_RESPONSE_MAX_CHARS", 60_000),
            candidate_cap=_int_env("VIDTHEQUE_CANDIDATE_CAP", 5_000),
            vec_max_distance=_float_env("VIDTHEQUE_VEC_MAX_DISTANCE", 1.0),
            frame_max_distance=_float_env("VIDTHEQUE_FRAME_MAX_DISTANCE", 1.0),
            deeplink_lead_s=_int_env("VIDTHEQUE_DEEPLINK_LEAD", 2),
            stale_claim_s=_int_env("VIDTHEQUE_STALE_CLAIM_S", 300),
            query_timeout_s=float(_int_env("VIDTHEQUE_QUERY_TIMEOUT_S", 30)),
            max_concurrent_searches=_int_env("VIDTHEQUE_MAX_CONCURRENT_SEARCHES", 2),
            read_pool_size=_int_env("VIDTHEQUE_READ_POOL_SIZE", 4),
            inline_frame_max=_int_env("VIDTHEQUE_INLINE_FRAME_MAX", 4),
            inline_frame_bytes=_int_env("VIDTHEQUE_INLINE_FRAME_BYTES", 6 * 1024 * 1024),
            derived_cache_mb=_int_env("DERIVED_CACHE_MB", 256),
            frame_cache_max_age_s=_int_env("VIDTHEQUE_FRAME_CACHE_MAX_AGE", 86_400),
            timezone=_env("VIDTHEQUE_TIMEZONE", "UTC") or "UTC",
            log_level=(_env("LOG_LEVEL", "info") or "info").lower(),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        """The failures that otherwise eat an afternoon (research doc §6.5)."""
        if not self.public_url:
            raise ConfigError("PUBLIC_URL must be set")
        if self.auth_mode == "token" and not self.static_token:
            raise ConfigError("VIDTHEQUE_AUTH=token requires VIDTHEQUE_TOKEN")
        if self.auth_mode == "oauth":
            if not self.password:
                raise ConfigError("VIDTHEQUE_AUTH=oauth requires VIDTHEQUE_PASSWORD")
            host = self.public_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
            if not self.public_url.startswith("https://") and host not in {
                "localhost",
                "127.0.0.1",
                "[::1]",
            }:
                raise ConfigError(
                    "VIDTHEQUE_AUTH=oauth requires an https PUBLIC_URL "
                    "(RFC 8414 issuers must be HTTPS outside loopback)"
                )
            if host not in {"localhost", "127.0.0.1", "[::1]"} and host not in self.public_hostnames:
                raise ConfigError(
                    f"VIDTHEQUE_PUBLIC_HOSTNAME must include {host!r}, or the "
                    "transport's DNS-rebinding guard answers 421 to every request"
                )

    def resolve_secret(self) -> str:
        """The signing secret, auto-generated into the data dir on first boot.

        A compose file should need no secret management to get a working
        deployment; an operator who *wants* to manage it sets VIDTHEQUE_SECRET.
        """
        if self.secret:
            return self.secret
        cached = self._resolved.get("secret")
        if isinstance(cached, str):
            return cached
        path = self.data_dir / "secret.key"
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
        else:
            value = secrets.token_urlsafe(48)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value + "\n", encoding="utf-8")
            path.chmod(0o600)
        self._resolved["secret"] = value
        return value
