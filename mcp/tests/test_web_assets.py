"""The Next.js front end in ``web/`` is a separate deployable, so it carries
its own copy of the two vendored faces. DESIGN.md (Fonts, rule 1) makes
``public/static/fonts/`` the document of record; this is what stops the copy
drifting, the same way the palette test stops the dashboard and the demo
becoming two visual worlds."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RECORD = REPO / "mcp/src/vidtheque_mcp/public/static/fonts"
WEB = REPO / "web/src/fonts"

FACES = (
    "archivo-latin-wght-normal.woff2",
    "jetbrains-mono-latin-wght-normal.woff2",
)


def test_the_web_app_carries_the_document_of_record_fonts_byte_for_byte() -> None:
    for name in FACES:
        assert (WEB / name).read_bytes() == (RECORD / name).read_bytes(), name


def test_the_licences_travel_with_the_copies() -> None:
    for name in ("Archivo-OFL.txt", "JetBrainsMono-OFL.txt"):
        assert (WEB / name).read_text() == (RECORD / name).read_text(), name
