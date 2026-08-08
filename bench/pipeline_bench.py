#!/usr/bin/env python3
"""The whole pipeline, once per configuration, on the same video.

`gpu_validation.py` asks whether the memory comes back. `run.py` asks which
backend is faster at one endpoint. This asks the operator's question: **what
does the GPU buy you end to end** — `index-video` to `job-status: done`, every
stage, including the ones the GPU cannot help with.

    uv run --no-sync python bench/pipeline_bench.py --list
    uv run --no-sync python bench/pipeline_bench.py cpu-autocaps gpu-autocaps gpu-whisperx \
        --out bench/results/raw/pipeline-bench.json

One worker process and one mcp process per configuration, each with its own
fresh data dir, so no run inherits another's database, media or model residency.
Nothing is shared between configurations except the HF cache and YouTube.

**Not part of `make test` and never will be**: it downloads model weights and
media, and needs a GPU for two of the three configurations.

Three clocks, deliberately:

* `video_stages.started_at/finished_at` — the pipeline's own per-stage wall
  clock, 1 s resolution (`unixepoch()`), and the number an operator can read
  back out of any database. This is the table's spine.
* the worker's `/status` queue, polled at 250 ms — the *inference* span inside
  a stage, without the fetch, the decode or the SQLite writes around it. On GPU
  the difference between the two is most of the stage.
* whole-device VRAM from `nvidia-smi`, 500 ms — for the peak, and to show what
  a co-tenant would have had to give up.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    REPO,
    StatusPoller,
    VramSampler,
    Worker,
    get_json,
    post_json,
    vram_used_mb,
)

DEFAULT_URL = "https://youtu.be/5C_HPTJg5ek"  # Fireship, "Rust in 100 Seconds", 2:29
STAGES = ("fetch", "stt", "chunk", "text_embed", "keyframe", "ocr", "frame_embed")


# --------------------------------------------------------------- configurations


@dataclass(frozen=True)
class Config:
    """One end-to-end configuration: a worker environment and a pipeline policy.

    Everything that is *not* the variable under test is held equal here rather
    than left to defaults, because two of these three numbers are only
    comparable if the keyframe detector, the patch budget, the OCR thread count
    and the idle TTL are the same in all of them.
    """

    name: str
    what: str
    worker_env: dict[str, str]
    mcp_env: dict[str, str] = field(default_factory=dict)
    needs_gpu: bool = False


# Held equal across every configuration.
COMMON_WORKER = {
    # Not a preference — a workaround, and the run is invalid without it.
    # `HTTPWorkerClient` overrides transcribe/ocr/embed_images/embed_frame_query
    # but *not* `embed`, so the indexing text_embed call inherits
    # `HTTPEmbeddingClient.embed`: a flat 20 s budget with no retry, no
    # Retry-After handling and no `VIDTHEQUE_WORKER_TIMEOUT_S`. A cold
    # Qwen3-Embedding load is 14.6 s on CPU here, so the first batch times out,
    # the job retries the whole item three times and fails. Loading the model
    # before the job and keeping it resident takes the cold load out of that
    # 20 s window. Measured and written up in
    # research/pipeline-bench-2026-08-09.md.
    "VIDTHEQUE_EMBED_RESIDENT": "1",
    # The smoke's value, kept for both devices: sequential stages mean a model
    # can go before the next one loads. On CPU that is the difference between
    # running and swapping; on GPU it costs nothing inside one job, because no
    # stage reuses a model a later stage already unloaded.
    "VIDTHEQUE_IDLE_UNLOAD_SECONDS": "20",
    "VIDTHEQUE_OCR_THREADS": "8",
    # gpu_validation measured SigLIP 2 at 256 patches; keep the same budget so
    # the frame_embed rows compare.
    "VIDTHEQUE_IMAGE_EMBED_MAX_PATCHES": "256",
    "LOG_LEVEL": "info",
}

COMMON_MCP = {
    "VIDTHEQUE_AUTH": "none",
    # One video: the politeness gap between videos buys nothing but wall clock.
    "VIDTHEQUE_YTDLP_BETWEEN_VIDEOS_S": "0",
    # CPU embedding of a chunk batch can exceed the 120 s default.
    "VIDTHEQUE_WORKER_TIMEOUT_S": "600",
    "VIDTHEQUE_WORKER_RETRIES": "1",
    "VIDTHEQUE_WORKER_RETRY_MAX_WAIT_S": "10",
    "LOG_LEVEL": "info",
}

CONFIGS: tuple[Config, ...] = (
    Config(
        name="cpu-autocaps",
        what="DEVICE=cpu, YouTube auto-captions (no STT model at all)",
        worker_env={"DEVICE": "cpu", "COMPUTE_TYPE": "int8"},
        # `captions_only` rather than the smoke's `prefer_whisperx`-with-no-
        # whisperX: the fallback path is already proven (e2e-smoke §1) and its
        # 503 + backoff would put ~10 s of retry wait inside the `stt` row,
        # which is not a CPU cost and would not appear in the GPU rows.
        mcp_env={"VIDTHEQUE_STT_POLICY": "captions_only"},
    ),
    Config(
        name="gpu-autocaps",
        what="DEVICE=cuda, YouTube auto-captions — the GPU only touches the embedders",
        worker_env={"DEVICE": "cuda", "COMPUTE_TYPE": "float16"},
        mcp_env={"VIDTHEQUE_STT_POLICY": "captions_only"},
        needs_gpu=True,
    ),
    Config(
        name="gpu-whisperx",
        what="DEVICE=cuda, whisperX large-v3 transcribing for real",
        worker_env={"DEVICE": "cuda", "COMPUTE_TYPE": "float16"},
        # `whisperx_only`, not `prefer_whisperx`: a fallback to captions here
        # would look like a fast run rather than a failed one.
        mcp_env={"VIDTHEQUE_STT_POLICY": "whisperx_only"},
        needs_gpu=True,
    ),
)

BY_NAME = {c.name: c for c in CONFIGS}


# ------------------------------------------------------------------------- io


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


_DROP_PREFIXES = ("VIDTHEQUE_", "WORKER_", "STT_", "EMBED_", "IMAGE_EMBED_", "OCR_")
_DROP_NAMES = {"PUBLIC_URL", "DEVICE", "COMPUTE_TYPE"}


def refuse_dirty_shell() -> None:
    """Fail before measuring anything if the operator's shell configures either half.

    `harness.Worker` merges the environment it is given *over* `os.environ`, so
    a stripped copy cannot un-set an inherited `DEVICE=cuda` — it would come
    back from the parent and quietly decide the run. Refusing is the only
    honest option: a bench that silently measured the wrong device is worse
    than one that did not run.
    """
    dirty = sorted(
        k
        for k in os.environ
        if k.startswith(_DROP_PREFIXES) or k in _DROP_NAMES
    )
    if dirty:
        raise SystemExit(
            "these variables would reconfigure the run; unset them first: "
            + ", ".join(dirty)
        )


def clean_env(extra: dict[str, str]) -> dict[str, str]:
    """The caller's environment with every vidtheque setting stripped."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(_DROP_PREFIXES) and k not in _DROP_NAMES
    }
    env.update(extra)
    return env


