"""Which tools a read-only deployment does not register — demo-site.md §1.1.

The policy lives here rather than in ``tools/``: the tools package describes
the surface, this package decides which deployment gets which part of it.

The list is *derived*, not written down. A tool that declares
``readOnlyHint: False`` in the contract is a write tool by definition, so a
tenth one is masked the day it is added and there is no second list to keep in
sync with the annotations.
"""

from __future__ import annotations

from ..tools.descriptions import ANNOTATIONS

WRITE_TOOLS: frozenset[str] = frozenset(
    name for name, annotation in ANNOTATIONS.items() if not annotation.read_only_hint
)


def hidden_tools(public_readonly: bool) -> frozenset[str]:
    """The names ``register()`` must skip for this deployment."""
    return WRITE_TOOLS if public_readonly else frozenset()
