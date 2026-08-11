"""The dashboard's environment, resolved once — `docs/design/dashboard.md` §9.

Separate from :class:`~vidtheque_mcp.config.Settings` for the same reason
``PublicSettings`` is: this is a *mode*, resolved at app-construction time, and
the readers come from ``config`` so the two-spelling convention (bare name,
``VIDTHEQUE_``-prefixed wins) behaves the way ``deploy/.env.example`` promises.
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass

from ..config import _LOOPBACK_HOSTS, ConfigError, _bool_env, _env, _int_env

logger = logging.getLogger(__name__)

# Not configurable, and that is the decision (dashboard.md §2.6): a movable
# route root breaks every relative link, doubles the test matrix, and buys
# obscurity, which is not a security property.
ROOT = "/dashboard"


@dataclass(frozen=True)
class DashboardSettings:
    """Everything the route group needs."""

    enabled: bool = True
    rate_per_min: int = 120
    # Peer networks that may write without a credential. **Empty by default**
    # and deliberately so (§3.4): RFC1918-on-by-default silently grants
    # indexing to everyone on whatever network the box happens to be on, and
    # that is not a default to set on someone else's behalf.
    trusted_cidrs: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()

    def trusts(self, peer: str | None) -> bool:
        """Is this **socket peer** inside a configured network?

        The argument is always ``request.client.host`` — the address the kernel
        reports — and never ``VIDTHEQUE_TRUSTED_IP_HEADER``. That header is
        documented as trust-on-configuration (demo-site.md §4.3), which is
        correct for rate-limit bucketing and disqualifying for authorization:
        any client can send it, so a header-based allowlist is an allowlist of
        everybody.
        """
        if not self.trusted_cidrs or not peer:
            return False
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:  # a unix socket, or a test transport with no peer
            return False
        return any(address in network for network in self.trusted_cidrs)

    @classmethod
    def from_env(cls) -> "DashboardSettings":
        return cls(
            # Default-on is safe because of §3.2 rule 3: in `AUTH=none` the
            # write side is never registered, so the worst a default-on
            # dashboard can do is show the corpus to whoever could already
            # read it through `/mcp` and `/frames`.
            enabled=_bool_env("VIDTHEQUE_DASHBOARD", True),
            rate_per_min=_int_env("VIDTHEQUE_RATE_DASHBOARD_PER_MIN", 120),
            trusted_cidrs=_cidrs("VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS"),
        )


# The networks a reverse proxy or a tunnel connector speaks *from* when it sits
# on the same box or the same docker bridge: loopback, the RFC1918 ranges and
# IPv6 unique-local. `172.17.0.0/16`, docker's default bridge, is inside
# `172.16.0.0/12`.
_PROXY_ORIGIN_NETWORKS = tuple(
    ipaddress.ip_network(entry)
    for entry in (
        "127.0.0.0/8",
        "::1/128",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "fc00::/7",
    )
)


def proxy_origin_cidrs(settings: DashboardSettings) -> tuple[str, ...]:
    """Trusted networks a proxy in front of this server could be speaking from."""
    return tuple(
        str(network)
        for network in settings.trusted_cidrs
        if any(network.overlaps(origin) for origin in _PROXY_ORIGIN_NETWORKS)
    )


def refuse_proxy_origin_cidrs(
    settings: DashboardSettings,
    trusted_ip_header: str,
    public_hostnames: tuple[str, ...] = (),
) -> None:
    """Refuse to boot when the allowlist may be describing the proxy (§3.4).

    ``trusts()`` reads the **socket peer**, which is the right answer for a
    server a LAN talks to directly and the wrong shape for one behind a tunnel:
    cloudflared connects over loopback (or a docker bridge), so behind it every
    anonymous visitor on the internet arrives with a trusted peer address, and
    `is_owner` says yes to all of them — owner clamps on `/dashboard/api/*`,
    the full-transcript hatch, and with ``AUTH=token`` the credential-free
    write side too.

    A configured ``VIDTHEQUE_TRUSTED_IP_HEADER`` is the tell that a proxy is in
    front: it exists precisely because the socket peer is *not* the client. So
    the two settings together are the footgun.

    **This was a warning until 2026-08-11 (gate G2).** It refuses now, in the
    shape B-2 established the same night: a misconfiguration that silently
    widens authorization is a boot failure, not a log line, because a log line
    is a control that depends on someone reading it.

    **Three conditions, and the third is the one that took a second attempt.**
    The header alone is not the signal it looks like: ``trusted_ip_header``
    *defaults* to ``CF-Connecting-IP`` and `deploy/.env.example` ships that
    value, so "a header is set" is true of a LAN box that has never heard of a
    proxy. Refusing on the first two conditions alone would take owner access
    away from exactly the deployment §3.2 designed the allowlist for — an
    ``AUTH=none`` LAN box, where the CIDR is the only credential there is.

    So the third condition is a **non-loopback public hostname**, the same test
    `Settings._refuse_anonymous_writes_in_public` uses to mean "this is really
    exposed". A LAN deployment has none and boots untouched; a tunnelled one
    has one, and there the two settings together say *every visitor is the
    owner*.

    No escape-hatch env var, and none is needed: the remedy is to narrow the
    allowlist to a network the proxy cannot speak from, which is a safe
    configuration rather than a suppressed warning.

    Flagged by the 2026-08-09 review; `docs/deploy-public.md` §9 carries the
    operator half.
    """
    if not trusted_ip_header:
        return
    risky = proxy_origin_cidrs(settings)
    if not risky:
        return
    exposed = [h for h in public_hostnames if h not in _LOOPBACK_HOSTS]
    if not exposed:
        return
    raise ConfigError(
        f"VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS contains {', '.join(risky)} while "
        f"VIDTHEQUE_TRUSTED_IP_HEADER={trusted_ip_header} is set and this server "
        f"answers on {', '.join(exposed)}, which means a proxy is in front of "
        "it. Trust is decided on the SOCKET PEER, "
        "so if the proxy connects from one of those networks then every visitor "
        "arriving through it is treated as the owner — owner clamps, full "
        "transcripts, and the credential-free write side. Either narrow the "
        "allowlist to networks the proxy cannot speak from, or empty it and use "
        "the token."
    )


def _cidrs(name: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Parse a comma-separated allowlist, dropping — loudly — what will not parse.

    A typo here must not take the server down (this is an authorization
    *widening*, so failing open on a parse error would be the worst of both),
    and it must not pass silently either: an entry that never matches is a
    write side the operator thinks is reachable and is not.
    """
    networks = []
    for raw in (_env(name, "") or "").split(","):
        entry = raw.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning("%s: %r is not a network, ignoring it", name, entry)
    return tuple(networks)
