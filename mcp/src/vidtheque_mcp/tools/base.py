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
from ..embeddings import EmbeddingClient, EmbeddingUnavailable, FrameQueryUnsupported
from ..errors import ToolError, plausible_video_id, timeout, unknown_video
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

    # Which tools this deployment does not register (``public/readonly.py``),
    # set once by ``tools.register`` — the one place the deployment's policy
    # meets the tool surface. It is here rather than in each tool because the
    # strings that recommend a tool are written all over the surface: the demo
    # masks `index-video`, and the guide, `E_UNKNOWN_VIDEO` and `job-status`
    # all went on recommending it anyway (demo-queries §9.1.8). A `next:` that
    # names a tool the caller cannot see is a dead end with a signpost.
    hidden_tools: frozenset[str] = frozenset()

    # None = not yet probed. Set False the first time the worker 404s
    # /v1/embeddings/frame-query (it predates the endpoint) or answers it in
    # the wrong dimension, so we stop paying for a call that cannot work; a
    # worker that gains the encoder is picked up on the next restart. Transient
    # outages do NOT set this — they must not be cached. See embed_query.
    frame_text_encoder: bool | None = None

    def offers(self, tool: str) -> bool:
        """Is ``tool`` part of *this* deployment's surface?"""
        return tool not in self.hidden_tools

    def hint(self, tool: str, hint: str, otherwise: str) -> str:
        """``hint`` when ``tool`` is registered here, ``otherwise`` when it is not.

        Every `next:` line and error remedy that names a write tool goes through
        this, so a read-only deployment degrades to what the caller *can* do
        instead of pointing at a tool that is absent from `tools/list`.
        """
        return hint if self.offers(tool) else otherwise

    async def embed_query(
        self,
        text: str,
        notes: list[str],
        *,
        space: str = "text",
    ) -> bytes | None:
        """Embed the query for a vector leg, or explain why we did not.

        ``space`` picks the endpoint: ``"text"`` for the transcript leg,
        ``"frame"`` for the frame leg. A leg that cannot run prints a `note:`,
        never a silent narrowing.

        **Two calls, one model, deliberately.** With the unified embedder both
        legs are one 2048-d space served by one loaded checkpoint, and a
        `content_type=all` search still embeds the query twice — because the
        model is instruction-aware and the two legs want different instructions
        ("retrieve the passage that answers this" vs "retrieve the frame that
        shows this"). Collapsing them into one call would be one embedding
        answering two questions. Both calls hit one loaded model, so the cost is
        a forward pass, not a load.

        The frame leg goes through ``POST /v1/embeddings/frame-query`` — a
        sibling path rather than a ``space=`` field, so a hosted-provider swap
        404s loudly instead of answering in the wrong space. A worker that
        predates the endpoint 404s too; we remember that (it is a property of
        the worker build, not a transient), note it, and stop asking until
        restart.
        """
        if not self.db.vectors.enabled:
            note = self.db.vectors.note()
            if note and note not in notes:
                notes.append(note)
            return None

        if space == "frame" and self.frame_text_encoder is False:
            self._frame_note(notes)
            return None

        model = self.db.config.get(f"{space}_embed.model")
        want_dim = self.db.frame_dim if space == "frame" else self.db.text_dim
        try:
            if space == "frame":
                # No `input_type`: this path IS the query side, and the
                # instruction it applies is the frame-retrieval one recorded as
                # config['frame_embed.query_prefix'].
                vectors, got_model, dimensions = await self.embeddings.embed_frame_query(
                    [text], model=model
                )
            else:
                # The asymmetric query instruction belongs to whoever runs the
                # model: `input_type=query` is the worker's switch for it, and
                # prepending config['text_embed.query_prefix'] here as well
                # would apply it twice.
                vectors, got_model, dimensions = await self.embeddings.embed(
                    [text], model=model, input_type="query"
                )
        except FrameQueryUnsupported:
            self.frame_text_encoder = False
            self._frame_note(notes)
            return None
        except EmbeddingUnavailable as exc:
            leg = "frame" if space == "frame" else "vector"
            # `str(exc)` is empty for a bare httpx timeout, and this note then
            # read "the embedding worker is unreachable ()" — a degraded search
            # that named its degradation and not its cause. A round-3 consumer
            # hit it live and could only say it "could not determine whether
            # the failure is transient" (§14.5).
            why = str(exc) or f"{type(exc).__name__}, no message"
            note = (
                f"note: the embedding worker is unreachable ({why}) — the "
                f"{leg} leg was skipped for this search."
            )
            if note not in notes:
                notes.append(note)
            return None

        # Both spaces are checked now. The old asymmetry ("only the text space
        # is checked for drift") was right while the two spaces came from two
        # checkpoints: a SigLIP mismatch said nothing about whether the
        # transcript index was coherent, so letting it disable both legs would
        # have been over-reach. With one model serving both, a frame-space
        # mismatch IS a text-space mismatch (memo §5.4) — and `note_worker_drift`
        # keeps the old restraint by ignoring the frame space when the corpus
        # names two different checkpoints.
        self.db.note_worker_drift(got_model, dimensions, space=space)
        if not self.db.vectors.enabled:
            note = self.db.vectors.note()
            if note and note not in notes:
                notes.append(note)
            return None

        if not vectors or len(vectors[0]) != want_dim:
            if space == "frame":
                self.frame_text_encoder = False
                self._frame_note(notes)
            else:
                notes.append(
                    f"note: the worker returned {len(vectors[0]) if vectors else 0}-d "
                    f"vectors for the {space} encoder but the corpus stores "
                    f"{want_dim}-d — that leg was skipped."
                )
            return None
        if space == "frame":
            self.frame_text_encoder = True
        return queries.pack_f32(vectors[0])

    def _frame_note(self, notes: list[str]) -> None:
        note = (
            "note: the frame leg needs the query run through the frame model's "
            "text tower, and this worker exposes no text->frame-space endpoint "
            "(POST /v1/embeddings/frame-query answered 404 — the worker likely "
            "predates it). Frame imagery was not searched; transcripts and "
            "on-screen text were."
        )
        if note not in notes:
            notes.append(note)


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


def require_known_videos(
    known: dict[str, int], requested: Sequence[str], deps: Deps | None = None
) -> None:
    """`E_UNKNOWN_VIDEO` names *which* ids were unknown.

    ``deps`` is optional only so a caller with no deployment context still gets
    the error; pass it wherever there is one, so the remedy degrades on a
    server that masks `index-video`.
    """
    missing = [v for v in requested if v not in known]
    if not missing:
        return
    can_index = deps is None or deps.offers("index-video")
    if len(missing) == 1:
        raise unknown_video(missing[0], can_index=can_index)
    # Same shape check as the single-id error (§4.6): a batch that contains
    # something which cannot be an id is a mis-copied id, not a video to spend
    # 2-6 min of GPU on.
    malformed = [v for v in missing if not plausible_video_id(v)]
    if malformed:
        remedy = (
            f"{', '.join(malformed)} cannot be video_id(s) — a video_id is an "
            "11-char YouTube id (e.g. kCc8FmEb1nY), used exactly as a result "
            "printed it. list-videos to browse what is indexed."
        )
    elif can_index:
        remedy = "list-videos to browse what is indexed, or index-video to add them."
    else:
        remedy = (
            "list-videos to browse what is indexed — this server is read-only "
            "and cannot add videos."
        )
    raise ToolError(
        "E_UNKNOWN_VIDEO",
        f"These video ids are not in the corpus: {', '.join(missing)}.",
        remedy,
    )
