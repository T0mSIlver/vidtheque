"""Turn Claude's project memory into `project` entries on the vidtheque profile.

companion.md §2.2 (#159). Runs on the machine where Claude Code keeps its
memory. Three steps, and only the last one sends anything to vidtheque:

1. collect: each `~/.claude/projects/<dir>/memory/` with a file changed in the
   last 30 days gives its `MEMORY.md` index and its newest files, bounded. A
   project directory or memory file whose name or description hits the deny
   list is skipped before anything reads it.
2. extract: `claude -p` with no tools, no MCP servers and no settings reads
   that text on stdin and answers `{"projects": [{"text"}]}` against a schema.
3. check, then send: a topic must fit the profile's caps (32 characters,
   5 words), use only plain characters and miss the deny list and the vendor
   names. The survivors
   go to the `profile` tool as `kind=project`, weight 0.5, under a fixed
   reason, so the checked topic text is the only thing that leaves the memory.

A project written again refreshes its 30 days; one that stops appearing lapses.
Dry run by default: prints what it would send and why each candidate was kept
or refused. `--send` writes, and appends the run to a local log.

Usage:
    uv run scripts/memory_projects.py
    uv run scripts/memory_projects.py --send --url https://HOST/mcp --token-file ~/.config/vidtheque/token

The checks are the nightly GitHub pass's (`vidtheque_mcp.profile.topics`); the
deny list adds one term per line of `~/.config/vidtheque/memory-deny.txt`
(company and people names belong there).
Exit code: 0 on success, 1 when the model or the tool answered an error,
2 on transport failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vidtheque_mcp.profile import topics

RECENT_S = 30 * 86_400
# What the model reads, whatever the memory holds.
FILE_CHARS = 3_000
PROJECT_CHARS = 8_000
TOTAL_CHARS = 60_000
REASON = "a current project, from Claude's memory"
DENY_FILE = Path("~/.config/vidtheque/memory-deny.txt")
LOG_FILE = Path("~/.local/state/vidtheque/memory-projects.jsonl")

SYSTEM = (
    "You read a developer's notes from their coding projects and name what they "
    "are building now, as short topics for a video recommender.\n" + topics.RULES
)


@dataclass
class Memory:
    text: str
    projects: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# ------------------------------------------------------------------- collect


def collect(root: Path, terms: frozenset[str], now: float | None = None) -> Memory:
    """The recent memory of every project the deny list lets through, bounded."""
    now = time.time() if now is None else now
    memory = Memory(text="")
    parts: list[str] = []
    total = 0
    dirs = sorted(root.expanduser().glob("*/memory"), key=_newest, reverse=True)
    for directory in dirs:
        files = [f for f in directory.glob("*.md") if f.is_file()]
        if not files or now - max(f.stat().st_mtime for f in files) > RECENT_S:
            continue
        name = _project_name(directory.parent.name)
        hit = topics.denied(directory.parent.name.replace("-", " "), terms)
        if hit:
            memory.skipped.append(f"{name}: project name hits {hit!r}")
            continue
        body = _project_text(directory, files, terms, now, memory.skipped, name)
        if not body:
            continue
        block = f"## Project {name}\n{body}"[: min(PROJECT_CHARS, TOTAL_CHARS - total)]
        if not block.strip():
            break
        parts.append(block)
        memory.projects.append(name)
        total += len(block)
    memory.text = "\n\n".join(parts)
    return memory


def _project_text(
    directory: Path, files: list[Path], terms: frozenset[str], now: float, skipped: list[str], name: str
) -> str:
    index = directory / "MEMORY.md"
    chunks: list[str] = []
    if index.is_file():
        # An index line names its file; a line whose words hit the list goes with it.
        lines = [
            line
            for line in index.read_text(encoding="utf-8", errors="replace").splitlines()
            if not topics.denied(line, terms)
        ]
        chunks.append("\n".join(lines)[:FILE_CHARS])
    recent = sorted(
        (f for f in files if f.name != "MEMORY.md" and now - f.stat().st_mtime <= RECENT_S),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    for path in recent:
        text = path.read_text(encoding="utf-8", errors="replace")
        hit = topics.denied(path.stem.replace("-", " ").replace("_", " "), terms) or topics.denied(
            _description(text), terms
        )
        if hit:
            skipped.append(f"{name}/{path.name}: hits {hit!r}")
            continue
        chunks.append(f"### {path.stem}\n{text[:FILE_CHARS]}")
    return "\n\n".join(c for c in chunks if c.strip())


def _description(text: str) -> str:
    """The frontmatter's name and description, which say what a memory is about."""
    return " ".join(re.findall(r"^(?:name|description):(.*)$", text[:2_000], re.MULTILINE))


