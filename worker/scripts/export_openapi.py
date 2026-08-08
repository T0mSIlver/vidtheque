#!/usr/bin/env python3
"""Dump the worker's OpenAPI document to ``worker/openapi.json``.

That file is the contract the MCP service codes against — no Python import
crosses the two packages, so the schema is the only shared artefact. Run
``make openapi`` after changing a request or response shape.
"""

from __future__ import annotations

import json
from pathlib import Path

from vidtheque_worker.app import create_app

DESTINATION = Path(__file__).resolve().parents[1] / "openapi.json"


def main() -> None:
    schema = create_app().openapi()
    DESTINATION.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {DESTINATION} ({len(schema['paths'])} paths)")


if __name__ == "__main__":
    main()
