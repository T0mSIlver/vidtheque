"""The typed error contract from tool-surface §3.8.

Errors are one text block plus `structuredContent`, returned with
`isError: true` so the model retries differently instead of treating the error
prose as data.

Note the deliberate asymmetry with the *transport*: an MCP tool error is a 200
with `isError: true`. An **auth** failure is never one of these — it must be a
transport-level 401, or Claude passes the text to the model and no auth prompt
appears (research doc §2.2). Auth lives in the middleware, not here.
"""

from __future__ import annotations

import re

from mcp_types import CallToolResult, TextContent

# §3.1: an 11-char YouTube id, or `<source>:<id>` for a future non-YouTube one.
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SOURCED_ID = re.compile(r"^[a-z][a-z0-9]{1,15}:[A-Za-z0-9_.:-]{1,64}$")
# The id inside a pasted watch/short/embed URL, which is the one wrong-shaped
# input that is worth answering with the right shape rather than a shrug.
_ID_IN_URL = re.compile(r"(?:youtu\.be/|[?&]v=|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})")


def plausible_video_id(value: str) -> bool:
    """Could this string be a `video_id` at all? (§3.1's two shapes.)"""
    return bool(_YOUTUBE_ID.match(value) or _SOURCED_ID.match(value))


# code -> HTTP-shaped status, kept for the raw API and for documentation parity
# with the table in tool-surface §3.8.
HTTP_STATUS: dict[str, int] = {
    "E_BAD_TIME_FORMAT": 400,
    "E_BAD_PARAM": 400,
    "E_EMPTY_QUERY": 400,
    "E_ORDER_SCOPE": 400,
    "E_UNKNOWN_VIDEO": 404,
    "E_UNKNOWN_FRAME": 404,
    "E_UNKNOWN_JOB": 404,
    "E_NOT_INDEXED": 409,
    "E_INDEXING": 409,
    "E_FEATURE_DISABLED": 409,
    "E_TIMEOUT": 408,
    "E_BUSY": 503,
    "E_RATE_LIMIT": 429,
    "E_TOO_LARGE": 413,
    "E_UNSUPPORTED_SOURCE": 422,
    "E_NOT_IMPLEMENTED": 501,
    "E_INTERNAL": 500,
}


class ToolError(Exception):
    """A typed, actionable error the model can act on."""

    def __init__(
        self,
        code: str,
        message: str,
        next_hint: str | None = None,
        retry_after_s: int | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_hint = next_hint
        self.retry_after_s = retry_after_s
        self.extra = extra or {}

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 500)

    def text(self) -> str:
        lines = [f"error: {self.code}", self.message]
        if self.next_hint:
            lines.append(f"next: {self.next_hint}")
        return "\n".join(lines)

    def structured(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "next": self.next_hint,
            "retry_after_s": self.retry_after_s,
        }
        payload.update(self.extra)
        return payload

    def to_result(self) -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text=self.text())],
            structured_content=self.structured(),
            is_error=True,
        )


# ---------------------------------------------------------------------------
# Constructors for the errors raised in more than one place, so the `next:`
# hints cannot drift between call sites.


def bad_param(message: str, next_hint: str | None = None) -> ToolError:
    return ToolError("E_BAD_PARAM", message, next_hint)


def bad_time(value: str, param: str) -> ToolError:
    return ToolError(
        "E_BAD_TIME_FORMAT",
        f'Could not parse {param}={value!r}.',
        "accepted: ISO 8601 (2026-03-01, 2026-03-01T12:00:00Z), relative "
        '("7d ago", "3w ago", "6mo ago", "2y ago"), or a keyword '
        "(now, today, yesterday). Intra-video times also accept seconds (723) "
        "or a clock string (12:03, 1:12:03).",
    )