# ------------------------------------------------------------------ mcp process


@dataclass
class McpServer:
    proc: subprocess.Popen
    log_path: Path
    url: str

    def tail(self, n: int = 60) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-n:])
        except OSError:
            return ""

    def stop(self) -> None:
        if self.proc.poll() is not None:
            return
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover
            self.proc.kill()


def start_mcp(env: dict[str, str], log_path: Path, url: str, timeout: float = 120.0) -> McpServer:
    if get_json(f"{url}/healthz", timeout=2.0).status == 200:
        raise RuntimeError(f"something is already serving {url} — stop it before benching")
    handle = log_path.open("w")
    proc = subprocess.Popen(
        ["uv", "run", "--no-sync", "python", "-m", "vidtheque_mcp"],
        cwd=str(REPO),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    server = McpServer(proc=proc, log_path=log_path, url=url)
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"mcp exited {proc.returncode}:\n{server.tail()}")
        if get_json(f"{url}/healthz", timeout=2.0).status == 200:
            return server
        time.sleep(0.25)
    server.stop()
    raise TimeoutError(f"mcp did not come up:\n{server.tail()}")


# --------------------------------------------------------------------- driving


async def index_and_wait(
    mcp_url: str, video_url: str, poll_s: float, timeout: float
) -> dict[str, Any]:
    """`index-video`, then `job-status` until it settles. Returns the wire facts."""
    from mcp.client import Client

    out: dict[str, Any] = {}
    async with Client(f"{mcp_url}/mcp", read_timeout_seconds=900.0) as client:
        started = time.perf_counter()
        queued = await client.call_tool(
            "index-video",
            {"url": video_url, "expand": "none", "tags": "topic:bench", "channels": "all"},
        )
        job_id = (queued.structured_content or {}).get("job_id")
        if not job_id:
            texts = [c.text for c in queued.content if getattr(c, "type", "") == "text"]
            raise RuntimeError(f"no job created: {' '.join(texts)}")
        out["job_id"] = job_id
        seen: set[str] = set()
        state = "?"
        while time.perf_counter() - started < timeout:
            status = await client.call_tool("job-status", {"job_id": job_id})
            structured = status.structured_content or {}
            state = str(structured.get("state", "?"))
            stamp = f"{state} {float(structured.get('progress', 0.0)):.0%}"
            if stamp not in seen:
                seen.add(stamp)
                log(f"    job {job_id}: {stamp}")
            if state in {"done", "failed", "cancelled"}:
                break
            await asyncio.sleep(poll_s)
        out["job_state"] = state
        out["job_seconds"] = round(time.perf_counter() - started, 1)
        out["job_status_text"] = "\n".join(
            c.text for c in status.content if getattr(c, "type", "") == "text"
        )
    return out


