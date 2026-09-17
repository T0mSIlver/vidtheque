"""Which tools a read-only deployment does not register — demo-site.md §1.1.

The policy lives here rather than in ``tools/``: the tools package describes
the surface, this package decides which deployment gets which part of it.

**Two axes, and only the first is derived.** A tool that declares
``readOnlyHint: False`` in the contract is a write tool by definition, so an
eleventh one is masked the day it is added and there is no second list to keep
in sync with the annotations.

The second axis is *bulk*, and it cannot be derived from an annotation because
nothing in the contract says how much of the corpus one call hands over.
``get-transcript`` is read-only and idempotent and still must not exist on a
public instance: it is the model-facing half of ``GET /videos/<id>/export.md``,
which that deployment already gates on proved ownership rather than on the read
gate (``http/export.py``). The demo runs ``VIDTHEQUE_AUTH=none``, so every
request is ``"open"`` — a surface that lets an anonymous caller page an entire
transcript, then the next one, is a bulk download of the corpus with a tool
description on it. ``demo-site.md`` §1.1 said "never a full transcript" about
the pages; the tool surface owes the same answer.

So the names below are written down, deliberately, and the one thing that keeps
them honest is that each is here for a stated reason rather than a category.
"""

from __future__ import annotations

from ..tools.descriptions import ANNOTATIONS

WRITE_TOOLS: frozenset[str] = frozenset(
    name for name, annotation in ANNOTATIONS.items() if not annotation.read_only_hint
)

# Read-only, and still absent from a public deployment: one call hands over an
# artifact the owner's gate exists to protect. See the module docstring.
OWNER_ONLY_TOOLS: frozenset[str] = frozenset({"get-transcript"})


def hidden_tools(public_readonly: bool) -> frozenset[str]:
    """The names ``register()`` must skip for this deployment."""
    return WRITE_TOOLS | OWNER_ONLY_TOOLS if public_readonly else frozenset()
