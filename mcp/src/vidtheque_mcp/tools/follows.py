"""`follow-channel` — start, pause, resume, check or stop a following.

Five verbs on one tool rather than five tools, because they are five things to
say about one object, and a model choosing between `pause-follow` and
`unfollow-channel` would be choosing between two names for the same noun. Each
extra tool is permanent context in every session (DECISIONS.md's description
budget is the same argument), and none of these five verbs needs its own.

**Nothing here talks to YouTube.** Creating a follow is a database row, and the
display name is read off the URL — a probe would make `action="follow"` a
network call that can 429 the box before the follow even exists, and the first
check is the request that asks the source anyway. That is also what lets this
tool carry `idempotentHint: true`: following a channel twice returns the first
follow and creates no second row, the way `index-video` returns an existing
`video_id`.

The rules are validated in `follows/params.py`, which the dashboard form shares
(dashboard.md §2.2). This module adds no policy — it decides what the payload
says, and every payload it writes is the same size whatever the input.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs, urlsplit

from mcp_types import CallToolResult

from ..errors import ToolError, bad_param
from ..follows import store
from ..follows.params import build_rules
from ..follows.rules import Rules, describe
from ..pipeline.settings import PipelineSettings
from ..pipeline.sources import is_indexable_url, looks_like_container
from ..text import duration_clock, iso_minute, middle_truncate
from .base import Deps, handle_errors, text_result

ACTIONS = ("follow", "unfollow", "pause", "resume", "check_now")

# A URL is caller-supplied and unbounded; every line that echoes one is capped,
# for the reason `bad_time` caps the value it rejects (2026-08-10 audit, F-14).
MAX_URL_CHARS = 160
MAX_NAME_CHARS = 60

# What may be *stored*, as opposed to what is echoed. A real channel or playlist
# URL is well under a hundred characters; 2048 is the conventional URL ceiling
# and is generous against every legitimate shape.
MAX_STORED_URL_CHARS = 2048

# `/videos`, `/streams` and `/shorts` name a *tab*, and which tabs a follow
# watches is the `tabs` rule — so they are not part of which channel this is.
# Stripping them is what makes `@handle` and `@handle/videos` one follow rather
# than two rows watching the same channel twice.
_TAB_TAILS = ("/videos", "/streams", "/shorts", "/featured", "/live")

# The rule arguments, and what they mean when the caller did not send them. A
# state action that was handed one of these says so rather than dropping it.
_RULE_DEFAULTS: dict[str, Any] = {
    "title": None,
    "min_duration": None,
    "max_duration": None,
    "tabs": "videos",
    "title_include": None,
    "title_exclude": None,
    "channels": "all",
    "tags": None,
    "backfill": 0,
    "max_per_check": 5,
    "mode": "auto",
    "check_interval_s": None,
}


@handle_errors
async def follow_channel(
    deps: Deps,
    url: str | None = None,
    action: str = "follow",
    title: str | None = None,
    min_duration: str | float | None = None,
    max_duration: str | float | None = None,
    tabs: str = "videos",
    title_include: str | None = None,
    title_exclude: str | None = None,
    channels: str = "all",
    tags: str | None = None,
    backfill: int = 0,
    max_per_check: int = 5,
    mode: str = "auto",
    check_interval_s: int | None = None,
) -> CallToolResult:
    if action not in ACTIONS:
        raise bad_param(
            f"action must be one of {', '.join(ACTIONS)}.",
            'action="follow" starts one; the other four name a follow you already have.',
        )
    needle = (url or "").strip()
    if not needle:
        raise bad_param(
            "url is required for every action.",
            'url="https://youtube.com/@handle" to follow a channel; for the other '
            "actions the follow's slug, its stored URL, or part of its name all resolve.",
        )
    if not deps.db.writes_allowed:
        raise ToolError(
            "E_FEATURE_DISABLED",
            "following is disabled: the corpus config and the vector tables "
            f"disagree ({deps.db.vectors.reason}) — a follow that queued videos "
            "now would index them into a corpus that cannot answer with them.",
            "fix the config/dimension mismatch and restart; search still works.",
        )

    settings = _pipeline_settings()
    if action == "follow":
        rules = build_rules(
            tabs=tabs,
            min_duration=min_duration,
            max_duration=max_duration,
            title_include=title_include,
            title_exclude=title_exclude,
            channels=channels,
            tags=tags,
            backfill=backfill,
            max_per_check=max_per_check,
            mode=mode,
            check_interval_s=check_interval_s,
            # The server's default rather than the dataclass's, so an operator
            # who set VIDTHEQUE_FOLLOW_INTERVAL_S gets it for the follows made
            # here as well as for the ones the dashboard makes.
            default_interval_s=settings.follow_interval_s,
        )
        return await _follow(deps, needle, title, rules, settings)

    row = await deps.db.read(lambda c: store.find(c, needle))
    if row is None:
        raise _unknown_follow(needle)
    note = _ignored_rule_args(
        title=title,
        min_duration=min_duration,
        max_duration=max_duration,
        tabs=tabs,
        title_include=title_include,
        title_exclude=title_exclude,
        channels=channels,
        tags=tags,
        backfill=backfill,
        max_per_check=max_per_check,
        mode=mode,
        check_interval_s=check_interval_s,
    )
    if action == "unfollow":
        return await _unfollow(deps, row, settings, note)
    if action == "pause":
        return await _set_state(deps, row, settings, note, "paused")
    if action == "resume":
        return await _set_state(deps, row, settings, note, "active")
    return await _check_now(deps, row, settings, note)


# ------------------------------------------------------------------- actions


async def _follow(
    deps: Deps, raw: str, title: str | None, rules: Rules, settings: PipelineSettings
) -> CallToolResult:
    source_url = _normalize(raw)
    if not is_indexable_url(source_url):
        raise ToolError(
            "E_UNSUPPORTED_SOURCE",
            f"{middle_truncate(raw, MAX_URL_CHARS)!r} is not a YouTube URL.",
            "supported: youtube.com channel URLs (/@handle, /channel/UC…, /c/…, "
            "/user/…) and playlist URLs.",
        )
    if not looks_like_container(source_url):
        # A check lists a channel or a playlist and judges what it finds. A
        # single video has nothing to list, so following one would be a follow
        # that can never bring anything in.
        shown = middle_truncate(raw, MAX_URL_CHARS)
        raise bad_param(
            f"{shown!r} is a single video, and a follow watches a channel or a "
            "playlist for new uploads.",
            f'index-video url="{shown}" indexes that one video; follow-channel '
            "takes the channel or playlist URL it came from.",
        )

    kind = "playlist" if _is_playlist(source_url) else "channel"
    # A playlist has no tabs — the check lists it once and tags what it finds
    # `videos`. Left alone, `tabs="streams"` on a playlist produced a follow
    # that was structurally dead: every candidate rejected as `skipped_tab`
    # ("from /videos; this follow watches /streams"), forever, discoverable
    # only by reading the ledger. It is narrowed rather than refused, and it
    # prints a `note:` — a filter that cannot apply says so, it never silently
    # narrows (CLAUDE.md).
    tab_note: str | None = None
    if kind == "playlist" and rules.tabs != ("videos",):
        tab_note = (
            "note: tabs are a channel's /videos, /streams and /shorts, and a "
            f"playlist has none — {', '.join('/' + t for t in rules.tabs)} was "
            "not applied. The playlist is listed whole."
        )
        rules = replace(rules, tabs=("videos",))
    name = middle_truncate((title or "").strip() or _display_name(source_url), MAX_NAME_CHARS)

    existing = await deps.db.read(lambda c: store.by_source_url(c, source_url))
    if existing is not None:
        return await _already_following(deps, existing, settings)

    collection_id = await deps.db.write(
        lambda c: store.create(c, title=name, source_url=source_url, kind=kind, rules=rules)
    )
    row = await deps.db.read(lambda c: store.get(c, collection_id))
    if row is None:  # pragma: no cover - the write above just created it
        raise ToolError("E_INTERNAL", "the follow was written but could not be read back.")

    lines = [
        f"Following: {name} ({kind}) — {middle_truncate(source_url, MAX_URL_CHARS)}",
        # No probe ran, so the name is a reading of the URL and nothing more.
        # Saying so costs a line and is cheaper than the request that would
        # make it authoritative; an operator seeing the handle where they
        # expected the channel's own name should know why it is there.
        f'Name: "{name}" was taken from the URL — nothing was fetched. It stays the '
        + ("handle" if kind == "channel" else "playlist id")
        + " until it is renamed.",
        f"Rule: {describe(rules, name=name)}",
    ]
    if tab_note:
        lines.append(tab_note)
    lines.extend(await _state_lines(deps, row, rules, settings))
    nxt = (
        'next: job-status state="active" — the first check is queued as a '
        "follow_check job on the next tick, and nothing is indexed until it runs."
    )
    lines.append(nxt)
    return text_result(
        "\n".join(lines),
        await _structured(deps, "follow", row, rules, settings, nxt, already_following=False),
    )


async def _already_following(
    deps: Deps, row: sqlite3.Row, settings: PipelineSettings
) -> CallToolResult:
    """A second follow of the same URL: the first one back, and no second row.

    The shape `index-video` uses for an already-indexed video — the caller gets
    the object it asked for and nothing was created, which is what makes a
    retry after a dropped connection safe rather than duplicating the follow.
    """
    rules = Rules.from_row(row)
    name = str(row["title"])
    lines = [
        f"Already following: {name} ({row['kind']}) — "
        f"{middle_truncate(str(row['source_url']), MAX_URL_CHARS)}",
        "No second follow was created. The rule below is the stored one; the "
        "arguments in this call did not change it.",
        f"Rule: {describe(rules, name=name)}",
    ]
    lines.extend(await _state_lines(deps, row, rules, settings))
    nxt = (
        f'next: follow-channel url="{row["slug"]}" action="check_now" to look for '
        'new uploads sooner, or action="unfollow" to stop.'
    )
    lines.append(nxt)
    return text_result(
        "\n".join(lines),
        await _structured(deps, "follow", row, rules, settings, nxt, already_following=True),
    )


async def _unfollow(
    deps: Deps, row: sqlite3.Row, settings: PipelineSettings, note: str | None
) -> CallToolResult:
    collection_id = int(row["collection_id"])
    rules = Rules.from_row(row)
    name = str(row["title"])
    counts = await deps.db.read(lambda c: store.counts(c, collection_id))
    brought_in = int(counts.get("queued", 0))
    await deps.db.write(lambda c: store.delete(c, collection_id))

    lines = [
        f"Unfollowed: {name} — the rule is gone and no further check will run.",
        f"The {brought_in} video(s) it brought in stay in the corpus and stay "
        "searchable; only the following stops. Its indexing jobs keep their "
        "history too.",
        f"The rule that was removed: {describe(rules, name=name)}",
        _budget_line(await _spent(deps), settings),
    ]
    if note:
        lines.append(note)
    nxt = (
        "next: corpus-summary include_follows=true for what is still followed, or "
        f'follow-channel url="{middle_truncate(str(row["source_url"]), MAX_URL_CHARS)}" '
        "to start again."
    )
    lines.append(nxt)
    return text_result(
        "\n".join(lines),
        {
            "action": "unfollow",
            "follow": _follow_fields(row),
            "rule": describe(rules, name=name),
            "unfollowed": True,
            "videos_kept": brought_in,
            "budget": await _budget(deps, settings),
            "next": nxt,
        },
    )


async def _set_state(
    deps: Deps, row: sqlite3.Row, settings: PipelineSettings, note: str | None, state: str
) -> CallToolResult:
    collection_id = int(row["collection_id"])
    await deps.db.write(lambda c: store.set_state(c, collection_id, state))
    # Re-read rather than describe the row we changed: `set_state` also moves
    # the clock when it resumes, and a payload that printed the pre-write row
    # would say the old `next_check_at` in the same breath as the new state.
    row = await _reread(deps, row, collection_id)
    name = str(row["title"])
    rules = Rules.from_row(row)
    if state == "paused":
        lines = [
            f"Paused: {name} — no check runs until you resume it. Nothing already "
            "indexed is affected, and the rule is kept as it is.",
            f"Rule (kept): {describe(rules, name=name)}",
        ]
        action, suggest = "pause", "resume"
    else:
        lines = [
            f"Resumed: {name} — active again, with the clock re-armed to now.",
            # `set_state` does this deliberately, and the payload says it
            # because the alternative an operator would assume — that a
            # fortnight paused is a fortnight of checks owed — is exactly the
            # catch-up burst the daily budget exists to prevent.
            "A pause owes no back-checks: the next check looks at what is on the "
            "channel now, not at everything published while it was paused.",
            f"Rule: {describe(rules, name=name)}",
        ]
        action, suggest = "resume", "check_now"
    return await _state_result(deps, row, rules, settings, note, lines, action, suggest)


async def _check_now(
    deps: Deps, row: sqlite3.Row, settings: PipelineSettings, note: str | None
) -> CallToolResult:
    collection_id = int(row["collection_id"])
    name = str(row["title"])
    if str(row["state"]) in ("paused", "failing"):
        # `store.check_now` arms an *active* follow and nothing else, and the
        # scheduler only ever enqueues an active one — so saying a check was
        # scheduled would be a claim the database contradicts. `failing` is here
        # for the same reason as `paused` and it is the easier one to get wrong:
        # a channel that 404'd once leaves the follow failing, and "check now"
        # is exactly the gesture an operator makes after fixing the URL. It has
        # to say that resume is what re-arms it, rather than silently doing
        # nothing while printing a time.
        paused = str(row["state"]) == "paused"
        rules = Rules.from_row(row)
        lines = [
            f"Not scheduled: {name} is {row['state']}, and a {row['state']} follow is "
            "never checked. Nothing was queued.",
            f"Rule (kept): {describe(rules, name=name)}",
        ]
        if not paused:
            lines.insert(
                1,
                'Last error: '
                f"{middle_truncate(str(row['last_error_message'] or 'not recorded'), 200)}",
            )
        return await _state_result(
            deps, row, rules, settings, note, lines, "check_now", "resume", scheduled=False
        )

    in_flight = await deps.db.read(lambda c: store.check_in_flight(c, collection_id))
    await deps.db.write(lambda c: store.check_now(c, collection_id))
    row = await _reread(deps, row, collection_id)
    rules = Rules.from_row(row)
    lines = [
        f"Due now: {name} is marked due, and the scheduler queues its check on the "
        "next tick. It has not run yet and nothing has been fetched.",
    ]
    if in_flight is not None:
        lines.append(
            f"A check for this follow is already queued or running as "
            f"{in_flight['public_id']}; that one does the work."
        )
    lines.append(f"Rule: {describe(rules, name=name)}")
    return await _state_result(
        deps, row, rules, settings, note, lines, "check_now", "check_now", scheduled=True
    )


async def _state_result(
    deps: Deps,
    row: sqlite3.Row,
    rules: Rules,
    settings: PipelineSettings,
    note: str | None,
    lines: list[str],
    action: str,
    suggest: str,
    scheduled: bool | None = None,
) -> CallToolResult:
    """The tail every state action shares: state, budget, the note, the `next:`."""
    body = list(lines)
    body.extend(await _state_lines(deps, row, rules, settings))
    if note:
        body.append(note)
    nxt = f'next: follow-channel url="{row["slug"]}" action="{suggest}"'
    nxt += (
        ' — or job-status state="active" to watch the check itself.'
        if suggest == "check_now"
        else " when you want it going again."
    )
    body.append(nxt)
    structured = await _structured(deps, action, row, rules, settings, nxt)
    if scheduled is not None:
        structured["scheduled"] = scheduled
    return text_result("\n".join(body), structured)


async def _reread(deps: Deps, row: sqlite3.Row, collection_id: int) -> sqlite3.Row:
    fresh = await deps.db.read(lambda c: store.get(c, collection_id))
    return fresh if fresh is not None else row


# --------------------------------------------------------------- the trimmings


async def _state_lines(
    deps: Deps, row: sqlite3.Row, rules: Rules, settings: PipelineSettings
) -> list[str]:
    """State, mode, the two clocks, the corpus-wide budget. Always this size."""
    # A paused follow has a `next_check_at` and it means nothing — `due` filters
    # on `state = 'active'`. Printing the timestamp anyway would be the payload
    # promising a check the scheduler will never make.
    due = "—" if row["state"] == "paused" else iso_minute(row["next_check_at"])
    lines = [
        f"State: {row['state']} · {_every(rules)} · next check {due} · last check "
        f"{iso_minute(row['last_sync_at']) if row['last_sync_at'] else 'never'}",
    ]
    if rules.mode == "review":
        lines.append(
            "Mode: review — candidates are held for you and nothing is queued "
            "until you release them."
        )
    lines.append(_budget_line(await _spent(deps), settings))
    # A follow stored on a box that runs no check is stored and idle, and only
    # this server knows that. Printing it here is the same honesty `job-status`
    # owes a deferred job: the row exists, the work does not.
    if not settings.follow_checks:
        lines.append(
            "note: VIDTHEQUE_FOLLOW_CHECKS is off on this server, so no check "
            "will run until it is turned back on."
        )
    elif not deps.settings.run_pipeline:
        lines.append(
            "note: this server runs no pipeline, so nothing claims a check here "
            "— the follow is stored and idle."
        )
    return lines


def _budget_line(spent_s: float, settings: PipelineSettings) -> str:
    spent_h = spent_s / 3600.0
    if settings.follow_daily_hours <= 0:
        return (
            f"Budget: {spent_h:.1f}h of video accepted in the last 24h across every "
            "follow — no ceiling is set (VIDTHEQUE_FOLLOW_DAILY_HOURS=0)."
        )
    return (
        f"Budget: {spent_h:.1f}h of {settings.follow_daily_hours:.1f}h accepted in the "
        "last 24h, across every follow together. Over it a candidate is held and "
        "reconsidered on the next check, never dropped."
    )


async def _spent(deps: Deps) -> float:
    return float(await deps.db.read(store.budget_spent_s))


async def _budget(deps: Deps, settings: PipelineSettings) -> dict[str, Any]:
    return {
        "spent_hours": round(await _spent(deps) / 3600.0, 2),
        "daily_hours": settings.follow_daily_hours,
    }


async def _structured(
    deps: Deps,
    action: str,
    row: sqlite3.Row,
    rules: Rules,
    settings: PipelineSettings,
    nxt: str,
    already_following: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": action,
        "follow": _follow_fields(row),
        # The sentence, not the eleven columns behind it: a client reading only
        # `structuredContent` should be able to show the rule without owning a
        # renderer for it.
        "rule": describe(rules, name=str(row["title"])),
        "budget": await _budget(deps, settings),
        "next": nxt,
    }
    if already_following is not None:
        payload["already_following"] = already_following
    return payload


def _follow_fields(row: sqlite3.Row) -> dict[str, Any]:
    """The follow itself, as a structured-only client sees it.

    The URL is truncated here as it is in the text lines. `structuredContent` is
    a payload like any other and the same budget binds it — a row written before
    the intake clamp existed would otherwise carry its whole length into every
    response that names the follow.
    """
    return {
        "slug": str(row["slug"]),
        "title": middle_truncate(str(row["title"]), MAX_NAME_CHARS),
        "kind": str(row["kind"]),
        "source_url": middle_truncate(str(row["source_url"]), MAX_URL_CHARS),
        "state": str(row["state"]),
        "mode": str(row["mode"]),
        "tabs": str(row["tabs"]),
        "check_interval_s": int(row["check_interval_s"]),
        "next_check_at": iso_minute(row["next_check_at"]),
        "last_check_at": iso_minute(row["last_sync_at"]) if row["last_sync_at"] else None,
    }


def _unknown_follow(needle: str) -> ToolError:
    shown = middle_truncate(needle, MAX_URL_CHARS)
    return ToolError(
        "E_UNKNOWN_FOLLOW",
        f"No follow matches {shown!r} — it was tried as a slug, as the stored "
        "source URL, and as part of a name.",
        "corpus-summary include_follows=true lists what is followed, or "
        'follow-channel url="…" action="follow" starts a new one.',
    )


def _ignored_rule_args(**passed: Any) -> str | None:
    """A `note:` for rule arguments the action cannot apply — never a silent drop.

    The same rule as a search leg that could not run: the payload says what was
    not applied rather than letting the caller believe it was.
    """
    named = [name for name, default in _RULE_DEFAULTS.items() if passed.get(name) != default]
    if not named:
        return None
    return (
        f"note: {', '.join(named[:10])} {'were' if len(named) > 1 else 'was'} ignored "
        "— this action changes the follow's state, not its rule. The rule printed "
        "above is the stored one, unchanged."
    )


def _normalize(raw: str) -> str:
    """The URL as it will be stored, so two spellings are one follow.

    Bounded before anything is done with it. `is_indexable_url` checks the host
    and `looks_like_container` looks for a marker, so a hundred kilobytes of
    padding after `youtube.com/@` passed both — and a follow stores its URL
    forever and echoes it in every payload that names the follow, including
    `corpus-summary`. The clamp is a refusal rather than a truncation because a
    truncated URL is a different URL, and this one is a key.
    """
    candidate = raw.strip()
    if len(candidate) > MAX_STORED_URL_CHARS:
        raise bad_param(
            f"that URL is {len(candidate)} characters; the limit is "
            f"{MAX_STORED_URL_CHARS}.",
            "a channel or playlist URL is short — check for a pasted "
            "duplicate or a tracking payload.",
        )
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    candidate = candidate.split("#", 1)[0].rstrip("/")
    lowered = candidate.lower()
    for tail in _TAB_TAILS:
        if lowered.endswith(tail):
            return candidate[: -len(tail)]
    return candidate


def _is_playlist(url: str) -> bool:
    lowered = url.lower()
    return "/playlist" in lowered or "list=" in lowered


def _display_name(url: str) -> str:
    """The name to show, read off the URL because nothing here probes.

    A handle is what a person recognises, so it wins over the rest of the path;
    a playlist has no handle and is named by the id its URL carries.
    """
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    for segment in segments:
        if segment.startswith("@"):
            return segment
    listed = parse_qs(parts.query).get("list")
    if listed and listed[0]:
        return f"playlist {listed[0]}"
    if segments:
        return segments[-1]
    return parts.netloc or "follow"


def _pipeline_settings() -> PipelineSettings:
    # `Deps.settings` is the *server's* config; the daily budget and the default
    # interval belong to the pipeline settings, which `app.py` builds for the
    # runner alone and never puts on `Deps`. Reading the environment here is one
    # cheap call; a new `Deps` field would be carried by nine tools that have no
    # use for it.
    return PipelineSettings.from_env()


def _every(rules: Rules) -> str:
    """`every 6h` / `every 45min` — the clock, short enough to sit in a line."""
    if rules.check_interval_s % 3600 == 0:
        return f"every {rules.check_interval_s // 3600}h"
    return f"every {max(1, rules.check_interval_s // 60)}min"


def brief_rule(rules: Rules) -> str:
    """The rule as a clause rather than a sentence — for `corpus-summary`.

    `describe` leads with the follow's name, which a list has already printed in
    the column beside it. This is the same facts with the subject removed, and
    it lives here so there is still one place that renders a rule for a reader.
    """
    parts = [_every(rules), "/" + ", /".join(rules.tabs), f"≤{rules.max_per_check}/check"]
    if rules.min_duration_s is not None or rules.max_duration_s is not None:
        low = duration_clock(rules.min_duration_s) if rules.min_duration_s else "0:00"
        high = duration_clock(rules.max_duration_s) if rules.max_duration_s else "any"
        parts.append(f"{low}-{high}")
    if rules.title_include:
        parts.append("titled like " + ", ".join(rules.title_include[:3]))
    if rules.title_exclude:
        parts.append("never " + ", ".join(rules.title_exclude[:3]))
    if rules.channels != "all":
        parts.append(f"{rules.channels} only")
    if rules.tags:
        parts.append("tag " + ", ".join(rules.tags[:3]))
    if rules.mode == "review":
        parts.append("held for review")
    return " · ".join(parts)
