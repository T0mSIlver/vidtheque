"""Every env var the code reads has an entry in deploy/.env.example.

AGENTS.md makes that file the document of record, so a new variable without an
entry is a bug. The worker is parsed as source, never imported: mcp/ tests
stay on their side of the HTTP boundary.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MCP_SRC = ROOT / "mcp" / "src"
WORKER_SRC = ROOT / "worker" / "src"
WORKER_CONFIG = WORKER_SRC / "vidtheque_worker" / "config.py"
DEPLOY = ROOT / "deploy"
ENV_EXAMPLE = DEPLOY / ".env.example"

# mcp's config readers (config._env and the typed wrappers around it) and
# dashboard's CIDR-list reader; each takes the variable name first.
_READERS = {"_env", "_int_env", "_float_env", "_clamped_float_env", "_bool_env", "_cidrs"}

# Read by the code but set by the runtime or the platform, not by an operator.
ALLOWLIST: dict[str, str] = {}


def _spellings(name: str) -> frozenset[str]:
    """The names one setting answers to; documenting any of them is enough.

    config._env accepts FOO and VIDTHEQUE_FOO, so the two spell one setting.
    """
    bare = name.removeprefix("VIDTHEQUE_")
    return frozenset({bare, f"VIDTHEQUE_{bare}"})


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_os_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _names_in_source(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args:
            first = _const_str(node.args[0])
            func = node.func
            if first is None:
                continue
            if isinstance(func, ast.Name) and func.id in _READERS:
                names.add(first)
            elif isinstance(func, ast.Attribute) and (
                (func.attr in {"get", "pop", "setdefault"} and _is_os_environ(func.value))
                or (
                    func.attr == "getenv"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "os"
                )
            ):
                names.add(first)
        elif isinstance(node, ast.Subscript) and _is_os_environ(node.value):
            key = _const_str(node.slice)
            if key is not None:
                names.add(key)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A bare "VIDTHEQUE_…" literal is a variable name read through a
            # loop or a helper the reader set above does not know about.
            if re.fullmatch(r"VIDTHEQUE_[A-Z0-9_]+", node.value):
                names.add(node.value)
    return names


def _worker_settings_names(tree: ast.AST) -> set[str]:
    """Fields of the worker's pydantic Settings: alias names, else the prefixed field."""
    names: set[str] = set()
    for cls in ast.walk(tree):
        if not (isinstance(cls, ast.ClassDef) and cls.name == "Settings"):
            continue
        for stmt in cls.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            alias = None
            if isinstance(stmt.value, ast.Call):
                alias = next(
                    (k.value for k in stmt.value.keywords if k.arg == "validation_alias"), None
                )
            if isinstance(alias, ast.Call) and alias.args:
                names.update(s for a in alias.args if (s := _const_str(a)) is not None)
            else:
                names.add(f"VIDTHEQUE_{stmt.target.id.upper()}")
    return names


def _compose_names() -> set[str]:
    names: set[str] = set()
    for path in [*DEPLOY.glob("*.yml"), DEPLOY / "Caddyfile"]:
        # Comment lines quote retired or example variables (MCP_PORT, {$NAME}).
        text = "\n".join(
            line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
        )
        names.update(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", text))  # compose
        names.update(re.findall(r"\{\$([A-Z][A-Z0-9_]*)", text))  # caddy
    return names


def _read_names() -> dict[str, str]:
    """Every variable name read, mapped to one place that reads it."""
    found: dict[str, str] = {}
    for src in (MCP_SRC, WORKER_SRC):
        for path in sorted(src.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            names = _names_in_source(tree)
            if path == WORKER_CONFIG:
                names |= _worker_settings_names(tree)
            for name in names:
                found.setdefault(name, str(path.relative_to(ROOT)))
    for name in _compose_names():
        found.setdefault(name, "deploy/*.yml or deploy/Caddyfile")
    return found


def _documented() -> set[str]:
    # An entry is `NAME=`, commented out or not.
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", ENV_EXAMPLE.read_text(), re.MULTILINE))


def test_the_collector_sees_each_kind_of_read() -> None:
    names = _read_names()
    for expected in ("VIDTHEQUE_DATA_DIR", "STT_BACKEND", "VIDTHEQUE_WORKER_PORT", "EDGE_PORT"):
        assert expected in names


def test_every_env_var_read_has_an_entry_in_env_example() -> None:
    documented = _documented()
    missing = sorted(
        f"{name} ({where})"
        for name, where in _read_names().items()
        if name not in ALLOWLIST and not (_spellings(name) & documented)
    )
    assert not missing, "undocumented in deploy/.env.example:\n  " + "\n  ".join(missing)
