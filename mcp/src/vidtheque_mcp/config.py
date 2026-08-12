"""Configuration, read from the environment exactly once.

Every knob here has an entry in ``deploy/.env.example`` — CLAUDE.md says an env
var without one is a bug.

The auth mode is resolved here and nowhere else: the research doc's design rule
is that the mode is *one branch at app-construction time*, not a per-route
conditional sprinkled through the codebase.
"""

from __future__ import annotations

import logging
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


def _clamped_float_env(name: str, default: float, low: float, high: float) -> float:
    """A float env with a server-side clamp — never a prompt-only limit.

    The relevance band is a search *guarantee*, so an operator typo (`2O`, `20`)
    must not be able to switch it off silently: out-of-range values are pulled
    back into the documented range instead.
    """
    return min(high, max(low, _float_env(name, default)))


_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off"})

# The hosts that are not "the internet can reach this".
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})


def _bool_env(name: str, default: bool) -> bool:
    """Parse a boolean, and **refuse** a spelling that is neither.

    This used to coerce anything unrecognised to false, which is the wrong
    direction for every flag that reads it: `VIDTHEQUE_PUBLIC_READONLY=Y` is
    plainly someone asking for read-only mode, and silently answering "false"
    registers the write tools on a public hostname with no complaint anywhere.
    A typo must be a boot failure, not a security posture.

    Both documented spellings still work — the value is stripped and
    lower-cased first, so `True` and a trailing space are fine.
    (2026-08-10 audit, B-2.)
    """
    raw = _env(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE_WORDS:
        return True
    if value in _FALSE_WORDS:
        return False
    raise ConfigError(
        f"{name}={raw!r} is not a boolean. "
        f"Use one of {sorted(_TRUE_WORDS)} or {sorted(_FALSE_WORDS)}."
    )


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
    # closer and these are ceilings. The reasoning is on
    # db.queries.VEC_MAX_DISTANCE.
    #
    # Calibrated 2026-08-10 against the repaired Qwen3-VL-Embedding-2B space
    # (research/vec-floor-calibration-2026-08-10.md §6): real best-hit
    # 0.220-0.459 vs junk 0.579-0.665 on the text leg, which is the first time
    # this project has had a corridor to put an absolute ceiling in. The
    # SigLIP-era values (0.72 / 0.96) are documented on the queries constants
    # for anyone still running that pair.
    vec_max_distance: float = 0.55
    frame_max_distance: float = 0.65

    # The floor that actually binds, and the reason the two above may stay open:
    # a margin over the query's OWN nearest hit, which needs no knowledge of the
    # radius at which a given model packs its corpus. Grounded on the one pair
    # we have calibrated — see db.queries.VEC_MAX_MARGIN — and clamped to
    # [0, 2] on the way in, because a mistyped env is not a licence to return
    # the whole corpus.
    vec_max_margin: float = 0.20
    frame_max_margin: float = 0.15

    # Whether this box runs the job runner at all. Off is for boxes that only
    # serve: read-only mode masks the write *tools*, not the runner, and a
    # migrated database with queued rows would otherwise be resumed here.
    run_pipeline: bool = True

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
            # One cookie, one table, one lifetime — so one env var, whichever
            # of the two pages minted it (dashboard.md §9). It is named for the
            # dashboard because that is the surface a human keeps open; the
            # OAuth consent screen's session is the same row and honours the
            # same number rather than a second one nobody would think to set.
            login_session_ttl_s=_int_env("VIDTHEQUE_DASHBOARD_SESSION_TTL_S", 12 * 3_600),
            response_max_chars=_int_env("VIDTHEQUE_RESPONSE_MAX_CHARS", 60_000),
            candidate_cap=_int_env("VIDTHEQUE_CANDIDATE_CAP", 5_000),
            vec_max_distance=_float_env("VIDTHEQUE_VEC_MAX_DISTANCE", 0.55),
            frame_max_distance=_float_env("VIDTHEQUE_FRAME_MAX_DISTANCE", 0.65),
            vec_max_margin=_clamped_float_env("VIDTHEQUE_VEC_MAX_MARGIN", 0.20, 0.0, 2.0),
            frame_max_margin=_clamped_float_env(
                "VIDTHEQUE_FRAME_MAX_MARGIN", 0.15, 0.0, 2.0
            ),
            deeplink_lead_s=_int_env("VIDTHEQUE_DEEPLINK_LEAD", 2),
            run_pipeline=_bool_env("VIDTHEQUE_RUN_PIPELINE", True),
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
        self._refuse_anonymous_writes_in_public()

    def _refuse_anonymous_writes_in_public(self) -> None:
        """No credential, writes registered, and a public hostname: refuse.

        That combination has no legitimate use. It is what a missing compose
        overlay produces, and the failure is silent in the worst possible
        direction — `index-video` and `tag-video` registered for anyone who
        finds the URL, which is also an unrestricted SSRF probe into whatever
        network the box sits on. Nothing in the app noticed; the runbook
        documented it and documentation is not a control.

        The escape hatch is deliberate and one step: indexing genuinely does
        need the write tools, and the documented workflow (stop the tunnel,
        flip the flag, index, flip it back) would otherwise refuse to boot
        while the hostname is still configured. `VIDTHEQUE_ALLOW_PUBLIC_WRITES=1`
        says "I know, the tunnel is down" out loud, which is the difference
        between a decision and an accident. (2026-08-10 audit, B-2.)
        """
        if self.auth_mode != "none":
            return
        if _bool_env("VIDTHEQUE_PUBLIC_READONLY", False):
            return
        if _bool_env("VIDTHEQUE_ALLOW_PUBLIC_WRITES", False):
            logging.getLogger(__name__).warning(
                "VIDTHEQUE_ALLOW_PUBLIC_WRITES is set: the write tools are "
                "registered with no credential in front of them, on %s. Make "
                "sure the tunnel is stopped.",
                ", ".join(self.public_hostnames),
            )
            return
        public = [h for h in self.public_hostnames if h not in _LOOPBACK_HOSTS]
        if not public:
            return
        raise ConfigError(
            f"VIDTHEQUE_AUTH=none with writes enabled on a public hostname "
            f"({', '.join(public)}) registers index-video and tag-video for "
            "anyone who finds the URL. Set VIDTHEQUE_PUBLIC_READONLY=1 for a "
            "demo deployment, or VIDTHEQUE_ALLOW_PUBLIC_WRITES=1 if the tunnel "
            "is stopped and you are indexing."
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
