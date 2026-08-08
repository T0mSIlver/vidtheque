"""Tool and resource registration against the MCP server.

Nine tools, kebab-case, each carrying the annotations from tool-surface §3.9.
Every handler returns a ``CallToolResult`` directly so it controls its own
content blocks (text, and for ``get-frames`` the opt-in ``ImageContent``) and
its ``structuredContent`` — conformant clients read the latter without spending
prose tokens.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server import MCPServer
from mcp_types import CallToolResult
from pydantic import Field

from . import frames as frames_tool
from . import indexing, library, resources, search
from .base import Deps
from .descriptions import ANNOTATIONS, DESCRIPTIONS

__all__ = ["Deps", "register"]


def register(mcp: MCPServer, deps: Deps) -> None:
    _register_tools(mcp, deps)
    _register_resources(mcp, deps)


def _register_tools(mcp: MCPServer, deps: Deps) -> None:
    async def search_tool(
        q: str | None = None,
        content_type: str = "all",
        limit: int = 10,
        offset: int = 0,
        order: str = "relevance",
        video_id: str | list[str] | None = None,
        channel: str | None = None,
        video_title: str | None = None,
        tags: str | None = None,
        include_related: bool = False,
        published_after: str | None = None,
        published_before: str | None = None,
        t_start: float | str | None = None,
        t_end: float | str | None = None,
        speaker: str | None = None,
        min_chars: int | None = None,
        max_chars: int | None = None,
        max_per_video: int = 3,
        cluster_gap: float = 8.0,
        max_text_chars: int = 1000,
        format: str = "text",
        fields: str = search.DEFAULT_FIELDS,
    ) -> CallToolResult:
        return await search.run(
            deps,
            q=q,
            content_type=content_type,
            limit=limit,
            offset=offset,
            order=order,
            video_id=video_id,
            channel=channel,
            video_title=video_title,
            tags=tags,
            include_related=include_related,
            published_after=published_after,
            published_before=published_before,
            t_start=t_start,
            t_end=t_end,
            speaker=speaker,
            min_chars=min_chars,
            max_chars=max_chars,
            max_per_video=max_per_video,
            cluster_gap=cluster_gap,
            max_text_chars=max_text_chars,
            format=format,
            fields=fields,
        )

    async def list_videos_tool(
        q: str | None = None,
        channel: str | None = None,
        tags: str | None = None,
        published_after: str | None = None,
        published_before: str | None = None,
        indexed_after: str | None = None,
        indexed_before: str | None = None,
        has: str = "any",
        order: str = "recency",
        limit: int = 20,
        offset: int = 0,
        format: str = "tsv",
        fields: str = library.DEFAULT_LIST_FIELDS,
        max_text_chars: int = 120,
    ) -> CallToolResult:
        return await library.list_videos(
            deps,
            q=q,
            channel=channel,
            tags=tags,
            published_after=published_after,
            published_before=published_before,
            indexed_after=indexed_after,
            indexed_before=indexed_before,
            has=has,
            order=order,
            limit=limit,
            offset=offset,
            format=format,
            fields=fields,
            max_text_chars=max_text_chars,
        )

    async def corpus_summary_tool(
        channel: str | None = None,
        tags: str | None = None,
        published_after: str | None = None,
        published_before: str | None = None,
        include_channels: bool = True,
        include_tags: bool = True,
        include_recent: bool = True,
        include_gaps: bool = True,
        include_guidance: bool = True,
        max_channels: int = 10,
        max_tags: int = 30,
        max_recent: int = 8,
        max_text_chars: int = 120,
    ) -> CallToolResult:
        return await library.corpus_summary(
            deps,
            channel=channel,
            tags=tags,
            published_after=published_after,
            published_before=published_before,
            include_channels=include_channels,
            include_tags=include_tags,
            include_recent=include_recent,
            include_gaps=include_gaps,
            include_guidance=include_guidance,
            max_channels=max_channels,
            max_tags=max_tags,
            max_recent=max_recent,
            max_text_chars=max_text_chars,
        )

    async def video_summary_tool(
        video_id: str,
        t_start: float | str | None = None,
        t_end: float | str | None = None,
        include_chapters: bool = True,
        include_speakers: bool = True,
        include_key_texts: bool = True,
        include_ocr_highlights: bool = True,
        include_links: bool = False,
        include_tags: bool = True,
        include_guidance: bool = True,
        max_chapters: int = 20,
        max_key_texts: int = 12,
        max_ocr_highlights: int = 10,
        max_chars: int = 300,
        format: str = "text",
    ) -> CallToolResult:
        return await library.video_summary(
            deps,
            video_id=video_id,
            t_start=t_start,
            t_end=t_end,
            include_chapters=include_chapters,
            include_speakers=include_speakers,
            include_key_texts=include_key_texts,
            include_ocr_highlights=include_ocr_highlights,
            include_links=include_links,
            include_tags=include_tags,
            include_guidance=include_guidance,
            max_chapters=max_chapters,
            max_key_texts=max_key_texts,
            max_ocr_highlights=max_ocr_highlights,
            max_chars=max_chars,
            format=format,
        )

    async def get_segment_context_tool(
        video_id: str,
        t: float | str | None = None,
        window: float = 45,
        cue_id: int | None = None,
        include_ocr: bool = True,
        include_frame_refs: bool = True,
        include_chapter: bool = True,
        include_links: bool = False,
        max_text_chars: int = 4000,
    ) -> CallToolResult:
        from . import segment

        return await segment.run(
            deps,
            video_id=video_id,
            t=t,
            window=window,
            cue_id=cue_id,
            include_ocr=include_ocr,
            include_frame_refs=include_frame_refs,
            include_chapter=include_chapter,
            include_links=include_links,
            max_text_chars=max_text_chars,
        )

    async def get_frames_tool(
        frame_ids: list[str] | None = None,
        video_id: str | None = None,
        t_start: float | str | None = None,
        t_end: float | str | None = None,
        # `return` is a Python keyword and cannot be a parameter name, so the
        # wire name comes from a pydantic alias; see `_alias_return` below for
        # the other half.
        return_: Annotated[str, Field(alias="return")] = "url",
        limit: int = 3,
        width: int = 512,
        quality: int = 75,
        include_ocr: bool = True,
    ) -> CallToolResult:
        return await frames_tool.run(
            deps,
            frame_ids=frame_ids,
            video_id=video_id,
            t_start=t_start,
            t_end=t_end,
            return_=return_,
            limit=limit,
            width=width,
            quality=quality,
            include_ocr=include_ocr,
        )

    async def index_video_tool(
        url: str | None = None,
        urls: list[str] | None = None,
        expand: str = "playlist",
        max_items: int = 25,
        tags: str | None = None,
        force_reindex: bool = False,
        channels: str = "all",
        priority: str = "normal",
    ) -> CallToolResult:
        return await indexing.index_video(
            deps,
            url=url,
            urls=urls,
            expand=expand,
            max_items=max_items,
            tags=tags,
            force_reindex=force_reindex,
            channels=channels,
            priority=priority,
        )

    async def job_status_tool(
        job_id: str | None = None,
        video_id: str | None = None,
        state: str = "active",
        limit: int = 5,
    ) -> CallToolResult:
        return await indexing.job_status(
            deps, job_id=job_id, video_id=video_id, state=state, limit=limit
        )

    async def tag_video_tool(
        video_id: str | list[str],
        add: list[str] | None = None,
        remove: list[str] | None = None,
        dry_run: bool = False,
    ) -> CallToolResult:
        return await library.tag_video(
            deps, video_id=video_id, add=add, remove=remove, dry_run=dry_run
        )

    registry: list[tuple[str, Any]] = [
        ("search", search_tool),
        ("list-videos", list_videos_tool),
        ("corpus-summary", corpus_summary_tool),
        ("video-summary", video_summary_tool),
        ("get-segment-context", get_segment_context_tool),
        ("get-frames", get_frames_tool),
        ("index-video", index_video_tool),
        ("job-status", job_status_tool),
        ("tag-video", tag_video_tool),
    ]
    for name, fn in registry:
        mcp.add_tool(
            fn,
            name=name,
            title=ANNOTATIONS[name].title,
            description=DESCRIPTIONS[name],
            annotations=ANNOTATIONS[name],
            structured_output=False,
        )

    _alias_return(mcp)


def _alias_return(mcp: MCPServer) -> None:
    """Make ``get-frames`` accept the contract's ``return`` argument name.

    The alias makes the schema and validation use ``return``; the SDK then
    calls the handler with ``**{"return": ...}`` (it dumps by alias), which no
    Python function can accept for a keyword. So the registered callable is
    wrapped once, here, to map that single key back.
    """
    tool = mcp._tool_manager.get_tool("get-frames")  # no public accessor for this
    if tool is None:  # pragma: no cover - registration just ran
        return
    inner = tool.fn

    def call_with_return_alias(**kwargs: Any) -> Any:
        kwargs["return_"] = kwargs.pop("return", "url")
        return inner(**kwargs)

    tool.fn = call_with_return_alias


def _register_resources(mcp: MCPServer, deps: Deps) -> None:
    @mcp.resource(
        "vidtheque://corpus",
        name="corpus",
        title="vidtheque corpus",
        description="The browsable library as TSV: id, title, channel, date, duration, coverage, tags.",
        mime_type="text/tab-separated-values",
    )
    async def corpus() -> str:
        return await resources.corpus_resource(deps)

    @mcp.resource(
        "vidtheque://context",
        name="context",
        title="vidtheque context",
        description="Precomputed timestamps and corpus facts, so the model never does date arithmetic.",
        mime_type="application/json",
    )
    async def context() -> str:
        return await resources.context_resource(deps)

    @mcp.resource(
        "vidtheque://guide",
        name="guide",
        title="Using vidtheque",
        description="Progressive-disclosure guide and the shared rules every tool relies on.",
        mime_type="text/markdown",
    )
    async def guide() -> str:
        return resources.GUIDE
