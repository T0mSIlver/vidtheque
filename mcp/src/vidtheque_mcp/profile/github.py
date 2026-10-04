"""Current projects from the owner's GitHub repos — companion.md §2.2 (#159).

Part of the nightly update, before the expiry pass. It lists the repos
`VIDTHEQUE_GITHUB_USER` owns that were pushed to in the last 30 days, through
the REST API, and reads only each repo's name, description, topics and main
language: never code, commits or issues. Private repos are listed only with
`VIDTHEQUE_GITHUB_TOKEN`. A repo that hits the deny list is dropped before the
model sees it; one model call names the projects, each topic passes
`topics.check`, and the survivors are added as `kind=project` by
`actor=nightly`. A project whose repo is still active is named again, which
restarts its 30 days.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx2 as httpx

from ..config import ConfigError
from ..llm import Model
from . import store, topics

logger = logging.getLogger(__name__)

API = "https://api.github.com"
ACTIVE_S = 30 * 86_400
MAX_REPOS = 30
DESCRIPTION_CHARS = 200
REASON = "an active GitHub repo"
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")

SYSTEM = (
    "You read the list of a developer's GitHub repositories pushed to in the last "
    "30 days and name what they are building now, as short topics for a video "
    "recommender.\n" + topics.RULES
)


@dataclass(frozen=True)
class GitHubSettings:
    user: str
    token: str | None = None

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "GitHubSettings | None":
        user = env.get("VIDTHEQUE_GITHUB_USER", "").strip()
        if not user:
            return None
        if _LOGIN.fullmatch(user) is None:
            raise ConfigError(f"VIDTHEQUE_GITHUB_USER is not a GitHub login: {user!r}")
        return cls(user=user, token=env.get("VIDTHEQUE_GITHUB_TOKEN", "").strip() or None)


@dataclass
class Pass:
    repos: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)


async def active_repos(
    http: httpx.AsyncClient, settings: GitHubSettings, now: int
) -> list[dict[str, Any]]:
    """The owner's repos pushed to in the last 30 days, newest first, forks and archives left out."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "vidtheque",
    }
    if settings.token:
        # The token's own listing is the one that includes private repos.
        url = f"{API}/user/repos"
        params = {"affiliation": "owner", "sort": "pushed", "per_page": "100"}
        headers["Authorization"] = f"Bearer {settings.token}"
    else:
        url = f"{API}/users/{settings.user}/repos"
        params = {"type": "owner", "sort": "pushed", "per_page": "100"}
    response = await http.get(url, params=params, headers=headers, timeout=30.0)
    response.raise_for_status()
    repos = []
    for repo in response.json():
        owner = str((repo.get("owner") or {}).get("login", ""))
        if owner.casefold() != settings.user.casefold() or repo.get("fork") or repo.get("archived"):
            continue
        pushed = _epoch(repo.get("pushed_at"))
        if pushed is None or now - pushed > ACTIVE_S:
            continue
        repos.append(repo)
    return repos[:MAX_REPOS]


def prompt(repos: list[dict[str, Any]], current: list[str]) -> str:
    lines = [
        "\t".join(
            [
                str(r.get("name", "")),
                str(r.get("language") or "-"),
                ",".join(map(str, r.get("topics") or [])) or "-",
                " ".join(str(r.get("description") or "").split())[:DESCRIPTION_CHARS] or "-",
            ]
        )
        for r in repos
    ]
    projects = "\n".join(f"- {t}" for t in current) or "(none)"
    return (
        f"Current projects:\n{projects}\n\n"
        "Repositories (name, language, topics, description):\n" + "\n".join(lines)
    )


async def run(
    db: Any,
    model: Model,
    http: httpx.AsyncClient,
    settings: GitHubSettings,
    now: int,
    owner_id: int = 1,
) -> Pass:
    """List, name, check and write. Raises on a GitHub or model failure; writes nothing then."""
    done = Pass()
    terms = topics.deny_terms()
    repos = []
    for repo in await active_repos(http, settings, now):
        about = " ".join(
            [str(repo.get("name", "")).replace("-", " ").replace("_", " "),
             str(repo.get("description") or ""), " ".join(map(str, repo.get("topics") or []))]
        )
        hit = topics.denied(about, terms)
        if hit:
            done.skipped.append(f"{repo.get('name')}: hits {hit!r}")
            continue
        repos.append(repo)
    done.repos = [str(r.get("name")) for r in repos]
    if not repos:
        return done
    current = await db.read(
        lambda c: [str(e["text"]) for e in store.entries(c, owner_id, now) if e["kind"] == "project"]
    )
    answer = await model.complete(
        prompt(repos, current), system=SYSTEM, schema=topics.SCHEMA, purpose="github_projects"
    )
    checked = topics.check([str(p["text"]) for p in answer["projects"]], terms)
    done.refused = checked.refused

    def write(conn: sqlite3.Connection) -> None:
        send, out = topics.fit(checked.kept, store.entries(conn, owner_id, now))
        done.refused.extend(out)
        if not send:
            return
        adds = [(t, topics.WEIGHT, "project") for t in send]
        applied = store.apply(
            conn, store.Ops(add=adds, reason=REASON), actor="nightly", owner_id=owner_id, now=now
        )
        done.sent = send
        done.refreshed = applied.refreshed

    await db.write(write)
    return done


def _epoch(raw: Any) -> int | None:
    if not isinstance(raw, str):
        return None
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None
