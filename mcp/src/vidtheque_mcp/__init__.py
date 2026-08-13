"""vidtheque MCP server — the CPU half of vidtheque.

Owns the SQLite + sqlite-vec + FTS5 index, the keyframe directory, the job
queue, the OAuth authorization server and the nine-tool MCP surface described
in ``docs/design/tool-surface.md``.

Hard rule (CLAUDE.md): nothing here may import from ``vidtheque_worker``. The
two services talk over HTTP, and the worker's OpenAPI document is the contract.
"""

__version__ = "0.0.3"

__all__ = ["__version__"]
