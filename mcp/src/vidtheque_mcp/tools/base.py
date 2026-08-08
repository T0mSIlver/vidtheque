"""Shared plumbing for the nine tools: dependencies, admission, embedding."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Sequence

from mcp_types import CallToolResult, ContentBlock, TextContent

from ..auth.tokens import FrameUrlSigner
from ..config import Settings
from ..db import Database, QueryInterrupted
from ..db import queries
from ..embeddings import EmbeddingClient, EmbeddingUnavailable
from ..errors import ToolError, timeout, unknown_video
from ..jobs.runner import PipelineRunner


@dataclass
class Deps:
    """Everything the tool implementations need, wired once in ``app.py``."""

    settings: Settings
    db: Database
    embeddings: EmbeddingClient
    frame_signer: FrameUrlSigner | None = None
    runner: PipelineRunner | None = None
    search_semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))

    async def embed_query(
        self,
        text: str,
        notes: list[str],
        *,
        space: str = "text",
    ) -> bytes | None:
        """Embed the query for a vector leg, or explain why we did not.

        ``space`` picks the encoder: ``"text"`` for the transcript leg,
        ``"frame"`` for the frame leg — the query goes through SigLIP's *text*
        tower, which is the entire point of using SigLIP over a captioning
        pass. A leg that cannot run prints a `note:`, never a silent narrowing.
        """
        if not self.db.vectors.enabled:
            note = self.db.vectors.note()
            if note and note not in notes:
                notes.append(note)
            return None

        model = self.db.config.get(f"{space}_embed.model")
        want_dim = self.db.frame_dim if space == "frame" else self.db.text_dim
        prefix = self.db.query_prefix if space == "text" else ""
        try:
            vectors, got_model, dimensions = await self.embeddings.embed(
                [prefix + text], model=model
            )
        except EmbeddingUnavailable as exc:
            note = (
                f"note: the embedding worker is unreachable ({exc}) — this "
                "search ran on the lexical leg only."
            )
            if note not in notes:
                notes.append(note)
            return None

        if space == "text":
            # Only the text space is checked for drift: it is the one whose
            # vectors this query is about to be compared against by cosine.
            self.db.note_worker_drift(got_model, dimensions)
            if not self.db.vectors.enabled:
                note = self.db.vectors.note()
                if note:
                    notes.append(note)
                return None

        if not vectors or len(vectors[0]) != want_dim:
            notes.append(
                f"note: the worker returned {len(vectors[0]) if vectors else 0}-d "
                f"vectors for the {space} encoder but the corpus stores "
                f"{want_dim}-d — that leg was skipped."
            )
            return None
        return queries.pack_f32(vectors[0])


def text_result(body: str, structured: dict[str, Any] | None = None) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=body)],
        structured_content=structured,
    )


def blocks_result(blocks: Sequence[ContentBlock], structured: dict[str, Any] | None = None) -> CallToolResult:
    return CallToolResult(content=list(blocks), structured_content=structured)


def handle_errors(fn):
    """Map internal failures onto the typed error contract."""

    async def wrapper(*args: Any, **kwargs: Any) -> CallToolResult:
        try:
            return await fn(*args, **kwargs)
        except ToolError as err:
            return err.to_result()
        except QueryInterrupted as interrupted:
            if interrupted.deadline_expired:
                return timeout().to_result()
            raise
        except Exception as exc:  # pragma: no cover - last resort
            return ToolError(
                "E_INTERNAL",
                f"Unexpected failure: {exc}",
                "retry once; if it persists the server log has the trace id.",
            ).to_result()

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def normalize_video_ids(value: str | list[str] | None, limit: int, param: str = "video_id") -> list[str]:
    from ..errors import bad_param

    if value is None:
        return []
    ids = [value] if isinstance(value, str) else list(value)
    ids = [i.strip() for i in ids if i and i.strip()]
    if len(ids) > limit:
        raise bad_param(
            f"{param} accepts at most {limit} ids, got {len(ids)}.",
            f"scope to {limit} or fewer videos, or drop the filter and use channel=.",
        )
    return ids


def require_known_videos(known: dict[str, int], requested: Sequence[str]) -> None:
    """`E_UNKNOWN_VIDEO` names *which* ids were unknown."""
    missing = [v for v in requested if v not in known]
    if not missing:
        return
    if len(missing) == 1:
        raise unknown_video(missing[0])
    raise ToolError(
        "E_UNKNOWN_VIDEO",
        f"These video ids are not in the corpus: {', '.join(missing)}.",
        "list-videos to browse what is indexed, or index-video to add them.",
    )
