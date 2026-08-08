#!/usr/bin/env python3
"""End-to-end CPU smoke: worker + MCP server + a real YouTube video.

    uv run python scripts/e2e_smoke.py                    # default video
    uv run python scripts/e2e_smoke.py --url https://youtu.be/<id>
    uv run python scripts/e2e_smoke.py --channels transcript,ocr   # no SigLIP

**This is not part of `make test` and must never be.** It downloads model
weights (Qwen3-Embedding-0.6B ~1.2 GB, SigLIP 2 so400m ~4.3 GB), hits YouTube
through yt-dlp, and takes minutes. `make test` is CPU-only, offline, and
download-free — keep it that way.

What it does, in order:

1. Makes a CPU inference venv for `worker/` if one is missing (torch CPU
   wheels, sentence-transformers, transformers, rapidocr). whisperX is
   deliberately *not* installed: this run exercises the documented zero-GPU
   fallback, where `/v1/audio/transcriptions` answers 503 and the STT policy
   drops to YouTube auto-captions.
2. Creates a fresh data dir, applies the migrations, and (see `--align-config`)
   rewrites the `config` model identifiers to the ones `deploy/.env.example`
   makes the worker serve.
3. Boots `vidtheque-worker` (CPU) and `vidtheque-mcp` (`VIDTHEQUE_AUTH=none`,
   localhost), each in its own process with its own environment.
4. Drives the **real MCP surface** over streamable HTTP with the `mcp` package's
   client: `index-video`, `job-status` until done, then `search` (transcript,
   OCR and frame legs), `video-summary`, `get-segment-context`, `get-frames`
   in both `url` and `image` mode, `list-videos`, the three resources, and one
   deliberately-bad call to see the error contract.
5. Verifies the signed frame URL with `curl` and checks the bytes are a JPEG.
6. Collects timings (per stage, from `video_stages`), DB size, keyframe count
   and bytes, and writes everything to `<data-dir>/smoke-report.json` plus a
   readable transcript on stdout.

Each run gets its own data dir (`scripts/.smoke-data/run-<ts>` by default), so
runs never share a database. Nothing is ever deleted — the database, the
keyframes and both service logs are the evidence; `--keep` only silences the
reminder that they are still on disk.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DEFAULT_URL = "https://youtu.be/5C_HPTJg5ek"  # Fireship, "Rust in 100 Seconds", 2:29
DEFAULT_WORKER_VENV = Path.home() / ".cache" / "vidtheque-smoke" / "worker-venv"

# The worker's `gpu` extra minus whisperX (which drags CUDA-shaped deps and is
# not what this path is testing) — installed with CPU torch wheels.
#
# transformers is pinned to the version `uv.lock` resolves (whisperX caps
# huggingface-hub, which caps transformers at 4.x). Do not float it: on
# transformers 5.x `Siglip2Model.get_image_features` / `get_text_features`
# return `BaseModelOutputWithPooling` instead of a tensor and both SigLIP
# endpoints 500 — see research/e2e-smoke-2026-08-08.md.
WORKER_CPU_DEPS = (
    "sentence-transformers>=3.0",
    "transformers==4.57.6",
    "rapidocr==3.9.2",
    "onnxruntime==1.28.0",
    "pillow>=10.0",
    "numpy>=1.26",
)

# `config` ships short model names (db/migrations/0001_initial.sql) while
# deploy/.env.example ships the HF ids the worker reports back. The pipeline's
# drift check compares the two strings, so with the shipped defaults every
# embedding stage is skipped. See the report for the bug write-up.
CONFIG_ALIGNMENT = {
    "text_embed.model": "Qwen/Qwen3-Embedding-0.6B",
    "frame_embed.model": "google/siglip2-so400m-patch16-naflex",
    "ocr.model": "rapidocr-default",
}


# --------------------------------------------------------------------------- io


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n== {title}\n{'=' * 78}", flush=True)


def trim(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [{len(text) - limit} more chars]"


# ------------------------------------------------------------------ environment


def ensure_worker_venv(python: Path, install: bool) -> Path:
    """A venv with the CPU inference stack. Created on first run."""
    if python.exists():
        return python
    if not install:
        raise SystemExit(f"{python} does not exist (pass --install-worker-deps)")
    venv = python.parent.parent
    log(f"creating worker venv at {venv} (CPU torch — several minutes on a cold cache)")
    subprocess.run(["uv", "venv", "--python", "3.12", str(venv)], check=True, cwd=REPO)
    subprocess.run(
        [
            "uv", "pip", "install",
            "--python", str(python),
            "--torch-backend=cpu",
            str(REPO / "worker"),
            *WORKER_CPU_DEPS,
        ],
        check=True,
        cwd=REPO,
    )
    return python


def clean_env(extra: dict[str, str]) -> dict[str, str]:
    """A child's environment, with the caller's vidtheque settings stripped.

    Both halves read bare names as well as `VIDTHEQUE_`-prefixed ones, so an
    inherited `WORKER_URL` or `DEVICE` from the operator's shell would silently
    reconfigure a run that is supposed to be reproducible.
    """
    drop = ("VIDTHEQUE_", "WORKER_", "STT_", "EMBED_", "IMAGE_EMBED_", "OCR_")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(drop) and k not in {"PUBLIC_URL", "DEVICE", "COMPUTE_TYPE"}
    }
    env.update(extra)
    return env


# ---------------------------------------------------------------------- process


@dataclass
class Service:
    name: str
    proc: subprocess.Popen
    log_path: Path

    def tail(self, n: int = 40) -> str:
        try:
            lines = self.log_path.read_text(errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-n:])

    def stop(self) -> None:
        if self.proc.poll() is not None:
            return
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def spawn(name: str, argv: list[str], env: dict[str, str], log_path: Path) -> Service:
    handle = log_path.open("w")
    proc = subprocess.Popen(argv, cwd=REPO, env=env, stdout=handle, stderr=subprocess.STDOUT)
    return Service(name=name, proc=proc, log_path=log_path)


def wait_http(url: str, service: Service, timeout: float = 120.0) -> float:
    """Block until `url` answers 2xx. Returns seconds waited."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if service.proc.poll() is not None:
            raise SystemExit(
                f"{service.name} exited with {service.proc.returncode}:\n{service.tail(60)}"
            )
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if 200 <= resp.status < 300:
                    return time.monotonic() - started
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    raise SystemExit(f"{service.name} did not become healthy:\n{service.tail(60)}")