# -------------------------------------------------------------------- the data


def db_facts(data_dir: Path) -> dict[str, Any]:
    """Everything the run leaves behind that is worth a table row."""
    db_path = data_dir / "vidtheque.db"
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out: dict[str, Any] = {}
    try:
        out["stages"] = [
            dict(row)
            for row in conn.execute(
                "SELECT stage, state, model_key, started_at, finished_at, error "
                "FROM video_stages"
            )
        ]
        out["stage_seconds"] = {
            row["stage"]: (
                row["finished_at"] - row["started_at"]
                if row["started_at"] and row["finished_at"]
                else None
            )
            for row in out["stages"]
        }
        out["events"] = [
            dict(row)
            for row in conn.execute(
                "SELECT at, level, stage, message FROM job_events ORDER BY id"
            )
        ]
        out["video"] = dict(
            conn.execute(
                "SELECT public_id, title, duration_s, index_state, language "
                "FROM videos LIMIT 1"
            ).fetchone()
            or {}
        )
        out["counts"] = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("videos", "chapters", "cues", "chunks", "keyframes", "ocr_lines")
        }
        out["keyframes"] = dict(
            conn.execute(
                "SELECT count(*) AS n, coalesce(sum(jpeg_bytes), 0) AS bytes, "
                "sum(dup_of IS NOT NULL) AS dups, sum(ocr_state = 'done') AS ocr_done "
                "FROM keyframes"
            ).fetchone()
        )
        # The transcript itself, so two configurations can be diffed cue by cue.
        # `words_json` is the word-timestamp question: captions carry per-word
        # times from `json3`, whisperX carries them from forced alignment, and
        # `STT_ALIGN=0` would carry none.
        out["cues"] = [
            {
                "seq": row["seq"],
                "start_s": row["start_s"],
                "end_s": row["end_s"],
                "text": row["text"],
                "origin": row["origin"],
                "n_words": len(json.loads(row["words_json"])) if row["words_json"] else 0,
            }
            for row in conn.execute(
                "SELECT seq, start_s, end_s, text, origin, words_json FROM cues ORDER BY seq"
            )
        ]
    finally:
        conn.close()
    out["sizes_bytes"] = {
        "vidtheque.db": db_path.stat().st_size if db_path.exists() else 0,
        "keyframes_dir": _tree(data_dir / "keyframes"),
        "audio_dir": _tree(data_dir / "audio"),
        "media_dir": _tree(data_dir / "media"),
        "data_dir": _tree(data_dir),
    }
    return out


