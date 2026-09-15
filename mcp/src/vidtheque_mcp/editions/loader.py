"""Load the fields the context-bias builder needs from a committed edition."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any


class EditionError(ValueError):
    """A committed edition cannot satisfy its schema."""


@dataclass(frozen=True, slots=True)
class Speaker:
    name: str
    company: str


@dataclass(frozen=True, slots=True)
class Session:
    title: str
    speakers: tuple[Speaker, ...]


@dataclass(frozen=True, slots=True)
class Edition:
    slug: str
    edition_tag: str
    fixed_context_bias: tuple[str, ...]
    sessions: tuple[Session, ...]


def _string(value: Any, field: str, *, maximum: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EditionError(f"{field} must be a non-empty string")
    value = value.strip()
    if maximum is not None and len(value) > maximum:
        raise EditionError(f"{field} must be at most {maximum} characters")
    return value


def _parse(raw: Any, expected_slug: str) -> Edition:
    if not isinstance(raw, dict):
        raise EditionError("the edition root must be an object")
    if raw.get("schema_version") != 1:
        raise EditionError("schema_version must be 1")
    slug = _string(raw.get("slug"), "slug")
    if slug != expected_slug:
        raise EditionError(f"slug must be {expected_slug!r}")

    tags = raw.get("tags")
    if not isinstance(tags, dict):
        raise EditionError("tags must be an object")
    edition_tag = _string(tags.get("edition"), "tags.edition")

    context_bias = raw.get("context_bias")
    if not isinstance(context_bias, dict) or not isinstance(context_bias.get("fixed"), list):
        raise EditionError("context_bias.fixed must be an array")
    fixed = tuple(
        _string(value, f"context_bias.fixed[{index}]", maximum=100)
        for index, value in enumerate(context_bias["fixed"])
    )

    raw_sessions = raw.get("sessions")
    if not isinstance(raw_sessions, list) or not raw_sessions:
        raise EditionError("sessions must be a non-empty array")
    if len(raw_sessions) > 100:
        raise EditionError("sessions must contain at most 100 entries")

    sessions: list[Session] = []
    for session_index, item in enumerate(raw_sessions):
        field = f"sessions[{session_index}]"
        if not isinstance(item, dict):
            raise EditionError(f"{field} must be an object")
        title = _string(item.get("title"), f"{field}.title", maximum=256)
        raw_speakers = item.get("speakers")
        if not isinstance(raw_speakers, list) or not raw_speakers:
            raise EditionError(f"{field}.speakers must be a non-empty array")
        speakers: list[Speaker] = []
        for speaker_index, speaker in enumerate(raw_speakers):
            speaker_field = f"{field}.speakers[{speaker_index}]"
            if not isinstance(speaker, dict):
                raise EditionError(f"{speaker_field} must be an object")
            speakers.append(
                Speaker(
                    name=_string(speaker.get("name"), f"{speaker_field}.name", maximum=100),
                    company=_string(
                        speaker.get("company"), f"{speaker_field}.company", maximum=100
                    ),
                )
            )
        sessions.append(Session(title=title, speakers=tuple(speakers)))

    return Edition(
        slug=slug,
        edition_tag=edition_tag,
        fixed_context_bias=fixed,
        sessions=tuple(sessions),
    )


def load_edition(slug: str = "aie-paris-2026") -> Edition:
    """Load and validate one packaged edition fixture."""
    name = f"{slug}.json"
    try:
        text = resources.files(__package__).joinpath(name).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise EditionError(f"unknown edition {slug!r}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EditionError(f"{name}: invalid JSON at line {exc.lineno}") from exc
    return _parse(raw, slug)
