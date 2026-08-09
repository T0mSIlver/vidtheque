"""`POST /api/ask` — the server-side agent loop over OpenRouter (demo-site.md §3).

The mode exists to show a visitor the thing the corpus is *for*: an agent
answering from timestamped evidence. It is a bounded loop against OpenRouter's
OpenAI-compatible ``/chat/completions``, with two internal tools, a hard round
cap, and a forced final answer.

Two properties are load-bearing and tested:

* **Citations cannot be fabricated.** Every result the loop shows the model is
  recorded under an index; the response's citations are drawn from that record,
  and an `[n]` in the prose that names nothing is stripped rather than rendered
  as a dead link.
* **Nothing upstream leaks.** Not the key, not the provider's response body, not
  the status line, not an ``httpx2`` exception string. The operator gets those
  in the log; the visitor gets one of five reasons and "use search".
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx2 as httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..text import clock, deeplink, middle_truncate
from ..tools import search, segment
from ..tools.base import Deps
from .ratelimit import refund
from .settings import PublicSettings

logger = logging.getLogger(__name__)

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
        from .api import demo_text

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
                text=demo_text(hit.get("text")),
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

    async def complete(self, body: dict[str, Any], deadline: float) -> dict[str, Any]:
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
    deps: Deps, public: PublicSettings, llm: OpenRouter, question: str
) -> dict[str, Any]:
    """The loop. Returns the answer payload, or raises :class:`AskUnavailable`."""
    deadline = time.monotonic() + public.ask_timeout_s
    evidence = Evidence()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    rounds = 0

    for _ in range(max(1, public.ask_max_rounds)):
        payload = await llm.complete(
            {
                "model": public.openrouter_model,
                "messages": messages,
                "tools": TOOL_SPECS,
                "temperature": 0.2,
            },
            deadline,
        )
        message = _first_message(payload)
        calls = message.get("tool_calls") or []
        if not calls:
            content = (message.get("content") or "").strip()
            if content:
                return _answer(deps, content, evidence, rounds, public)
            break

        rounds += 1
        batch = _with_ids(calls[:MAX_TOOL_CALLS_PER_ROUND], rounds)
        messages.append(_assistant_turn(message, batch))
        for call in batch:
            messages.append(await _run_tool(deps, call, evidence))
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
    )
    content = (_first_message(payload).get("content") or "").strip()
    if not content:
        logger.warning("ask: model produced no answer after %s rounds", rounds)
        raise AskUnavailable("upstream_unavailable")
    return _answer(deps, content, evidence, rounds, public)


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


async def _run_tool(
    deps: Deps, call: dict[str, Any], evidence: Evidence
) -> dict[str, Any]:
    """Run one internal tool and shape its result as a `tool` message."""
    function = call.get("function") or {}
    name = function.get("name") or ""
    try:
        args = json.loads(function.get("arguments") or "{}")
    except ValueError:
        args = {}
    if not isinstance(args, dict):
        args = {}

    if name == "search":
        text = await _tool_search(deps, args, evidence)
    elif name == "get_segment_context":
        text = await _tool_context(deps, args, evidence)
    else:
        text = f"error: no tool named {name!r}. Use search or get_segment_context."

    return {
        # `_with_ids` ran over this batch, so the id exists and is unique.
        "role": "tool",
        "tool_call_id": call["id"],
        "name": name,
        "content": text,
    }


async def _tool_search(deps: Deps, args: dict[str, Any], evidence: Evidence) -> str:
    query = str(args.get("query") or "").strip()[:512]
    if not query:
        return "error: search needs a query."
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
        return f"error: {payload.get('code')} — {payload.get('message')}"

    hits = (result.structured_content or {}).get("results", [])
    if not hits:
        return f'No results for "{query}". Try different words.'
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
    return "\n".join(lines)


async def _tool_context(deps: Deps, args: dict[str, Any], evidence: Evidence) -> str:
    video_id = str(args.get("video_id") or "").strip()
    if not video_id:
        return "error: get_segment_context needs a video_id from a search hit."
    try:
        t = float(args.get("t"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "error: t must be a number of seconds, as given by a search hit."
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
        return f"error: {payload.get('code')} — {payload.get('message')}"
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
    return text


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


async def ask_endpoint(request: Request) -> JSONResponse:
    """The endpoint, plus the one thing the limiter cannot know on its own.

    The daily budget is charged before this runs (§4), which is right for an
    ask that reaches the model and wrong for one that does not. Launch day is
    exactly when a free tier flaps: without this, one visitor retrying through
    503s spends the whole 50/day in ten minutes and every *other* visitor is
    locked out until the bucket trickles back. So a non-200 gives the day's
    token back — the request cost no upstream tokens, so it costs no budget.

    Only ``ask_global`` is refunded. The per-IP minute bucket is the anti-hammer
    guard, not the cost control: someone retrying a broken upstream five times a
    minute should still be slowed down.
    """
    response = await _ask(request)
    if response.status_code != 200:
        refund(request.scope, "ask_global")
    return response


async def _ask(request: Request) -> JSONResponse:
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

    try:
        payload = await asyncio.wait_for(
            run_ask(deps, public, llm, question), timeout=public.ask_timeout_s + 5
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


def _unavailable(reason: str, retry_after_s: int) -> JSONResponse:
    headers = {"Retry-After": str(retry_after_s)} if retry_after_s else {}
    return JSONResponse(
        {
            "error": "llm_unavailable",
            "reason": reason,
            "message": "LLM mode unavailable — use search.",
            "retry_after_s": retry_after_s or None,
        },
        status_code=503,
        headers=headers,
    )
