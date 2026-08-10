"""`POST /api/ask` — the server-side agent loop over OpenRouter (demo-site.md §3).

The mode exists to show a visitor the thing the corpus is *for*: an agent
answering from timestamped evidence. It is a bounded loop against OpenRouter's
OpenAI-compatible ``/chat/completions``, with two internal tools, a hard round
cap, and a forced final answer.

Three properties are load-bearing and tested:

* **Citations cannot be fabricated.** Every result the loop shows the model is
  recorded under an index; the response's citations are drawn from that record,
  and an `[n]` in the prose that names nothing is stripped rather than rendered
  as a dead link.
* **Nothing upstream leaks.** Not the key, not the provider's response body, not
  the status line, not an ``httpx2`` exception string. The operator gets those
  in the log; the visitor gets one of five reasons and "use search".
* **The work is visible while it happens.** The loop is an *event stream*
  (§3.5): every tool call it makes becomes one human-readable line, emitted
  before the tool runs and completed with what the tool actually returned. The
  answer is not streamed — it arrives whole, as the last event. The
  non-streaming POST is the same generator, drained, and the two streaming
  framings (NDJSON and SSE) are the same generator, framed — so no transport
  can answer differently or bill differently from another.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx2 as httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.types import Scope

from ..text import clock, deeplink, middle_truncate
from ..tools import search, segment
from ..tools.base import Deps
from . import humanize
from .ratelimit import refund
from .settings import PublicSettings

logger = logging.getLogger(__name__)

# Two framings, one event vocabulary (§3.5).
#
# NDJSON came first and is the better parse: one JSON object per line, and
# `EventSource` was never on the table because the ask is a POST with a body.
# What it is not is *recognisable*. Cloudflare buffers a proxied response unless
# its content type is `text/event-stream`, so through the tunnel the whole point
# of the stream — ninety seconds of visible work — arrived as ninety seconds of
# silence and then a burst. Measured on Tom's deployment, 2026-08-09.
#
# So SSE is the second framing and the page's default: same events, same JSON,
# `event:`/`data:` around it, negotiated by `Accept`. Both are read by the same
# `fetch` reader on the page — SSE here is a wire format, not `EventSource`.
NDJSON_MEDIA_TYPE = "application/x-ndjson"
SSE_MEDIA_TYPE = "text/event-stream"

MAX_QUESTION_CHARS = 400
MAX_TOOL_CALLS_PER_ROUND = 6

# The facade's bounds, tighter than the MCP defaults and enforced server-side:
# the model cannot ask for more.
ASK_SEARCH_LIMIT = 6
ASK_SEARCH_TEXT_CHARS = 300
ASK_SEARCH_PER_VIDEO = 2
ASK_CONTEXT_WINDOW = 45.0
ASK_CONTEXT_TEXT_CHARS = 1200

SYSTEM_PROMPT = (
    "You answer questions about a personal video corpus using only the tools "
    "provided. Never answer from your own knowledge: if the tools return "
    "nothing useful, say the corpus does not cover it. Search first; use "
    "get_segment_context when a hit needs its surrounding sentences. Every "
    "claim must carry the [n] marker of the search result it came from. "
    # Each hit says which channel it came from, and the three are different
    # kinds of evidence. Encouraged, not templated: an answer that says "the
    # slide reads" where the slide is the source is worth more than one that
    # flattens speech, screen text and imagery into a single voice — and a
    # frame is the case that goes wrong silently, because there is nothing in
    # it to quote.
    "Hits are labelled transcript (said aloud), ocr (on-screen text) or frame "
    "(a visual match): say which in your prose. Describe what a frame shows; "
    "never quote text from one. Keep "
    "the answer under 150 words and write plain prose, no headings."
)

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": (
                "Search the video corpus (transcripts, on-screen text, frame "
                "imagery). Returns numbered hits with video ids and timestamps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for."},
                    "content_type": {
                        "type": "string",
                        "enum": list(search.CONTENT_TYPES),
                        "description": "Which channel to search. Omit for all.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_segment_context",
            "description": (
                "Read the transcript around one moment of one video. Use a "
                "video_id and t from a search hit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "video_id": {"type": "string"},
                    "t": {"type": "number", "description": "Seconds into the video."},
                },
                "required": ["video_id", "t"],
            },
        },
    },
]


class AskUnavailable(Exception):
    """LLM mode cannot serve this request. Carries a reason, never a body."""

    def __init__(self, reason: str, retry_after_s: int = 60) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after_s = retry_after_s


@dataclass
class Billing:
    """Did this ask already buy upstream work? One flag, per request (§4.4).

    The daily budget is refunded for an ask that cost nothing, and *cost* is
    the only honest test. "Did the visitor get an answer" is not the same
    question, and using it was a way to get paid completions for free: the
    first `activity` event is emitted only after a completion came back with a
    tool call in it, so a client that waits for that line, disconnects, and
    repeats spends the model's tokens while the ``finally`` hands the day's
    token back every time. Reported by the 2026-08-09 review.

    So the flag is set by :meth:`OpenRouter.complete` the moment the provider
    answers with a non-error status — that response is a generation, and it is
    billed whether or not the loop ever turns it into prose. An explicit
    refusal (401/403/429/5xx) and a request that never reached the provider at
    all cost nothing and leave the flag alone, which is what keeps a flapping
    free tier from eating the day (§4.4's original reason for existing).

    One caveat, deliberately: a read timeout *after* the provider accepted the
    request may have been billed and is counted as unpaid here, because a
    timeout is far more often a dead upstream than a generated answer. Nobody
    can force one on demand, which is what separates it from the disconnect.
    """

    paid: bool = False


@dataclass
class Citation:
    """One search hit the model was shown, addressable by its `[n]`."""

    n: int
    video_id: str
    title: str
    channel: str | None
    t: int
    link: str | None
    frame_id: str | None = None
    # The evidence the model was shown, carried through so the page's source
    # list reads exactly like a search result instead of a bare title.
    source: str | None = None
    text: str | None = None

    def as_dict(self, thumb: str | None, thumb_large: str | None) -> dict[str, Any]:
        return {
            "n": self.n,
            "video_id": self.video_id,
            "title": self.title,
            "channel": self.channel,
            "t": self.t,
            "timestamp": clock(self.t),
            "link": self.link,
            "thumb": thumb,
            # The Sources list is a list of search rows, so it enlarges like one.
            "thumb_large": thumb_large,
            "source": self.source,
            "text": self.text,
        }


@dataclass
class Evidence:
    """The set of results the model has been shown, in the order it saw them."""

    items: list[Citation] = field(default_factory=list)
    _seen: dict[tuple[str, int], int] = field(default_factory=dict)

    def record(self, hit: dict[str, Any]) -> int:
        """Return the `[n]` for this hit, deduplicated on (video, second)."""
        video_id = str(hit.get("video_id") or "")
        t = int(hit.get("start") or 0)
        key = (video_id, t)
        if key in self._seen:
            return self._seen[key]
        n = len(self.items) + 1
        self._seen[key] = n
        self.items.append(
            Citation(
                n=n,
                video_id=video_id,
                title=str(hit.get("title") or ""),
                channel=hit.get("channel"),
                t=t,
                link=hit.get("link"),
                frame_id=hit.get("frame_id"),
                source=hit.get("source"),
                # The citation is rendered as a search row, so it is humanised
                # like one — the *model* still sees the tool's own text.
                text=humanize.snippet(hit.get("text"), hit.get("source")),
            )
        )
        return n

    def describe(self, video_id: str) -> tuple[str, str | None]:
        """Title and channel already known for a video, from an earlier hit."""
        for item in self.items:
            if item.video_id == video_id:
                return item.title, item.channel
        return "", None


class OpenRouter:
    """The thinnest possible OpenAI-compatible chat client.

    Injectable at the app seam (``build_app(public_http=…)``) so the tests can
    hand it a ``MockTransport`` instead of the internet — the same shape the
    embedding client already uses for the worker.
    """

    def __init__(self, settings: PublicSettings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    async def complete(
        self, body: dict[str, Any], deadline: float, billing: Billing | None = None
    ) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AskUnavailable("upstream_unavailable")
        key = self._settings.openrouter_key
        if not key:  # pragma: no cover - the endpoint checks first
            raise AskUnavailable("not_configured")
        try:
            response = await self._client.post(
                f"{self._settings.openrouter_base_url}/chat/completions",
                json=body,
                headers={
                    "Authorization": f"Bearer {key}",
                    # OpenRouter's attribution headers. Harmless, and they keep
                    # a free-tier key from looking like anonymous scraping.
                    "HTTP-Referer": "https://github.com/T0mSIlver/vidtheque",
                    "X-Title": "vidtheque demo",
                },
                timeout=min(remaining, 60.0),
            )
        except Exception as exc:  # network, TLS, timeout — all the same to a visitor
            logger.warning("ask: upstream request failed: %s", type(exc).__name__)
            raise AskUnavailable("upstream_unavailable") from None

        if response.status_code < 400 and billing is not None:
            # The provider answered. Whatever the loop makes of the body — an
            # answer, a tool call, an unparseable mess — this completion was
            # generated and it is billed, so the refund rule must know.
            billing.paid = True

        if response.status_code in (401, 403):
            logger.warning("ask: upstream rejected the key (%s)", response.status_code)
            raise AskUnavailable("upstream_rejected", retry_after_s=300)
        if response.status_code == 429:
            logger.warning("ask: upstream rate limited")
            raise AskUnavailable("upstream_rate_limited")
        if response.status_code >= 400:
            # 200 chars for the operator; nothing at all for the client. An
            # upstream body is attacker-influenced text and a provider detail.
            logger.warning(
                "ask: upstream %s: %s", response.status_code, response.text[:200]
            )
            raise AskUnavailable("upstream_unavailable")
        try:
            return response.json()
        except ValueError:
            logger.warning("ask: upstream returned a non-JSON body")
            raise AskUnavailable("upstream_unavailable") from None


async def run_ask(
    deps: Deps,
    public: PublicSettings,
    llm: OpenRouter,
    question: str,
    billing: Billing | None = None,
) -> dict[str, Any]:
    """The loop, drained. Returns the answer payload, or raises `AskUnavailable`.

    The POST-and-wait path (§3) is the stream (§3.5) with the activity events
    thrown away, so the two can never answer differently: there is one loop, and
    the only question is whether anybody is watching it work. ``billing`` is
    carried for the same reason — the two transports must not *bill*
    differently either.
    """
    async with aclosing(ask_events(deps, public, llm, question, billing)) as events:
        async for event in events:
            if event.get("event") == "answer":
                return event["payload"]
    # Unreachable: the generator either yields an answer or raises.
    raise AskUnavailable("upstream_unavailable")  # pragma: no cover


async def ask_events(
    deps: Deps,
    public: PublicSettings,
    llm: OpenRouter,
    question: str,
    billing: Billing | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """The loop, as the events a visitor can watch (§3.5).

    Yields one `activity` event per tool call *before* it runs and a second when
    it lands, then exactly one `answer` event carrying the payload the JSON path
    returns. Raises :class:`AskUnavailable` instead of yielding an error, so the
    two transports each say "unavailable" in their own vocabulary — a 503 body,
    or a terminal `error` event — from one raise.

    Nothing here is fabricated: a line is built from the arguments the model
    actually sent and finished with what the tool actually returned.
    """
    deadline = time.monotonic() + public.ask_timeout_s
    evidence = Evidence()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    rounds = 0
    step = 0

    for _ in range(max(1, public.ask_max_rounds)):
        payload = await llm.complete(
            {
                "model": public.openrouter_model,
                "messages": messages,
                "tools": TOOL_SPECS,
                "temperature": 0.2,
            },
            deadline,
            billing,
        )
        message = _first_message(payload)
        calls = message.get("tool_calls") or []
        if not calls:
            content = (message.get("content") or "").strip()
            if content:
                yield {"event": "answer", "payload": _answer(deps, content, evidence, rounds, public)}
                return
            break

        rounds += 1
        batch = _with_ids(calls[:MAX_TOOL_CALLS_PER_ROUND], rounds)
        messages.append(_assistant_turn(message, batch))
        for call in batch:
            step += 1
            # Announced *before* the tool runs — the line is what the model
            # asked for, which is all that is known yet, and the visitor is
            # watching the slow part happen rather than a spinner.
            yield {"event": "activity", "id": step, "phase": "start", "text": _asked(call, evidence)}
            result, summary = await _run_tool(deps, call, evidence)
            messages.append(result)
            yield {"event": "activity", "id": step, "phase": "done", "result": summary}
        if len(calls) > MAX_TOOL_CALLS_PER_ROUND:
            # A parallel-tool-call storm on a free model is how the daily budget
            # dies; the model is told rather than silently short-changed.
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"note: only the first {MAX_TOOL_CALLS_PER_ROUND} tool calls "
                        "of that round were run. Ask for fewer at a time."
                    ),
                }
            )

    # Out of rounds, or an empty answer with no tool calls: one last completion
    # with tools off, so the visitor always gets prose rather than a spinner.
    payload = await llm.complete(
        {
            "model": public.openrouter_model,
            "messages": [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "Answer now from what the tools already returned. Cite "
                        "with [n]. If there is not enough evidence, say so."
                    ),
                },
            ],
            "tool_choice": "none",
            "temperature": 0.2,
        },
        deadline,
        billing,
    )
    content = (_first_message(payload).get("content") or "").strip()
    if not content:
        logger.warning("ask: model produced no answer after %s rounds", rounds)
        raise AskUnavailable("upstream_unavailable")
    yield {"event": "answer", "payload": _answer(deps, content, evidence, rounds, public)}


def _first_message(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices") or []
    if not choices:
        logger.warning("ask: upstream answered with no choices")
        raise AskUnavailable("upstream_unavailable")
    message = choices[0].get("message")
    return message if isinstance(message, dict) else {}


def _with_ids(calls: list[dict[str, Any]], round_no: int) -> list[dict[str, Any]]:
    """Give every tool call an id, and no two the same.

    An OpenAI-compatible upstream requires each `tool` message's
    `tool_call_id` to name exactly one call in the assistant turn before it. A
    model that emits two `search` calls with no ids (the cheap and free tiers
    this demo pins are exactly where that happens) used to produce two tool
    messages both claiming `"search"` — a 400 upstream, which the visitor reads
    as a 503 for a loop that was otherwise fine. Synthesised here, once, so the
    assistant turn and the tool messages carry the same string by construction.
    """
    fixed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, call in enumerate(calls):
        entry = dict(call)
        call_id = str(entry.get("id") or "")
        if not call_id or call_id in seen:
            call_id = f"call_{round_no}_{i}"
        seen.add(call_id)
        entry["id"] = call_id
        fixed.append(entry)
    return fixed


def _assistant_turn(message: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": message.get("content") or "",
        "tool_calls": calls,
    }


# ------------------------------------------------------------ activity lines
#
# One tool call, one line a person can read. Every word of it comes from what
# the model asked for or what the tool answered — there is no phrase here that
# claims something the loop did not do, because the line is the visitor's only
# window onto the ninety seconds the model spends inside the corpus (§3.5).

# The same three words the page uses for the filter chips, so a visitor reads
# "on-screen text" in both places for the same leg.
_CHANNEL = {
    "all": "the corpus",
    "transcript": "the transcript",
    "ocr": "on-screen text",
    "frame": "the frames",
}

# A query or a title in an activity line is corpus/model text on one line: long
# enough to identify what was searched, short enough not to wrap three times.
_QUERY_CHARS = 80
_TITLE_CHARS = 60


def _call_args(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The tool name and its arguments, however mangled the model's JSON is."""
    function = call.get("function") or {}
    name = str(function.get("name") or "")
    try:
        args = json.loads(function.get("arguments") or "{}")
    except ValueError:
        args = {}
    return name, args if isinstance(args, dict) else {}


def _asked(call: dict[str, Any], evidence: Evidence) -> str:
    """What this tool call is about to do, in one line."""
    name, args = _call_args(call)
    if name == "search":
        query = humanize.clip(str(args.get("query") or ""), _QUERY_CHARS)
        content_type = args.get("content_type")
        where = _CHANNEL.get(content_type if content_type in _CHANNEL else "all")
        return f"Searching {where} for “{query}”" if query else f"Searching {where}"
    if name == "get_segment_context":
        video_id = str(args.get("video_id") or "")
        try:
            at = clock(float(args.get("t")))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            at = None
        title = humanize.clip(evidence.describe(video_id)[0], _TITLE_CHARS)
        # The title is only known if an earlier hit carried it; a drill-down
        # into a video the loop has not seen names the id rather than inventing
        # a title for it.
        which = f"“{title}”" if title else f"video {video_id}" if video_id else "a video"
        return f"Reading the transcript around {at} in {which}" if at else (
            f"Reading the transcript in {which}"
        )
    # A tool that does not exist is still work the model did, and hiding it
    # would make the round it happened in look like a stall.
    return f"Asking for “{humanize.clip(name, _TITLE_CHARS)}”" if name else "An empty tool call"


def _hits_summary(hits: list[dict[str, Any]]) -> str:
    """What a search actually found — counted, never estimated."""
    if not hits:
        return "nothing matched"
    talks = len({str(hit.get("video_id") or "") for hit in hits})
    return (
        f"{len(hits)} hit{'' if len(hits) == 1 else 's'} "
        f"in {talks} talk{'' if talks == 1 else 's'}"
    )


async def _run_tool(
    deps: Deps, call: dict[str, Any], evidence: Evidence
) -> tuple[dict[str, Any], str]:
    """Run one internal tool. Returns its `tool` message and one line about it.

    Two consumers, one call: the message is the model's evidence, the line is
    the visitor's. Neither is derived from the other — the model keeps the
    tool's own text, and the line is counted from the same result.
    """
    name, args = _call_args(call)

    if name == "search":
        text, summary = await _tool_search(deps, args, evidence)
    elif name == "get_segment_context":
        text, summary = await _tool_context(deps, args, evidence)
    else:
        text = f"error: no tool named {name!r}. Use search or get_segment_context."
        summary = "no such tool"

    return {
        # `_with_ids` ran over this batch, so the id exists and is unique.
        "role": "tool",
        "tool_call_id": call["id"],
        "name": name,
        "content": text,
    }, summary


async def _tool_search(
    deps: Deps, args: dict[str, Any], evidence: Evidence
) -> tuple[str, str]:
    query = str(args.get("query") or "").strip()[:512]
    if not query:
        return "error: search needs a query.", "the search had no query"
    content_type = args.get("content_type")
    if content_type not in search.CONTENT_TYPES:
        content_type = "all"
    result = await search.run(
        deps,
        q=query,
        content_type=content_type,
        limit=ASK_SEARCH_LIMIT,
        max_per_video=ASK_SEARCH_PER_VIDEO,
        max_text_chars=ASK_SEARCH_TEXT_CHARS,
    )
    if result.is_error:
        payload = result.structured_content or {}
        # The model gets the typed code; the visitor gets the fact that this
        # leg came back empty-handed, without a code to look up.
        return (
            f"error: {payload.get('code')} — {payload.get('message')}",
            "that search could not run",
        )

    hits = (result.structured_content or {}).get("results", [])
    if not hits:
        return f'No results for "{query}". Try different words.', _hits_summary(hits)
    summary = _hits_summary(hits)
    lines = [f'{len(hits)} results for "{query}":']
    for hit in hits:
        n = evidence.record(hit)
        # The label is what makes the prompt's "say which channel" actionable:
        # without it the model is asked to distinguish speech from a slide with
        # nothing in front of it that says which one this line is.
        lines.append(
            f"[{n}] {hit.get('source') or 'transcript'} · {hit.get('title')} — "
            f"{hit.get('channel') or 'unknown'} "
            f"({hit.get('video_id')} at {clock(hit.get('start'))}, t={int(hit.get('start') or 0)})"
        )
        lines.append(f"    {str(hit.get('text') or '')}")
    return "\n".join(lines), summary


async def _tool_context(
    deps: Deps, args: dict[str, Any], evidence: Evidence
) -> tuple[str, str]:
    video_id = str(args.get("video_id") or "").strip()
    if not video_id:
        return (
            "error: get_segment_context needs a video_id from a search hit.",
            "that read named no video",
        )
    try:
        t = float(args.get("t"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return (
            "error: t must be a number of seconds, as given by a search hit.",
            "that read named no moment",
        )
    result = await segment.run(
        deps,
        video_id=video_id,
        t=t,
        window=ASK_CONTEXT_WINDOW,
        include_frame_refs=False,
        max_text_chars=ASK_CONTEXT_TEXT_CHARS,
    )
    payload = result.structured_content or {}
    if result.is_error:
        return (
            f"error: {payload.get('code')} — {payload.get('message')}",
            "that moment could not be read",
        )
    block = result.content[0]
    text = getattr(block, "text", "")

    # The window is evidence the loop showed the model, so it gets an `[n]`
    # like a search hit does. Without one, an answer built from a drill-down —
    # the round where the model did the *most* work — cites a marker that names
    # nothing, gets it stripped, and lands on the page with an empty Sources
    # list. Deduplicated on (video, second) like every other record, so drilling
    # into the hit the model already has reuses that hit's number.
    cues = payload.get("cues") or []
    if cues:
        centre = float(payload.get("t") or t)
        title, channel = evidence.describe(video_id)
        n = evidence.record(
            {
                "video_id": video_id,
                "start": centre,
                "title": title,
                "channel": channel,
                "link": deeplink(video_id, centre, deps.settings.deeplink_lead_s),
                "source": "transcript",
                "text": middle_truncate(
                    " ".join(str(cue.get("text") or "") for cue in cues),
                    ASK_SEARCH_TEXT_CHARS,
                ),
            }
        )
        text = f"This window is [{n}] — cite [{n}] for anything you take from it.\n{text}"
    # Counted from the cues the window actually returned, so "no transcript
    # there" is a fact about the corpus rather than a guess about the read.
    summary = (
        f"{len(cues)} line{'' if len(cues) == 1 else 's'} of transcript"
        if cues
        else "no transcript there"
    )
    return text, summary


# A citation marker *with the horizontal space that flanks it*, so dropping one
# takes the hole with it. `[ \t]` only: a newline is a paragraph break to the
# page's renderer and is never eaten.
_CITATION = re.compile(r"(?P<pre>[ \t]*)\[(?P<n>\d{1,2})\](?P<post>[ \t]*)")

# What a dropped marker must not leave a space in front of.
_TIGHT_AFTER = ",.;:!?)]}\n"


def _last_char(parts: list[str]) -> str:
    for part in reversed(parts):
        if part:
            return part[-1]
    return ""


def _answer(
    deps: Deps,
    content: str,
    evidence: Evidence,
    rounds: int,
    public: PublicSettings,
) -> dict[str, Any]:
    """Strip citations that name nothing, and return only the ones used.

    Stripping is a rewrite, not a deletion: `"the block table [9] is kept"` has
    to come back as `"the block table is kept"` — not with the double space a
    bare deletion leaves — and `"see [9]."` must not become `"see ."`. Models
    emit markers that name nothing often enough for that to show up in a real
    share of answers, and an answer is the thing a visitor screenshots.

    Done as one left-to-right rebuild rather than a `sub()` callback because the
    decision depends on what has already been *written* — two fabricated markers
    in a row (`"text [8][9] end"`) would otherwise each add their own space.
    """
    from .api import FRAME_THUMB_WIDTH, LIGHTBOX_WIDTH, THUMB_WIDTH, thumb_url

    known = {c.n: c for c in evidence.items}
    used: list[int] = []
    out: list[str] = []
    pos = 0

    for match in _CITATION.finditer(content):
        out.append(content[pos : match.start()])
        pos = match.end()
        n = int(match.group("n"))
        if n in known:
            if n not in used:
                used.append(n)
            out.append(match.group(0))  # untouched, the spaces around it included
            continue
        # A marker pointing at nothing is not a link, it is noise. One space is
        # left where it separated two words, and none where the text either side
        # already closes up: a gap already written, punctuation, or an edge.
        tail = content[pos:]
        previous = _last_char(out)
        if not previous or previous in " \t\n":
            continue
        if not tail or tail[0] in _TIGHT_AFTER:
            continue
        if match.group("pre") or match.group("post"):
            out.append(" ")
    out.append(content[pos:])
    cleaned = "".join(out).strip()
    citations = [
        known[n].as_dict(
            thumb_url(
                deps,
                known[n].frame_id,
                FRAME_THUMB_WIDTH if known[n].source == "frame" else THUMB_WIDTH,
            ),
            thumb_url(deps, known[n].frame_id, LIGHTBOX_WIDTH),
        )
        for n in sorted(used)
    ]
    return {
        "answer": cleaned,
        "citations": citations,
        "rounds": rounds,
        "model": public.openrouter_model,
    }


# ------------------------------------------------------------------- endpoint


async def ask_endpoint(request: Request) -> Response:
    """The endpoint, plus the one thing the limiter cannot know on its own.

    The daily budget is charged before this runs (§4), which is right for an
    ask that reaches the model and wrong for one that does not. Launch day is
    exactly when a free tier flaps: without this, one visitor retrying through
    503s spends the whole 50/day in ten minutes and every *other* visitor is
    locked out until the bucket trickles back. So a failure that bought
    **nothing upstream** gives the day's token back.

    "Bought nothing upstream" is the test, not the status code and not the
    answer — see :class:`Billing`. A 400, a `not_configured` 503, an upstream
    that refused or was unreachable: all free, all refunded. A loop that got a
    completion back and then fell over has already spent the money, and giving
    the token back for it is how a retry loop drains a paid budget at no cost
    to whoever is driving it.

    Only ``ask_global`` is refunded. The per-IP minute bucket is the anti-hammer
    guard, not the cost control: someone retrying a broken upstream five times a
    minute should still be slowed down.

    A **stream** is always a 200 — the status line is written before the model
    has done anything — so the refund for that path cannot live here. It lives
    where the outcome is actually known, in :func:`_stream`, under the same
    rule and the same flag. Exactly one of the two runs for any request, so a
    failed stream is refunded once, not twice.
    """
    billing = Billing()
    response = await _ask(request, billing)
    if response.status_code != 200 and not billing.paid:
        refund(request.scope, "ask_global")
    return response


@dataclass(frozen=True)
class Framing:
    """How one event becomes bytes, and what the transport needs before the first.

    The loop, the events and the budget accounting are shared; a framing is the
    only thing that differs between the two streaming variants, which is what
    makes it impossible for them to answer differently or to bill differently.
    """

    media_type: str
    frame: Callable[[dict[str, Any]], bytes]
    preamble: bytes = b""


def _ndjson(event: dict[str, Any]) -> bytes:
    """One event, one line. `json.dumps` escapes every newline in the payload,
    so a corpus title with a line break in it can never split a frame."""
    return (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")


def _sse(event: dict[str, Any]) -> bytes:
    """The same event, SSE-framed: a named event and one `data:` line.

    `data` carries the *whole* object, identical to the NDJSON line, so the page
    parses one shape either way and the vocabulary in §3.5 stays stated once.
    The `event:` line is redundant to a reader that looks at `data.event` — it
    is there because it is what makes this SSE, for `EventSource`, for a proxy,
    and for anyone reading the wire with curl.

    SSE's newline rule is what has to be respected here: a payload containing a
    newline would be read as two `data:` lines and one broken frame. It cannot
    contain one — `json.dumps` escapes every newline, the same property NDJSON
    relies on — and the event name is stripped of CR/LF regardless, because a
    name that could frame itself is header injection in miniature.
    """
    name = str(event.get("event") or "message").replace("\r", "").replace("\n", "")
    return f"event: {name}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")


# A comment line: legal SSE, ignored by every parser, and the first bytes of the
# body. It exists so the response is *flowing* before the first completion comes
# back — a proxy deciding whether to buffer has something to forward at once.
NDJSON_STREAM = Framing(NDJSON_MEDIA_TYPE, _ndjson)
SSE_STREAM = Framing(SSE_MEDIA_TYPE, _sse, b": ok\n\n")


def _negotiate(request: Request) -> Framing | None:
    """Which framing this client asked for, or `None` for the JSON body (§3.5).

    SSE is checked first, so a client that lists both gets the one that survives
    a CDN. A client that asks for neither — curl, a script, a browser with no
    `ReadableStream` — gets exactly the body it got before: `/api/ask` is still
    one route with one contract and negotiation is the only difference.
    """
    accept = request.headers.get("accept", "")
    if SSE_MEDIA_TYPE in accept:
        return SSE_STREAM
    if NDJSON_MEDIA_TYPE in accept:
        return NDJSON_STREAM
    return None


async def _stream(
    scope: Scope,
    deps: Deps,
    public: PublicSettings,
    llm: OpenRouter,
    question: str,
    framing: Framing,
    billing: Billing,
) -> AsyncIterator[bytes]:
    """The loop's events, framed, and the budget accounting that goes with it.

    Two things this owes the rest of the system, and both are owed identically
    by every framing — which is why there is one of these and not two:

    * **The refund.** ``ask_endpoint`` refunds a failure that bought nothing
      and a stream is a 200 whatever happens inside it, so the accounting moves
      here: no paid completion, no charge. ``finally`` rather than an
      ``except`` branch, because the ways a stream dies without an error event
      are the interesting ones — the visitor closing the tab, a mode switch
      aborting the fetch, the loop being cancelled.

      What the condition may *not* be is "did an answer go out". A disconnect
      is something the client chooses, and the first `activity` line it can
      wait for is proof a completion already came back: refunding on
      no-answer paid for a stranger's tool calls, once per disconnect, for as
      long as they cared to repeat it. So the flag is :class:`Billing`, set
      when the provider answers, and a stream that died before the first
      completion is still refunded exactly as before.
    * **The terminal event.** ``AskUnavailable`` is a 503 body on the JSON path
      and an `error` event here, built from the same words, so the page's
      degraded pane renders the same either way.

    The ``finally`` runs on a cancelled generator, and the refund it makes is
    deliberately synchronous all the way down to the counter — a budget that
    needed to `await` to be given back is a budget that leaks on exactly the
    disconnect this exists to handle.
    """
    try:
        if framing.preamble:
            yield framing.preamble
        async with aclosing(ask_events(deps, public, llm, question, billing)) as events:
            async for event in events:
                yield framing.frame(event)
    except AskUnavailable as exc:
        yield framing.frame(_error_event(exc.reason, exc.retry_after_s))
    except Exception:  # pragma: no cover - last resort, still no leak
        logger.exception("ask: unexpected failure mid-stream")
        yield framing.frame(_error_event("upstream_unavailable", 30))
    finally:
        if not billing.paid:
            refund(scope, "ask_global")


async def _ask(request: Request, billing: Billing) -> Response:
    deps: Deps = request.app.state.assembled.deps
    public: PublicSettings = request.app.state.public_settings
    llm: OpenRouter | None = request.app.state.openrouter

    if llm is None or not public.ask_enabled:
        return _unavailable("not_configured", 0)

    try:
        body = await request.json()
    except Exception:
        body = None
    question = ""
    if isinstance(body, dict):
        question = str(body.get("q") or body.get("question") or "").strip()
    if not question:
        return JSONResponse(
            {
                "error": "E_BAD_PARAM",
                "message": 'ask needs a question: {"q": "…"}.',
                "next": None,
            },
            status_code=400,
        )
    question = question[:MAX_QUESTION_CHARS]

    framing = _negotiate(request)
    if framing is not None:
        # Everything that can be refused *before* the model is reached has been
        # refused above, with a status code. From here the answer is worth
        # watching, so the status line goes out now and the rest is events.
        return StreamingResponse(
            _stream(request.scope, deps, public, llm, question, framing, billing),
            media_type=framing.media_type,
            headers={
                "Cache-Control": "no-store",
                # nginx buffers a proxied response by default, which would hold
                # every activity line until the answer landed — i.e. exactly the
                # ninety seconds of silence this exists to remove. It is an
                # *nginx* header and nothing else reads it: Cloudflare decides
                # on the content type, which is why there is an SSE framing and
                # not a third header here.
                "X-Accel-Buffering": "no",
            },
        )

    try:
        payload = await asyncio.wait_for(
            run_ask(deps, public, llm, question, billing),
            timeout=public.ask_timeout_s + 5,
        )
    except AskUnavailable as exc:
        return _unavailable(exc.reason, exc.retry_after_s)
    except asyncio.TimeoutError:
        logger.warning("ask: the loop exceeded its wall-clock budget")
        return _unavailable("upstream_unavailable", 30)
    except Exception:  # pragma: no cover - last resort, still no leak
        logger.exception("ask: unexpected failure")
        return _unavailable("upstream_unavailable", 30)
    return JSONResponse(payload)


def _degraded_body(reason: str, retry_after_s: int) -> dict[str, Any]:
    """The one body both transports say "unavailable" with (§3.4).

    Written once so a stream's terminal event and a 503's body cannot drift: the
    page renders the same degraded pane from either, and neither carries the
    upstream's status line, body or exception text.
    """
    return {
        "error": "llm_unavailable",
        "reason": reason,
        "message": "LLM mode unavailable — use search.",
        "retry_after_s": retry_after_s or None,
    }


def _error_event(reason: str, retry_after_s: int) -> dict[str, Any]:
    """A stream's terminal event, carrying the status it *would* have been."""
    return {
        "event": "error",
        "status": 503,
        "payload": _degraded_body(reason, retry_after_s),
    }


def _unavailable(reason: str, retry_after_s: int) -> JSONResponse:
    headers = {"Retry-After": str(retry_after_s)} if retry_after_s else {}
    return JSONResponse(
        _degraded_body(reason, retry_after_s), status_code=503, headers=headers
    )
