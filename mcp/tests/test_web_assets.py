"""The Next.js front end in ``web/`` is a separate deployable, so it carries
its own copy of the two vendored faces, and its own copy of the mark. DESIGN.md
(Fonts, rule 1) makes ``public/static/fonts/`` the document of record; this is
what stops the copies drifting, the same way the palette test stops the
dashboard and the demo becoming two visual worlds."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

REPO = Path(__file__).resolve().parents[2]
RECORD = REPO / "mcp/src/vidtheque_mcp/public/static/fonts"
WEB = REPO / "web/src/fonts"
DASHBOARD_BASE = REPO / "mcp/src/vidtheque_mcp/dashboard/templates/base.html"
WEB_ICON = REPO / "web/src/app/icon.svg"

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


def test_the_two_front_ends_wear_the_same_mark() -> None:
    """Tom's `v` and its dot, drawn once and served twice.

    The dashboard inlines it as a `data:` URI in its `<head>` (which is what
    ``test_the_favicon_is_the_v_and_carries_no_ground`` pins the properties of);
    the Next front end serves the same drawing as a file, ``app/icon.svg``. Two
    tab strips wearing two marks would be two products, so the drawings are
    compared rather than the bytes: the inline copy percent-encodes its `#`
    and quotes attributes with `'`, which is transport, not design.
    """
    inline = re.search(
        r"""<link rel="icon" href="data:image/svg\+xml,([^"]+)\"""",
        DASHBOARD_BASE.read_text(),
    )
    assert inline, "the dashboard's inline data: icon"
    assert ET.canonicalize(unquote(inline.group(1)), strip_text=True) == ET.canonicalize(
        WEB_ICON.read_text(), strip_text=True
    )
