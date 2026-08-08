"""Call one vidtheque MCP tool (or read one resource) from the shell.

The smallest useful MCP client: speaks streamable HTTP to a running server,
prints the text blocks the model would see, then the structured content.
For interactive exploration use the MCP Inspector; this exists for scripts,
spot checks, and driving `index-video` without a chat client.

Usage:
    uv run scripts/mcp_call.py list-tools
    uv run scripts/mcp_call.py call index-video '{"url": "https://www.youtube.com/watch?v=..."}'
    uv run scripts/mcp_call.py call job-status '{"job_id": "..."}'
    uv run scripts/mcp_call.py call search '{"q": "agent loops", "limit": 5}'
    uv run scripts/mcp_call.py resource vidtheque://corpus
    uv run scripts/mcp_call.py --url http://127.0.0.1:8100/mcp --token "$TOKEN" call search '{"q": "..."}'

Exit code: 0 on success, 1 when the tool answered is_error, 2 on transport
failure. Not part of `make test`; needs a running server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8100/mcp", help="MCP endpoint (streamable HTTP)")
    p.add_argument("--token", default=None, help="bearer token (token/oauth auth modes)")
    p.add_argument("--timeout", type=float, default=900.0, help="per-request read timeout, seconds")
    p.add_argument("--json", action="store_true", help="print the raw payload as JSON only")
    sub = p.add_subparsers(dest="mode", required=True)
    sub.add_parser("list-tools")
    c = sub.add_parser("call")
    c.add_argument("tool")
    c.add_argument("args", nargs="?", default="{}", help="tool arguments as a JSON object")
    r = sub.add_parser("resource")
    r.add_argument("uri")
    return p.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    import httpx2 as httpx
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    async with httpx.AsyncClient(headers=headers, timeout=args.timeout) as http:
        async with Client(
            streamable_http_client(args.url, http_client=http),
            read_timeout_seconds=args.timeout,
        ) as client:
            if args.mode == "list-tools":
                tools = await client.list_tools()
                for tool in tools.tools:
                    line = (tool.description or "").splitlines()[0][:100]
                    print(f"{tool.name:<22} {line}")
                return 0

            if args.mode == "resource":
                result = await client.read_resource(args.uri)
                for content in result.contents:
                    print(getattr(content, "text", "") or "")
                return 0

            try:
                tool_args: dict[str, Any] = json.loads(args.args)
            except json.JSONDecodeError as exc:
                print(f"arguments are not valid JSON: {exc}", file=sys.stderr)
                return 2
            result = await client.call_tool(args.tool, tool_args)
            texts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
            images = [
                {"mimeType": c.mimeType, "bytes": len(c.data)}
                for c in result.content
                if getattr(c, "type", "") == "image"
            ]
            if args.json:
                print(json.dumps(
                    {
                        "is_error": bool(result.is_error),
                        "text": "\n".join(texts),
                        "images": images,
                        "structured": result.structured_content,
                    },
                    indent=2,
                    default=str,
                ))
            else:
                print("\n".join(texts))
                if images:
                    print(f"\n[image blocks] {json.dumps(images)}")
                if result.structured_content:
                    print(f"\nstructured: {json.dumps(result.structured_content, default=str)}")
            return 1 if result.is_error else 0


def main() -> int:
    args = parse_args(sys.argv[1:])
    try:
        return asyncio.run(run(args))
    except Exception as exc:  # transport, protocol, auth
        print(f"transport failure: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