# --------------------------------------------------------------------- database


async def create_database(data_dir: Path) -> None:
    """Apply the migrations by opening the real `Database`, then close it."""
    from vidtheque_mcp.db import Database

    db = Database(path=data_dir / "vidtheque.db", read_pool_size=1, query_budget_s=30.0)
    await db.open()
    await db.close()


def align_config(db_path: Path) -> dict[str, tuple[str, str]]:
    """Point `config` at the model ids the worker actually reports."""
    changed: dict[str, tuple[str, str]] = {}
    conn = sqlite3.connect(db_path)
    try:
        for key, value in CONFIG_ALIGNMENT.items():
            row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
            if row is None or row[0] == value:
                continue
            conn.execute(
                "UPDATE config SET value = ?, updated_at = unixepoch() WHERE key = ?",
                (value, key),
            )
            changed[key] = (row[0], value)
        conn.commit()
    finally:
        conn.close()
    return changed


def db_facts(db_path: Path, data_dir: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    # vec_chunks/vec_frames are vec0 virtual tables: without the extension even
    # `count(*)` fails with "no such module: vec0".
    import sqlite_vec

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    out: dict[str, Any] = {}
    try:
        def scalar(sql: str) -> Any:
            return conn.execute(sql).fetchone()[0]

        out["counts"] = {
            table: scalar(f"SELECT count(*) FROM {table}")
            for table in (
                "videos", "chapters", "cues", "chunks", "keyframes", "ocr_lines",
                "vec_chunks", "vec_frames", "jobs", "job_items", "job_events",
            )
        }
        out["config"] = {
            row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM config")
        }
        out["stages"] = [
            dict(row)
            for row in conn.execute(
                "SELECT stage, state, model_key, started_at, finished_at, error "
                "FROM video_stages ORDER BY started_at, stage"
            )
        ]
        out["events"] = [
            dict(row)
            for row in conn.execute(
                "SELECT at, level, stage, message FROM job_events ORDER BY id"
            )
        ]
        out["keyframes"] = dict(
            conn.execute(
                "SELECT count(*) AS n, coalesce(sum(jpeg_bytes), 0) AS bytes, "
                "sum(dup_of IS NOT NULL) AS dups, sum(ocr_state = 'done') AS ocr_done "
                "FROM keyframes"
            ).fetchone()
        )
        out["video"] = dict(
            conn.execute(
                "SELECT public_id, title, channel_name, duration_s, index_state, language, "
                "length(coalesce(heatmap_json, '')) AS heatmap_chars FROM videos LIMIT 1"
            ).fetchone()
            or {}
        )
    finally:
        conn.close()

    out["sizes_bytes"] = {
        "vidtheque.db": _size(db_path),
        "db_wal": _size(db_path.with_name(db_path.name + "-wal")),
        "keyframes_dir": _tree_size(data_dir / "keyframes"),
        "audio_dir": _tree_size(data_dir / "audio"),
        "media_dir": _tree_size(data_dir / "media"),
        "data_dir": _tree_size(data_dir),
    }
    return out


def _size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _tree_size(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


# ------------------------------------------------------------------ MCP driving


@dataclass
class Recorder:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def add(self, kind: str, name: str, args: Any, seconds: float, payload: Any) -> None:
        self.calls.append(
            {
                "kind": kind,
                "name": name,
                "args": args,
                "seconds": round(seconds, 3),
                "payload": payload,
            }
        )


def result_payload(result: Any) -> dict[str, Any]:
    texts: list[str] = []
    images: list[dict[str, Any]] = []
    for block in result.content or []:
        if getattr(block, "type", None) == "text":
            texts.append(block.text)
        elif getattr(block, "type", None) == "image":
            images.append(
                {
                    "mime_type": block.mime_type,
                    "base64_len": len(block.data or ""),
                    "base64_head": (block.data or "")[:24],
                }
            )
    return {
        "is_error": bool(getattr(result, "is_error", False)),
        "text": "\n".join(texts),
        "images": images,
        "structured": result.structured_content,
    }


async def call(client: Any, rec: Recorder, name: str, args: dict[str, Any] | None = None,
               *, show: bool = True, limit: int = 2500) -> dict[str, Any]:
    started = time.monotonic()
    result = await client.call_tool(name, args or {})
    payload = result_payload(result)
    rec.add("tool", name, args or {}, time.monotonic() - started, payload)
    if show:
        section(f"tool {name} {json.dumps(args or {}, default=str)}")
        print(trim(payload["text"], limit))
        if payload["images"]:
            print(f"\n[image blocks] {json.dumps(payload['images'])}")
        if payload["structured"]:
            print(f"\nstructuredContent: {trim(json.dumps(payload['structured'], default=str), 800)}")
        print(f"\n(is_error={payload['is_error']}, {rec.calls[-1]['seconds']}s)")
    return payload


async def read_resource(client: Any, rec: Recorder, uri: str, limit: int = 1200) -> str:
    started = time.monotonic()
    result = await client.read_resource(uri)
    texts = [getattr(c, "text", "") or "" for c in result.contents]
    body = "\n".join(texts)
    rec.add("resource", uri, {}, time.monotonic() - started, {"text": body})
    section(f"resource {uri}")
    print(trim(body, limit))
    return body


async def poll_job(client: Any, rec: Recorder, job_id: str, timeout: float) -> dict[str, Any]:
    """`job-status` until the job leaves the active states."""
    started = time.monotonic()
    seen: set[str] = set()
    last: dict[str, Any] = {}
    while time.monotonic() - started < timeout:
        last = await call(client, rec, "job-status", {"job_id": job_id}, show=False)
        state = str((last["structured"] or {}).get("state", "?"))
        pct = float((last["structured"] or {}).get("progress", 0.0))
        stamp = f"{state} {pct:.0%} " + " ".join(
            line.strip() for line in last["text"].splitlines() if "running" in line
        )
        if stamp not in seen:
            seen.add(stamp)
            log(f"  job {job_id}: {stamp}")
        if state in {"done", "failed", "cancelled"}:
            break
        await asyncio.sleep(5)
    section(f"tool job-status (final, after {time.monotonic() - started:.1f}s)")
    print(last["text"])
    return last


def curl_frame(url: str, out: Path) -> dict[str, Any]:
    """Fetch a signed frame URL the way a browser would — no MCP, no auth header."""
    proc = subprocess.run(
        ["curl", "-sS", "-D", "-", "-o", str(out), url],
        capture_output=True,
        text=True,
        timeout=60,
    )
    head = out.read_bytes()[:4] if out.exists() else b""
    return {
        "url": url,
        "curl_rc": proc.returncode,
        "headers": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "bytes": _size(out),
        "jpeg_magic": head[:3] == b"\xff\xd8\xff",
    }


# --------------------------------------------------------------------- the smoke


async def drive(args: argparse.Namespace, mcp_url: str, data_dir: Path) -> dict[str, Any]:
    from mcp.client import Client

    rec = Recorder()
    out: dict[str, Any] = {"calls": rec.calls}

    async with Client(mcp_url, read_timeout_seconds=args.request_timeout) as client:
        tools = await client.list_tools()
        section("tools/list")
        for tool in tools.tools:
            print(f"  {tool.name:<22} {(tool.description or '').splitlines()[0][:90]}")
        out["tools"] = [t.name for t in tools.tools]

        # ------------------------------------------------------------- index
        indexed = await call(
            client,
            rec,
            "index-video",
            # Tags are namespaced (`^[a-z0-9]+:[a-z0-9][a-z0-9._-]{0,63}$`); a
            # bare word is E_BAD_PARAM.
            {"url": args.url, "expand": "none", "tags": "topic:smoke", "channels": args.channels},
        )
        job_id = (indexed["structured"] or {}).get("job_id")
        if not job_id:
            raise SystemExit(f"no job created: {indexed['text']}")

        job_started = time.monotonic()
        final = await poll_job(client, rec, job_id, args.job_timeout)
        out["job_seconds"] = round(time.monotonic() - job_started, 1)
        out["job_state"] = (final["structured"] or {}).get("state")

        # ------------------------------------------------------------ library
        await call(client, rec, "list-videos", {})
        video_id = args.url.rstrip("/").split("/")[-1].split("?")[0]

        await call(client, rec, "video-summary", {"video_id": video_id})

        # -------------------------------------------------------------- search
        for label, params in (
            ("transcript", {"q": args.q_transcript, "content_type": "transcript", "limit": 5}),
            ("ocr", {"q": args.q_ocr, "content_type": "ocr", "limit": 5}),
            ("ocr_echo", {"q": args.q_ocr_echo, "content_type": "ocr", "limit": 5}),
            ("frame", {"q": args.q_frame, "content_type": "frame", "limit": 5}),
            ("all", {"q": args.q_transcript, "content_type": "all", "limit": 6}),
        ):
            payload = await call(client, rec, "search", params)
            out.setdefault("search", {})[label] = payload["text"]

        # ------------------------------------------ segment context at a hit
        hit_t = first_timestamp(out["search"].get("transcript", "")) or 30.0
        await call(
            client,
            rec,
            "get-segment-context",
            {"video_id": video_id, "t": hit_t, "window": 45, "include_links": True},
        )

        # ------------------------------------------------------------- frames
        url_frames = await call(
            client,
            rec,
            "get-frames",
            {"video_id": video_id, "t_start": max(0.0, hit_t - 10), "limit": 2},
        )
        signed = [
            token
            for token in url_frames["text"].split()
            if token.startswith("http") and "/frames/" in token
        ]
        if signed:
            out["frame_url_check"] = curl_frame(signed[0].rstrip(".,"), data_dir / "frame-check.jpg")
            section("curl of the signed frame URL")
            print(json.dumps(out["frame_url_check"], indent=2)[:1500])
        else:
            out["frame_url_check"] = {"error": "no frame URL in the get-frames output"}

        await call(
            client,
            rec,
            "get-frames",
            {"video_id": video_id, "t_start": max(0.0, hit_t - 10), "limit": 1, "return": "image"},
            limit=600,
        )

        # ---------------------------------------------------------- resources
        for uri in ("vidtheque://corpus", "vidtheque://context", "vidtheque://guide"):
            await read_resource(client, rec, uri)

        # ------------------------------------------------------- error contract
        await call(client, rec, "video-summary", {"video_id": "totally-bogus"})
        await call(client, rec, "search", {"q": "x", "content_type": "banana"})

    return out


async def check_token_mode(
    args: argparse.Namespace, data_dir: Path, worker_url: str, video_id: str
) -> dict[str, Any]:
    """Re-boot the same corpus with `VIDTHEQUE_AUTH=token` and prove the
    capability URL.

    In `none` mode `/frames` is open, so the interesting half of the design —
    an HMAC-signed URL a browser can fetch with no Authorization header, on a
    server that 401s everything else — is never exercised. This runs the same
    `get-frames` call behind a bearer and then fetches the URL it hands back
    with plain curl, no credentials.
    """
    import secrets

    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    token = secrets.token_urlsafe(24)
    mcp_url = f"http://127.0.0.1:{args.mcp_port}"
    service = spawn(
        "mcp-token",
        [sys.executable, "-m", "vidtheque_mcp"],
        clean_env(
            {
                "VIDTHEQUE_AUTH": "token",
                "VIDTHEQUE_TOKEN": token,
                "VIDTHEQUE_DATA_DIR": str(data_dir),
                "VIDTHEQUE_HOST": "127.0.0.1",
                "VIDTHEQUE_PORT": str(args.mcp_port),
                "PUBLIC_URL": mcp_url,
                "WORKER_URL": worker_url,
                "LOG_LEVEL": "info",
            }
        ),
        data_dir / "mcp-token.log",
    )
    out: dict[str, Any] = {}
    try:
        wait_http(f"{mcp_url}/healthz", service)
        rec = Recorder()
        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=60
        ) as http:
            async with Client(
                streamable_http_client(f"{mcp_url}/mcp", http_client=http)
            ) as client:
                payload = await call(
                    client, rec, "get-frames", {"video_id": video_id, "limit": 1}
                )
        signed = next(
            (
                tok.rstrip(".,")
                for tok in payload["text"].split()
                if tok.startswith("http") and "/frames/" in tok
            ),
            None,
        )
        out["signed_url"] = signed
        out["has_signature"] = bool(signed and "sig=" in signed and "exp=" in signed)
        if signed:
            out["signed_fetch"] = curl_frame(signed, data_dir / "frame-signed.jpg")
            bare = signed.split("?")[0]
            proc = subprocess.run(
                ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", bare],
                capture_output=True,
                text=True,
                timeout=30,
            )
            out["unsigned_status"] = proc.stdout.strip()
        section("token mode: signed frame URL")
        print(json.dumps({k: v for k, v in out.items() if k != "signed_fetch"}, indent=2))
        if "signed_fetch" in out:
            print(json.dumps(out["signed_fetch"], indent=2)[:900])
    finally:
        service.stop()
    return out


def first_timestamp(text: str) -> float | None:
    """Pull `?t=123` out of the first deep link in a search result."""
    import re

    match = re.search(r"[?&]t=(\d+)", text)
    return float(match.group(1)) if match else None


# ------------------------------------------------------------------------- main


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=DEFAULT_URL, help="video to index")
    p.add_argument("--channels", default="all", help="index-video channels (all | transcript,ocr | …)")
    p.add_argument("--data-dir", type=Path, default=None, help="default: scripts/.smoke-data/run-<ts>")
    p.add_argument("--keep", action="store_true",
                   help="silence the reminder that the data dir was left on disk")
    p.add_argument("--worker-python", type=Path, default=DEFAULT_WORKER_VENV / "bin" / "python")
    p.add_argument("--install-worker-deps", action="store_true", default=True)
    p.add_argument("--no-install-worker-deps", dest="install_worker_deps", action="store_false")
    p.add_argument("--worker-port", type=int, default=8391)
    p.add_argument("--mcp-port", type=int, default=8390)
    p.add_argument("--job-timeout", type=float, default=3600.0)
    p.add_argument("--request-timeout", type=float, default=900.0)
    p.add_argument("--align-config", action="store_true", default=True,
                   help="rewrite config model ids to the ones the worker serves (default on)")
    p.add_argument("--no-align-config", dest="align_config", action="store_false")
    p.add_argument("--token-mode-check", action="store_true", default=True,
                   help="re-boot in VIDTHEQUE_AUTH=token and verify the signed frame URL")
    p.add_argument("--no-token-mode-check", dest="token_mode_check", action="store_false")
    p.add_argument("--q-transcript", default="memory safety")
    # A term that is on screen and *not* narrated. The OCR leg drops any line
    # whose moment is covered by a longer transcript cue matching the same
    # query, so a word the presenter says out loud returns nothing here.
    p.add_argument("--q-ocr", default="Cargo.toml")
    # …and one that is both, to show that suppression in the output.
    p.add_argument("--q-ocr-echo", default="cargo")
    p.add_argument("--q-frame", default="terminal with code on screen")
    return p.parse_args(argv)