def unknown_video(video_id: str, can_index: bool = True) -> ToolError:
    """``can_index`` is False where the deployment masks `index-video`.

    The remedy has to be a tool the caller can actually see: in demo/read-only
    mode `index-video` is absent from `tools/list`, and this error kept naming
    it anyway — the most-hit dead end in the surface pointing at the one tool
    that is not there (demo-queries §9.1.8).

    It also has to be a remedy worth following. The `index-video` line used to
    string-concatenate whatever arrived into `https://youtu.be/<it>` and
    recommend spending 2-6 min of GPU on it; a stress-testing consumer refused
    it on its own ("a client should not follow that suggestion blindly", terra
    eval §4.6). Two things were wrong, and they need different fixes:

    - **Input that cannot be an id at all** — a title, a sentence, a URL, a
      13-character string — now gets the shape back instead of a download
      recommendation, and a pasted URL gets the id from inside it.
    - **Input that is a well-formed id we do not have** cannot be told apart
      from a real one by this server; that is what "not in the corpus" means.
      (`not-a-video`, the eval's own example, is 11 legal characters.) So the
      remedy states its own precondition rather than recommending the spend
      unconditionally: a copied id is worth indexing, a remembered one is the
      fabrication the guide forbids.

    **The ordering is the residual** (amended 2026-08-11, terra eval §9.5).
    Two stress-testing consumers a week apart graded this "partly" for the same
    reason: the sentence *led* with `index-video` on the string they had just
    made up, so what a reader saw first was an offer to spend 2-6 min of GPU on
    a guess, and the precondition arrived after they had already read the
    remedy. The clauses are unchanged in substance and reversed in order —
    `list-videos` first, because it is the recovery that is always right, and
    `index-video` second, behind a precondition written as a test the caller
    can actually apply ("in front of you in this conversation", not "not from
    memory", which asks a model to introspect). A shape check cannot reach
    this: the string is a legal id, and the only thing that distinguishes a
    copied one from an invented one lives in the caller.
    """
    if plausible_video_id(video_id):
        remedy = (
            f"list-videos (or the vidtheque://corpus resource) to browse what is "
            f"indexed — that is the recovery whatever the id turns out to be. "
            f'index-video url="https://youtu.be/{video_id}" is worth ~2-6 min of GPU '
            f"ONLY if this id came from outside the corpus and is in front of you — "
            f"a YouTube URL or page you were given. An id you recalled or assembled "
            f"is not a video: indexing it fetches nothing, or the wrong thing."
            if can_index
            else "list-videos to browse what is indexed — this server is read-only and "
            "cannot add videos, so a video that is not listed cannot be answered from."
        )
        return ToolError("E_UNKNOWN_VIDEO", f'Video "{video_id}" is not in the corpus.', remedy)

    inside = _ID_IN_URL.search(video_id)
    if inside:
        remedy = (
            f'that is a URL — the video_id is the 11 characters inside it: '
            f'video_id="{inside.group(1)}".'
        )
        if can_index:
            remedy += f' If it is not in the corpus, index-video url="{video_id}" adds it.'
    else:
        remedy = (
            "a video_id is an 11-char YouTube id (e.g. kCc8FmEb1nY). Use one exactly "
            "as a search, list-videos or corpus resource result printed it — never a "
            "title, a URL or a guess. list-videos to browse what is indexed."
        )
    return ToolError(
        "E_UNKNOWN_VIDEO",
        f'"{video_id}" is not a video_id, so nothing in the corpus can match it.',
        remedy,
    )


def unknown_frame(frame_id: str, video_id: str | None, max_ord: int | None) -> ToolError:
    if video_id is not None and max_ord is not None:
        detail = (
            f'Frame "{frame_id}" does not exist. Video {video_id} has keyframe '
            f"ordinals 00000-{max_ord:05d}."
        )
    else:
        detail = f'Frame "{frame_id}" does not exist.'
    return ToolError(
        "E_UNKNOWN_FRAME",
        detail,
        "use a frame_id exactly as it appeared in a search, video-summary or "
        "get-segment-context result — never construct one.",
    )


def unknown_job(job_id: str) -> ToolError:
    return ToolError(
        "E_UNKNOWN_JOB",
        f'No job with id "{job_id}".',
        "call job-status with no arguments to list recent jobs.",
    )


def busy(retry_after_s: int = 1) -> ToolError:
    """The remedy is time, and only time — say so and nothing else.

    It used to read "retry in 1s, **or narrow the query so it costs less**",
    which taught a recovery that cannot work: the semaphore is acquired before
    the query is built (`db/connection.py::admission`), so a cheaper query is
    refused exactly as fast as an expensive one. A terra consumer read the
    second clause as the actionable half, narrowed twice instead of waiting,
    lost both searches and gave up on `search` for four calls
    (research/mcp-eval-terra-2026-08-10.md §4.9).
    """
    return ToolError(
        "E_BUSY",
        "The server is already running its maximum number of concurrent searches.",
        # No mention of query cost at all, not even to deny it: the reason this
        # hint misfired is that a consumer took the cheapest-looking clause and
        # acted on it, and a negated one is exactly as available.
        #
        # "do not reformulate" is the second attempt at the behavioural half
        # (terra eval §9.7): with the text fixed, 2 of 3 consumers repeated the
        # identical call and the third re-worded its query twice — reading
        # "retry the same call" as advice rather than as the instruction. An
        # imperative naming the wrong move is one clause; if it still does not
        # land, the answer is a bigger semaphore, not more prose.
        f"retry the IDENTICAL call in {retry_after_s}s — do not reformulate the "
        "query, a different one is refused exactly as fast. The limit is on "
        "concurrent searches, not on what a query costs, so the same call "
        "succeeds as soon as a slot frees.",
        retry_after_s=retry_after_s,
    )


def timeout() -> ToolError:
    return ToolError(
        "E_TIMEOUT",
        "The query exceeded the 30s budget and was interrupted.",
        "narrow the range: add channel=, video_id=, or a tighter published_after.",
    )
