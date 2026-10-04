"""The routine that turns Claude's project memory into profile projects (#159).

What it may send is the trust boundary: these pin the filters, not the model.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "memory_projects", Path(__file__).parents[2] / "scripts" / "memory_projects.py"
)
assert _SPEC is not None and _SPEC.loader is not None
mp = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = mp  # dataclasses look their module up
_SPEC.loader.exec_module(mp)

TERMS = mp.DENY_TERMS | {"acme corp"}


def memory(root: Path, project: str, files: dict[str, str], age_days: float = 0) -> None:
    directory = root / project / "memory"
    directory.mkdir(parents=True)
    at = time.time() - age_days * 86_400
    for name, text in files.items():
        path = directory / name
        path.write_text(text)
        os.utime(path, (at, at))


def test_collect_skips_denied_and_stale_projects_and_files_unread(tmp_path: Path) -> None:
    memory(tmp_path, "-home-dev-work-vidtheque", {
        "MEMORY.md": "- [Feed](feed.md) — ranking work\n- [Prep](prep.md) — interview prep\n",
        "feed.md": "---\nname: feed\ndescription: ranking the feed\n---\nCross-encoder reranking.",
        "prep.md": "---\nname: prep\ndescription: interview prep\n---\nSECRET-PREP",
    })
    memory(tmp_path, "-home-dev-work-job-search", {"MEMORY.md": "SECRET-SEARCH"})
    memory(tmp_path, "-home-dev-work-old", {"MEMORY.md": "SECRET-OLD"}, age_days=40)

    read = mp.collect(tmp_path, TERMS)

    assert read.projects == ["vidtheque"]
    assert "Cross-encoder reranking." in read.text
    assert "SECRET" not in read.text and "interview" not in read.text


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("Interview prep in Kotlin", "hits 'interview'"),
        ("Acme Corp data platform", "hits 'acme corp'"),
        ("A very long project name over the cap", "over 32 characters or 5 words"),
        ("Pay: 120k€", "characters outside letters, digits and . + # / -"),
    ],
)
def test_check_refuses_what_must_not_leave(text: str, why: str) -> None:
    checked = mp.check([text, ".NET file storage", "llama.cpp on a 3090"], TERMS)
    assert checked.refused == [(text, why)]
    assert checked.kept == [".NET file storage", "llama.cpp on a 3090"]


def test_fit_refreshes_known_projects_and_adds_only_what_fits() -> None:
    live = [{"text": f"p{i}", "kind": "project"} for i in range(9)] + [
        {"text": "Local inference", "kind": "topic"}
    ]
    send, out = mp.fit(["P0", "Local inference", "new one", "new two"], live)
    assert send == ["P0", "new one"]
    assert out == [("Local inference", "already a topic"), ("new two", "no room for another project")]