async def main(argv: list[str]) -> int:
    args = parse_args(argv)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    data_dir = args.data_dir or (REPO / "scripts" / ".smoke-data" / f"run-{stamp}")
    data_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "url": args.url,
        "channels": args.channels,
        "data_dir": str(data_dir),
    }
    log(f"data dir: {data_dir}")

    worker_python = ensure_worker_venv(args.worker_python, args.install_worker_deps)

    t0 = time.monotonic()
    await create_database(data_dir)
    if args.align_config:
        report["config_alignment"] = align_config(data_dir / "vidtheque.db")
        if report["config_alignment"]:
            log(f"config aligned to the worker's model ids: {report['config_alignment']}")
    report["db_bootstrap_seconds"] = round(time.monotonic() - t0, 2)

    worker_url = f"http://127.0.0.1:{args.worker_port}"
    mcp_url = f"http://127.0.0.1:{args.mcp_port}"

    worker = spawn(
        "worker",
        [str(worker_python), "-m", "vidtheque_worker"],
        clean_env(
            {
                "VIDTHEQUE_HOST": "127.0.0.1",
                "VIDTHEQUE_PORT": str(args.worker_port),
                "DEVICE": "cpu",
                "COMPUTE_TYPE": "int8",
                # Sequential stages on one box: let a model go before the next
                # one loads, or Qwen3 and SigLIP 2 are resident together.
                "VIDTHEQUE_IDLE_UNLOAD_SECONDS": "20",
                "VIDTHEQUE_OCR_THREADS": str(min(8, os.cpu_count() or 4)),
                "VIDTHEQUE_IMAGE_EMBED_MAX_PATCHES": "256",
                "LOG_LEVEL": "info",
            }
        ),
        data_dir / "worker.log",
    )
    services = [worker]
    try:
        report["worker_boot_seconds"] = round(wait_http(f"{worker_url}/healthz", worker), 2)
        log(f"worker up on {worker_url} in {report['worker_boot_seconds']}s")
        with urllib.request.urlopen(f"{worker_url}/status", timeout=10) as resp:
            report["worker_status"] = json.loads(resp.read())

        mcp = spawn(
            "mcp",
            [sys.executable, "-m", "vidtheque_mcp"],
            clean_env(
                {
                    "VIDTHEQUE_AUTH": "none",
                    "VIDTHEQUE_DATA_DIR": str(data_dir),
                    "VIDTHEQUE_HOST": "127.0.0.1",
                    "VIDTHEQUE_PORT": str(args.mcp_port),
                    "PUBLIC_URL": mcp_url,
                    "WORKER_URL": worker_url,
                    # One video, so the politeness gap between videos buys
                    # nothing but wall clock.
                    "VIDTHEQUE_YTDLP_BETWEEN_VIDEOS_S": "0",
                    # whisperX is absent by design; fail over to captions after
                    # one 503 instead of backing off for two minutes first.
                    "VIDTHEQUE_WORKER_RETRIES": "1",
                    "VIDTHEQUE_WORKER_RETRY_MAX_WAIT_S": "10",
                    # CPU embedding of a 45 s chunk batch is slow; the default
                    # 120 s per request is not enough on a shared box.
                    "VIDTHEQUE_WORKER_TIMEOUT_S": "600",
                    "LOG_LEVEL": "info",
                }
            ),
            data_dir / "mcp.log",
        )
        services.append(mcp)
        report["mcp_boot_seconds"] = round(wait_http(f"{mcp_url}/healthz", mcp), 2)
        log(f"mcp up on {mcp_url} in {report['mcp_boot_seconds']}s")

        run_started = time.monotonic()
        report.update(await drive(args, f"{mcp_url}/mcp", data_dir))
        report["total_seconds"] = round(time.monotonic() - run_started, 1)

        if args.token_mode_check and report.get("job_state") == "done":
            services[-1].stop()  # the port is the point: same URL, other mode
            services.pop()
            video_id = args.url.rstrip("/").split("/")[-1].split("?")[0]
            report["token_mode"] = await check_token_mode(
                args, data_dir, worker_url, video_id
            )
    finally:
        for service in reversed(services):
            service.stop()

    report["db"] = db_facts(data_dir / "vidtheque.db", data_dir)
    report["worker_log_tail"] = worker.tail(80)
    report["mcp_log_tail"] = mcp.tail(80)

    section("stage timings (video_stages)")
    for row in report["db"]["stages"]:
        span = (
            f"{row['finished_at'] - row['started_at']}s"
            if row["started_at"] and row["finished_at"]
            else "-"
        )
        print(f"  {row['stage']:<12} {row['state']:<8} {span:>6}  {row['model_key'] or ''} "
              f"{('error: ' + row['error']) if row['error'] else ''}")

    section("counts and sizes")
    print(json.dumps({k: report["db"][k] for k in ("counts", "keyframes", "sizes_bytes")}, indent=2))

    report_path = data_dir / "smoke-report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str))
    log(f"report written to {report_path}")

    ok = report.get("job_state") == "done"
    if ok and not args.keep:
        log(f"data dir left at {data_dir} (db, keyframes, logs); --keep silences this")
    if not ok:
        log(f"job state was {report.get('job_state')!r} — see {data_dir}/mcp.log")
    return 0 if ok else 1


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        raise SystemExit(asyncio.run(main(sys.argv[1:])))
