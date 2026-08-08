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

from mcp_types import CallToolResult, TextContent

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


def unknown_video(video_id: str) -> ToolError:
    return ToolError(
        "E_UNKNOWN_VIDEO",
        f'Video "{video_id}" is not in the corpus.',
        f'index-video url="https://youtu.be/{video_id}" to add it (takes ~2-6 min), '
        "or list-videos to browse what is indexed.",
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
    return ToolError(
        "E_BUSY",
        "The server is already running its maximum number of concurrent searches.",
        f"retry in {retry_after_s}s, or narrow the query so it costs less.",
        retry_after_s=retry_after_s,
    )


def timeout() -> ToolError:
    return ToolError(
        "E_TIMEOUT",
        "The query exceeded the 30s budget and was interrupted.",
        "narrow the range: add channel=, video_id=, or a tighter published_after.",
    )
