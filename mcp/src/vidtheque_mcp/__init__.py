"""vidtheque MCP server — placeholder package.

The framework choice is still open (see ``NOTE.md``). This module exists so the
uv workspace, the container build and CI all have something to resolve.

Hard rule: nothing here may import from ``vidtheque_worker``. The two services
talk over HTTP, and the worker's OpenAPI document is the contract.
"""

__version__ = "0.0.1"

__all__ = ["__version__"]
