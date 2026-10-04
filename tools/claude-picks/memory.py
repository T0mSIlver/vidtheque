"""Print what the picks routine may read of Claude's memory (companion.md §6.4).

The same collection `scripts/memory_projects.py` makes for the profile: the
memory of projects changed in the last 30 days, bounded, with every project,
file and index line that hits the deny list (job search, pay, employers, plus
`~/.config/vidtheque/memory-deny.txt`) left out unread. The routine reads this
file, never the memory directories themselves.

Usage: uv run tools/claude-picks/memory.py > ~/.local/state/vidtheque/claude-picks/memory.md
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from vidtheque_mcp.profile import topics

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "memory_projects.py"


def main() -> int:
    spec = importlib.util.spec_from_file_location("memory_projects", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    memory = module.collect(Path("~/.claude/projects"), topics.deny_terms(module.DENY_FILE))
    print(memory.text)
    print(f"{len(memory.projects)} project(s) read, {len(memory.skipped)} skipped unread.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
