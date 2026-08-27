"""``GET /videos/{video_id}/export.md`` — one video, as Markdown, for a human.

The owner's copy of what the index holds about one talk: the metadata, what the
pipeline made it out of, the chapters, the whole transcript with a `?t=` on
every line, and the on-screen text. Written for a notes app, not for a model —
`tool-surface.md` §6 kept it off the MCP surface for exactly that reason and
the tool count is unchanged.

**On a public instance it is owner-only, and that is the whole security
design.** This is the full-transcript hatch in file form, so where
``VIDTHEQUE_PUBLIC_READONLY=1`` the gate is ``auth.credential.is_owner`` rather
than "did the read gate let you in". The distinction is not academic: that
deployment runs ``VIDTHEQUE_AUTH=none``, so *every* request is ``"open"``, and a
route that read the open gate as ownership would turn `/demo` into a bulk
download of every transcript in the corpus.

A private box is the other case and gets the `/frames` rule instead: in `none`
mode the route is open, because everything else is too. The README's quickstart
deployment is exactly that box, and a self-hoster's own export refusing them on
their own machine would be a feature that does not work where it is aimed.
``is_owner`` still covers the middle case — a public instance reached with a
bearer, a session, or from a trusted CIDR.

Like `/frames/<id>.jpg` this cannot be an ``@mcp.custom_route`` — those are
never authenticated — so it lives on our own Starlette root app.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from .. import __version__
from ..auth.credential import credential, is_owner
from ..config import Settings
from ..db import Database, queries
from ..errors import HTTP_STATUS, plausible_video_id
from ..text import clock, deeplink, duration_clock, iso_day

# Cues are read in pages because `cue_page` is the only full-transcript reader
# and it answers `has_more` by over-fetching one row. A page this size is one
# statement for any talk shorter than about nine hours.
CUE_PAGE = 2_000

# The transcript is unbounded on purpose — it is the artifact — but the frame
# walk is not, because OCR is the half that can run to six figures of lines.
# 400 distinct frames against the 126 a measured 70-minute talk produced
# (`docs/takedown.md` §2.4) is headroom, not a limit anyone will meet; when it
# does bind the document says so rather than ending early in silence.
MAX_OCR_FRAMES = 400


def export_routes(settings: Settings, db: Database) -> list[Route]:
    async def serve(request: Request) -> Response:
        video_id = request.path_params["video_id"]

        if await credential(request) is None:
            return JSONResponse(
                {"error": "invalid_token", "error_description": "bearer or session required"},
                status_code=401,
                headers={
                    "Cache-Control": "no-store",
                    "WWW-Authenticate": 'Bearer error="invalid_token", scope="vidtheque:read"',
                },
            )
        # In `none` mode on a private box the route is open, because everything
        # else is too (`frames.py`). It is the *public projection* that must not
        # hand out whole transcripts, and there `"open"` is what an anonymous
        # request off the internet gets — so ownership has to be proved, not
        # inherited from the read gate.
        if request.app.state.assembled.public.enabled and not await is_owner(request):
            # 403, not 401: under `AUTH=none` there is no credential to present,
            # so a challenge would name a door that does not exist.
            return JSONResponse(
                {
                    "error": "E_FORBIDDEN",
                    "message": "export is owner-only on a public instance — "
                    "it is the whole transcript",
                },
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )

        if not plausible_video_id(video_id):
            return _error("E_UNKNOWN_VIDEO", f"{video_id!r} is not a video id", video_id)

        row = await db.read(lambda c: queries.lookup_video(c, video_id))
        if row is None:
            return _error("E_UNKNOWN_VIDEO", "no such video in this corpus", video_id)

        state = str(row["index_state"])
        if state in ("pending", "indexing"):
            code = "E_INDEXING" if state == "indexing" else "E_NOT_INDEXED"
            return _error(code, f"index_state is {state}; nothing to export yet", video_id)

        want_ocr = request.query_params.get("ocr") != "0"
        body = await _render(settings, db, row, want_ocr=want_ocr)
        return PlainTextResponse(
            body,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": f'attachment; filename="{video_id}.md"',
            },
        )

    return [Route("/videos/{video_id}/export.md", serve, methods=["GET"])]


def _error(code: str, message: str, video_id: str) -> JSONResponse:
    return JSONResponse(
        {"error": code, "message": message, "video_id": video_id},
        status_code=HTTP_STATUS[code],
        headers={"Cache-Control": "no-store"},
    )


async def _render(
    settings: Settings, db: Database, row, *, want_ocr: bool
) -> str:
    vid = int(row["id"])
    public_id = str(row["public_id"])
    lead = settings.deeplink_lead_s

    # One connection for the whole document rather than the tools' read-per-query:
    # an export is a snapshot, and a transcript that straddled a re-index would
    # be a file whose halves disagree.
    def gather(c):
        cues: list = []
        while True:
            page = queries.cue_page(c, vid, len(cues), CUE_PAGE)
            cues.extend(page[:CUE_PAGE])
            if len(page) <= CUE_PAGE:
                break
        frames = (
            queries.ocr_highlights(c, vid, MAX_OCR_FRAMES, None, None) if want_ocr else []
        )
        return (
            queries.video_stages(c, vid),
            queries.video_tags(c, [vid]).get(vid, []),
            queries.chapters(c, vid, 200),
            cues,
            # `ocr_highlights` orders by how much text a frame carries, because
            # its other caller wants the loudest slides. An export wants the
            # talk in the order it happened.
            sorted(frames, key=lambda f: float(f["t_s"])),
        )

    stages, tags, chapters, cues, frames = await db.read(gather)

    out: list[str] = []
    out.extend(_front_matter(row, public_id, tags))
    out.append(f"# {row['title']}")
    out.append("")
    out.append(_provenance_line(row, public_id))
    out.append("")

    if row["description"]:
        out.append("## Description")
        out.append("")
        out.append(str(row["description"]).strip())
        out.append("")

    out.extend(_stages_table(stages))
    out.extend(_chapters(chapters, public_id, lead))
    out.extend(_transcript(cues, public_id, lead))
    if want_ocr:
        out.extend(_on_screen(frames, public_id, lead))

    return "\n".join(out).rstrip() + "\n"


def _yaml(value: str) -> str:
    """Double-quoted YAML scalar. Titles carry quotes, colons and backslashes."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _front_matter(row, public_id: str, tags: list[str]) -> list[str]:
    lines = [
        "---",
        f"title: {_yaml(str(row['title']))}",
        f"video_id: {_yaml(public_id)}",
        f"url: {_yaml(str(row['url']))}",
    ]
    if row["channel_name"]:
        lines.append(f"channel: {_yaml(str(row['channel_name']))}")
    if row["published_at"]:
        lines.append(f"published: {iso_day(int(row['published_at']))}")
    if row["duration_s"]:
        lines.append(f"duration: {_yaml(duration_clock(float(row['duration_s'])))}")
    if row["indexed_at"]:
        lines.append(f"indexed: {iso_day(int(row['indexed_at']))}")
    if tags:
        lines.append("tags:")
        lines.extend(f"  - {_yaml(t)}" for t in tags)
    lines.append(f"exported_by: {_yaml('vidtheque ' + __version__)}")
    lines.append("---")
    lines.append("")
    return lines