def _newest(directory: Path) -> float:
    return max((f.stat().st_mtime for f in directory.glob("*.md")), default=0.0)


def _project_name(encoded: str) -> str:
    """`-home-dev-work-vidtheque` → `vidtheque`: the path after the work folder."""
    name = re.sub(r"^-home-[^-]+-(?:work-)?", "", encoded)
    return name or encoded


# ------------------------------------------------------------------- extract


def extract(memory: str, model: str, claude: str = "claude") -> list[str]:
    """Ask Claude, with no tools and no MCP servers, for the topics; raises on failure."""
    command = [
        claude,
        "-p",
        "--model", model,
        "--tools", "",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--no-session-persistence",
        "--output-format", "json",
        "--json-schema", json.dumps(topics.SCHEMA),
        "--system-prompt", SYSTEM,
        "The notes follow on stdin.",
    ]
    done = subprocess.run(
        command, input=memory, capture_output=True, text=True, timeout=600, check=False
    )
    if done.returncode != 0:
        raise RuntimeError(f"claude exited {done.returncode}: {done.stderr.strip()[:500]}")
    answer = json.loads(done.stdout)
    if answer.get("is_error") or "structured_output" not in answer:
        raise RuntimeError(f"claude answered no topics: {str(answer.get('result'))[:500]}")
    return [str(p["text"]) for p in answer["structured_output"]["projects"]]


# ---------------------------------------------------------------------- send


async def call_profile(url: str, token: str | None, arguments: dict[str, Any]) -> Any:
    import httpx2 as httpx
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # Our own writes say nothing about what the owner is reading (companion.md §2.3).
    headers["X-Vidtheque-Signals"] = "off"
    async with httpx.AsyncClient(headers=headers, timeout=60.0) as http:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            return await client.call_tool("profile", arguments)


def log_run(path: Path, record: dict[str, Any]) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------- main


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", type=Path, default=Path("~/.claude/projects"))
    p.add_argument("--model", default="sonnet")
    p.add_argument("--url", default=os.environ.get("VIDTHEQUE_MCP_URL", "http://127.0.0.1:8100/mcp"))
    p.add_argument("--token-file", type=Path, default=None, help="a file holding the bearer token")
    p.add_argument("--send", action="store_true", help="write to the profile; a dry run otherwise")
    p.add_argument("--show-input", action="store_true", help="print the text the model would read")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    terms = topics.deny_terms(DENY_FILE)
    memory = collect(args.root, terms)
    print(f"read {len(memory.projects)} project(s): {', '.join(memory.projects) or 'none'}")
    for line in memory.skipped:
        print(f"skipped {line}")
    if args.show_input:
        print(memory.text)
    if not memory.text:
        return 0
    try:
        candidates = extract(memory.text, args.model)
    except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError) as exc:
        print(f"extract failed: {exc}", file=sys.stderr)
        return 1
    checked = topics.check(candidates, terms)
    for text, why in checked.refused:
        print(f"refused {text!r}: {why}")
    if not args.send:
        for text in checked.kept:
            print(f"would send {text!r}")
        return 0

    token = args.token_file.expanduser().read_text().strip() if args.token_file else None
    try:
        bare = asyncio.run(call_profile(args.url, token, {}))
        send, left_out = topics.fit(checked.kept, bare.structured_content["entries"])
        for text, why in left_out:
            print(f"left out {text!r}: {why}")
        result = None
        if send:
            adds = [{"text": t, "weight": topics.WEIGHT, "kind": "project"} for t in send]
            result = asyncio.run(call_profile(args.url, token, {"add": adds, "reason": REASON}))
    except Exception as exc:  # transport, protocol, auth
        print(f"transport failure: {exc}", file=sys.stderr)
        return 2
    if result is not None and result.is_error:
        print("\n".join(getattr(c, "text", "") for c in result.content), file=sys.stderr)
        return 1
    done = result.structured_content if result is not None else {}
    refreshed = done.get("refreshed", [])
    print(f"sent {len(send)}: {len(refreshed)} refreshed, {len(send) - len(refreshed)} added")
    log_run(
        LOG_FILE,
        {
            "at": int(time.time()),
            "projects_read": memory.projects,
            "sent": send,
            "refreshed": refreshed,
            "refused": checked.refused,
            "left_out": left_out,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
