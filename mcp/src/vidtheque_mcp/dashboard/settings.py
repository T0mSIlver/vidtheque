"""The dashboard's environment, resolved once — `docs/design/dashboard.md` §9.

Separate from :class:`~vidtheque_mcp.config.Settings` for the same reason
``PublicSettings`` is: this is a *mode*, resolved at app-construction time, and
the readers come from ``config`` so the two-spelling convention (bare name,
``VIDTHEQUE_``-prefixed wins) behaves the way ``deploy/.env.example`` promises.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import _bool_env, _int_env

# Not configurable, and that is the decision (dashboard.md §2.6): a movable
# route root breaks every relative link, doubles the test matrix, and buys
# obscurity, which is not a security property.
ROOT = "/dashboard"


@dataclass(frozen=True)
class DashboardSettings:
    """Everything the route group needs."""

    enabled: bool = True
    rate_per_min: int = 120

    @classmethod
    def from_env(cls) -> "DashboardSettings":
        return cls(
            # Default-on is safe because of §3.2 rule 3: in `AUTH=none` the
            # write side is never registered, so the worst a default-on
            # dashboard can do is show the corpus to whoever could already
            # read it through `/mcp` and `/frames`.
            enabled=_bool_env("VIDTHEQUE_DASHBOARD", True),
            rate_per_min=_int_env("VIDTHEQUE_RATE_DASHBOARD_PER_MIN", 120),
        )