def _provenance_line(row, public_id: str) -> str:
    bits = [f"[{row['url']}]({row['url']})"]
    if row["channel_name"]:
        bits.append(str(row["channel_name"]))
    if row["published_at"]:
        bits.append(iso_day(int(row["published_at"])))
    if row["duration_s"]:
        bits.append(duration_clock(float(row["duration_s"])))
    return " · ".join(bits)


def _stages_table(stages) -> list[str]:
    if not stages:
        return []
    out = ["## How this was indexed", "", "| stage | state | model |", "|---|---|---|"]
    for s in stages:
        out.append(f"| {s['stage']} | {s['state']} | {s['model_key'] or '-'} |")
    out.append("")
    return out


def _chapters(chapters, public_id: str, lead: int) -> list[str]:
    if not chapters:
        return []
    out = ["## Chapters", ""]
    for ch in chapters:
        out.append(f"- {_stamp(public_id, ch['start_s'], lead)} {ch['title']}")
    out.append("")
    return out


def _transcript(cues, public_id: str, lead: int) -> list[str]:
    out = ["## Transcript", ""]
    if not cues:
        out.append("*No transcript cues are indexed for this video.*")
        out.append("")
        return out
    for cue in cues:
        speaker = f" **{cue['speaker']}:**" if cue["speaker"] else ""
        out.append(f"{_stamp(public_id, cue['start_s'], lead)}{speaker} {cue['text']}")
        out.append("")
    return out


def _on_screen(frames, public_id: str, lead: int) -> list[str]:
    out = ["## On-screen text", ""]
    if not frames:
        out.append("*No on-screen text is indexed for this video.*")
        out.append("")
        return out
    if len(frames) == MAX_OCR_FRAMES:
        out.append(
            f"*Capped at the {MAX_OCR_FRAMES} frames carrying the most text; "
            "near-identical frames are already collapsed. Pass `?ocr=0` to omit "
            "this section entirely.*"
        )
        out.append("")
    for f in frames:
        frame_id = f"{public_id}-{int(f['ord']):05d}"
        out.append(f"### {_stamp(public_id, f['t_s'], lead)} `{frame_id}`")
        out.append("")
        for line in str(f["screen_text"] or "").split(" | "):
            if line.strip():
                out.append(f"> {line.strip()}")
        out.append("")
    return out


def _stamp(public_id: str, t, lead: int) -> str:
    """`[12:03](https://youtu.be/ID?t=721)`, or the bare clock off YouTube."""
    seconds = None if t is None else float(t)
    link = deeplink(public_id, seconds, lead)
    if link is None:
        return f"[{clock(seconds)}]"
    return f"[{clock(seconds)}]({link})"