def _tree(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


# ----------------------------------------------------------------- one config


def run_config(config: Config, args: argparse.Namespace) -> dict[str, Any]:
    data_dir = Path(args.data_root) / config.name
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)

    worker_url = f"http://127.0.0.1:{args.worker_port}"
    mcp_url = f"http://127.0.0.1:{args.mcp_port}"
    result: dict[str, Any] = {
        "config": config.name,
        "what": config.what,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "worker_env": {**COMMON_WORKER, **config.worker_env},
        "mcp_env": {**COMMON_MCP, **config.mcp_env},
        "data_dir": str(data_dir),
    }
    # The number every GPU figure below is only true relative to: what the card
    # was doing before this configuration started.
    result["vram_baseline_mb"] = vram_used_mb(args.gpu_index)
    # The other half of the baseline. Four of the seven stages are CPU-only in
    # every configuration, so a busy box moves them and a reader needs to know
    # whether two runs were comparably loaded.
    result["loadavg_before"] = [round(x, 2) for x in os.getloadavg()]
    log(f"[{config.name}] {config.what}")
    log(
        f"[{config.name}] VRAM baseline {result['vram_baseline_mb']} MB, "
        f"loadavg {result['loadavg_before']}"
    )

    worker = Worker(
        env=clean_env(
            {
                **COMMON_WORKER,
                **config.worker_env,
                "VIDTHEQUE_HOST": "127.0.0.1",
                "VIDTHEQUE_PORT": str(args.worker_port),
            }
        ),
        log_path=data_dir / "worker.log",
        url=worker_url,
    )
    vram = VramSampler(interval=0.5, index=args.gpu_index)
    poller = StatusPoller(url=worker_url, interval=0.25)
    mcp: McpServer | None = None
    try:
        t0 = time.perf_counter()
        worker.start()
        result["worker_boot_seconds"] = round(time.perf_counter() - t0, 2)
        result["worker_status_at_boot"] = dict(worker.status())
        vram.start()
        poller.start(t0=vram._t0)

        # Load the text embedder before the job, for the reason COMMON_WORKER
        # gives. Timed rather than hidden: this *is* the cold-load number that
        # does not fit in the pipeline's text_embed budget, and every
        # `text_embed` row below is a warm-model number because of it.
        vram.mark("prewarm embed")
        warm = post_json(
            f"{worker_url}/v1/embeddings",
            {"input": ["warm the text embedder"], "input_type": "document"},
            timeout=600.0,
        )
        result["prewarm_embed"] = {
            "status": warm.status,
            "seconds": round(warm.elapsed_s, 2),
            "model": warm.get("model"),
        }
        log(f"[{config.name}] text embedder warm in {result['prewarm_embed']['seconds']}s")

        t1 = time.perf_counter()
        mcp = start_mcp(
            clean_env(
                {
                    **COMMON_MCP,
                    **config.mcp_env,
                    "VIDTHEQUE_DATA_DIR": str(data_dir),
                    "VIDTHEQUE_HOST": "127.0.0.1",
                    "VIDTHEQUE_PORT": str(args.mcp_port),
                    "PUBLIC_URL": mcp_url,
                    "WORKER_URL": worker_url,
                }
            ),
            data_dir / "mcp.log",
            mcp_url,
        )
        result["mcp_boot_seconds"] = round(time.perf_counter() - t1, 2)

        vram.mark("index-video")
        result.update(
            asyncio.run(
                index_and_wait(mcp_url, args.url, args.poll_seconds, args.job_timeout)
            )
        )
        vram.mark("job settled")
        log(f"[{config.name}] job {result['job_state']} in {result['job_seconds']}s")
    finally:
        if mcp is not None:
            mcp.stop()
        poller.stop()
        vram.stop()
        worker.stop()

    result["loadavg_after"] = [round(x, 2) for x in os.getloadavg()]
    result["db"] = db_facts(data_dir)
    result["worker_loads"] = worker.load_times()
    result["worker_unloads"] = worker.unloads()
    result["vram"] = {
        "baseline_mb": result["vram_baseline_mb"],
        "peak_mb": vram.peak(),
        "final_mb": vram.last(),
        "trace": vram.as_dict(),
    }
    # `running` spans are the worker's own view of when a model was computing —
    # the inference inside a stage, with the pipeline's IO stripped off.
    result["worker_spans"] = [
        {**span, "seconds": round(span["end"] - span["start"], 2)}
        for span in poller.running_spans()
    ]
    result["inference_seconds"] = _inference_by_task(result["worker_spans"])
    result["status_trace"] = poller.as_dict()
    return result


