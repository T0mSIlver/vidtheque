"""Agent text → reader text. One layer, one place (demo-site.md §2.4).

The MCP payload is written for a model: it carries markers a *client* can act
on (`pass max_text_chars=0`), stand-in strings that keep a field non-null, and
`note:` prefixes that mark a line as machinery rather than content. That text is
a contract and does not move — tool-surface.md owns it, and an agent parsing a
`note:` prefix is a supported thing to do.

The demo is the other consumer, and its reader has no API. So the translation
happens *here*, at the facade, and never in the tools. It is a module rather
than three expressions inside ``api.py`` because the dashboard is the second
caller: a humanised snippet should look the same wherever it is rendered.

What it deliberately does **not** do: rewrite the *body* of a note. Those
sentences are the query layer's own English, and a second copy of its vocabulary
kept here would drift silently the day someone edits a leg. The prefix is
machinery; the sentence after it is already written for a person.
"""

from __future__ import annotations

import re

from ..text import TRUNCATION_MARKER

# `…` on its own. The tool's marker ends in advice only an MCP client can take
# ("pass max_text_chars=0 for full text") and the facade has no such opt-out, so
# for a reader the marker is one thing: some words are missing here.
ELLIPSIS = "…"

# Built from the template rather than retyped, so a change in `text.py` cannot
# leave a stale pattern behind that silently matches nothing.
_TRUNCATED = re.compile(re.escape(TRUNCATION_MARKER).replace(re.escape("{n}"), r"\d+"))

# Two ellipses that meet — the marker landing next to text that already trailed
# off — are one ellipsis to a reader.
_DOUBLED = re.compile(rf"{ELLIPSIS}[\s.]*{ELLIPSIS}")

# What the frame leg puts in `text` when a frame matched on imagery alone. It
# keeps the field non-null for a model reading a fixed shape; on the page it is
# a sentence pretending to be evidence, in the one place where the evidence is
# the picture (§6.3). Retyped rather than imported because the search tool does
# not export it — `test_public.py` asserts the two still agree.
FRAME_WITHOUT_TEXT = "visual match, no text hit"

_NOTE_PREFIX = re.compile(r"^\s*note:\s*", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")

# Notes that are an answer to a *model* and machinery to a reader. The demo
# drops them; every other caller keeps them, and the MCP tool never sees this
# module at all (demo-site.md §6.1).
#
# There is one, and it is the semantic-legs note: "no word of this query occurs
# anywhere in the corpus, so the semantic (nearest-neighbour) legs were not
# queried — they would have returned their k nearest vectors regardless." To an
# agent that is the reason a `0/0` payload is genuinely empty rather than
# under-searched, which is worth saying. Under a one-line "nothing matched" on a
# demo it is two clauses of query-layer internals explaining a *refusal*, and a
# demo does not need to argue about its own honesty (Tom, 2026-08-11).
#
# Matched on the clause, not the sentence: the wording belongs to the query
# layer and may be edited there. `test_public.py` asserts the tool still emits a
# note this matches, so a rewrite fails a test rather than silently putting the
# paragraph back on the page.
AGENT_ONLY_NOTES = ("semantic (nearest-neighbour) legs were not queried",)


def snippet(text: str | None, source: str | None = None) -> str | None:
    """One hit's text, as a reader should see it.

    Returns ``None`` when there is nothing a reader should see — which is not
    the same as an empty string: a frame hit that matched on imagery has no
    text, and the page renders no snippet at all rather than a stand-in
    sentence styled like a quotation.
    """
    if not text:
        return text
    cleaned = _WHITESPACE.sub(" ", text).strip()
    if source == "frame" and cleaned == FRAME_WITHOUT_TEXT:
        return None
    cleaned = _TRUNCATED.sub(ELLIPSIS, cleaned)
    cleaned = _DOUBLED.sub(ELLIPSIS, cleaned)
    return cleaned or None


def clip(text: str | None, limit: int) -> str:
    """One line of at most ``limit`` characters, cut at the end.

    For the places where a *label* is built out of corpus text — an activity
    line naming a query or a talk (§3.5) — rather than for a snippet. The cut is
    at the end, not in the middle, because the front of a title or a query is
    the part that identifies it and the line is read while it scrolls past.
    """
    cleaned = _WHITESPACE.sub(" ", text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(1, limit - 1)].rstrip() + ELLIPSIS


def note(text: str | None) -> str | None:
    """A `note:` line, as a sentence.

    The prefix tells a model "this line is machinery, not content"; a reader
    gets that from where it is rendered. Dropping it leaves the tool's own
    sentence, which is already English — so it is given a capital and left
    alone.
    """
    if not text:
        return text
    body = _WHITESPACE.sub(" ", _NOTE_PREFIX.sub("", text)).strip()
    if not body:
        return None
    return body[0].upper() + body[1:]


def is_agent_only(text: str | None) -> bool:
    """Is this note written for a model rather than for a reader?

    See :data:`AGENT_ONLY_NOTES`. Only the demo asks.
    """
    body = _WHITESPACE.sub(" ", text or "")
    return any(clause in body for clause in AGENT_ONLY_NOTES)


def notes(values: list[str] | None, *, demo: bool = False) -> list[str]:
    """Every note, humanised, with the empty ones dropped.

    ``demo=True`` also drops the ones written for a model (§6.1). It is opt-in
    rather than the default because the demo page is the one surface with that
    problem: an operator reading the dashboard's JSON is closer to the agent
    than to the visitor, and the MCP tool's own payload is untouched either way.
    """
    kept = (v for v in values or [] if not (demo and is_agent_only(v)))
    return [line for line in (note(v) for v in kept) if line]