def _inference_by_task(spans: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for span in spans:
        out[span["task"]] = round(out.get(span["task"], 0.0) + span["seconds"], 2)
    return out


# ------------------------------------------------------------------ reporting


def table(results: list[dict[str, Any]]) -> str:
    names = [r["config"] for r in results]
    lines = ["| stage | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for stage in STAGES:
        cells = []
        for r in results:
            secs = r["db"]["stage_seconds"].get(stage)
            state = next(
                (s["state"] for s in r["db"]["stages"] if s["stage"] == stage), "-"
            )
            cells.append("-" if secs is None else f"{secs}s" + ("" if state == "done" else f" ({state})"))
        lines.append(f"| {stage} | " + " | ".join(cells) + " |")
    lines.append(
        "| **job total** | "
        + " | ".join(f"**{r['job_seconds']}s**" for r in results)
        + " |"
    )
    for r in results:
        duration = (r["db"]["video"] or {}).get("duration_s") or 0
        r["realtime_factor"] = (
            round(duration / r["job_seconds"], 2) if r["job_seconds"] else None
        )
    lines.append(
        "| realtime factor | "
        + " | ".join(f"{r['realtime_factor']}x" for r in results)
        + " |"
    )
    lines.append(
        "| loadavg 1m (before) | "
        + " | ".join(str(r["loadavg_before"][0]) for r in results)
        + " |"
    )
    lines.append(
        "| peak VRAM | "
        + " | ".join(
            "-" if r["vram"]["peak_mb"] is None else f"{r['vram']['peak_mb']} MB"
            for r in results
        )
        + " |"
    )
    return "\n".join(lines)


# ----------------------------------------------------------------------- main


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("configs", nargs="*", default=[], help="configuration names, in order")
    p.add_argument("--list", action="store_true", help="print the configurations and exit")
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument(
        "--data-root",
        default=str(Path.home() / ".cache" / "vidtheque-pipeline-bench"),
        help="parent for the per-configuration data dirs (one per config, wiped first)",
    )
    p.add_argument("--out", default=None, help="write the result envelope here (JSON)")
    p.add_argument("--worker-port", type=int, default=8341)
    p.add_argument("--mcp-port", type=int, default=8340)
    p.add_argument("--gpu-index", type=int, default=0)
    p.add_argument("--poll-seconds", type=float, default=3.0)
    p.add_argument("--job-timeout", type=float, default=3600.0)
    p.add_argument(
        "--settle-seconds",
        type=float,
        default=10.0,
        help="pause between configurations, so one run's unloads are not the next one's baseline",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.list or not args.configs:
        for config in CONFIGS:
            print(f"{config.name:<16} {config.what}")
        return 0
    unknown = [name for name in args.configs if name not in BY_NAME]
    if unknown:
        print(f"unknown configuration(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    refuse_dirty_shell()

    envelope: dict[str, Any] = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "url": args.url,
        "host": {
            "nvidia_smi": subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                 "--format=csv,noheader"],
                capture_output=True, text=True, check=False,
            ).stdout.strip(),
            "cpu_count": os.cpu_count(),
        },
        "results": [],
    }
    for index, name in enumerate(args.configs):
        if index:
            time.sleep(args.settle_seconds)
        envelope["results"].append(run_config(BY_NAME[name], args))
        if args.out:  # write as we go: a later configuration can fail
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(envelope, indent=2, default=str))

    print()
    print(table(envelope["results"]))
    if args.out:
        Path(args.out).write_text(json.dumps(envelope, indent=2, default=str))
        log(f"envelope written to {args.out}")
    return 0 if all(r["job_state"] == "done" for r in envelope["results"]) else 1


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        raise SystemExit(main(sys.argv[1:]))
