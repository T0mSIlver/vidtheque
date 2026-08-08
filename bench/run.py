#!/usr/bin/env python3
"""vidtheque benchmark harness — scenario runner.

Drives a *running* worker over HTTP. It never imports ``vidtheque_worker``:
same boundary rule as the MCP service, and it means these scenarios can be
pointed at a hosted OpenAI-compatible endpoint for comparison.

Stdlib only, so ``make bench`` works in any checkout (``harness.py`` next door
is stdlib too).

    python bench/run.py --list
    python bench/run.py bench/scenarios/stt-backends.toml --dry-run
    python bench/run.py bench/scenarios/stt-backends.toml \
        --worker-url http://localhost:8081 --out bench/runs/

Every case runs with two traces underneath — device VRAM from ``nvidia-smi``
and the manager's own view from ``/status`` — because a backend that is faster
but does not fit next to a resident embedder is not faster in practice. The
lifecycle side of that (does the memory come back?) is
``bench/gpu_validation.py``; this file is the backend comparison.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (
    StatusPoller,
    VramSampler,
    audio_seconds,
    backend_state,
    post_json,
    post_multipart,
)

BENCH_DIR = Path(__file__).resolve().parent
SCENARIO_DIR = BENCH_DIR / "scenarios"
DEFAULT_WORKER_URL = "http://localhost:8081"


# --------------------------------------------------------------------------
# result envelope (stable — the reporting side codes against this)
# --------------------------------------------------------------------------


@dataclass
class CaseResult:
    case: str
    ok: bool
    elapsed_s: float | None = None
    realtime_factor: float | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class VariantResult:
    variant: str
    env: dict[str, str]
    status_before: dict[str, Any] | None = None
    status_after: dict[str, Any] | None = None
    cases: list[CaseResult] = field(default_factory=list)


@dataclass
class RunResult:
    scenario: str
    started_at: str
    worker_url: str
    variants: list[VariantResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# scenario loading
# --------------------------------------------------------------------------


def load_scenario(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        scenario = tomllib.load(handle)
    if "variants" not in scenario:
        raise SystemExit(f"{path}: scenario needs at least one [[variants]] table")
    if "cases" not in scenario:
        raise SystemExit(f"{path}: scenario needs at least one [[cases]] table")
    return scenario


def list_scenarios() -> list[Path]:
    return sorted(SCENARIO_DIR.glob("*.toml"))


# --------------------------------------------------------------------------
# worker client (stdlib)
# --------------------------------------------------------------------------


def get_status(worker_url: str, timeout: float = 5.0) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(f"{worker_url}/status", timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"  ! could not read {worker_url}/status: {exc}", file=sys.stderr)
        return None


def wait_for_variant(worker_url: str, env: dict[str, str], *, interactive: bool) -> None:
    """Backend selection is startup-time, so a variant means a worker restart."""
    print("\n  restart the worker with:")
    for key, value in env.items():
        print(f"      {key}={value}")
    if not interactive:
        return
    input("  press enter once /status reports the new backend… ")


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------


def resolve_input(value: Any, media_root: Path) -> Path | None:
    """A case's ``input``, as a path, if it is one.

    Scenarios name files relative to ``[media] root`` (which is itself relative
    to the repo). A case whose ``input`` is a sentence rather than a filename —
    ``"one short query string"`` — resolves to nothing and the case body treats
    it as literal text.
    """
    if not isinstance(value, str):
        return None
    for candidate in (Path(value), media_root / value, BENCH_DIR.parent / value):
        if candidate.exists():
            return candidate
    return None


def image_inputs(path: Path | None, limit: int | None = None) -> list[Path]:
    if path is None:
        return []
    images = (
        sorted(p for p in path.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
        if path.is_dir()
        else [path]
    )
    return images[:limit] if limit else images


def text_inputs(case: dict[str, Any], media_root: Path) -> list[str]:
    """``batch`` texts: one per line of the input file, or the literal repeated.

    Repetition is honest for a throughput number and dishonest for a quality
    one — which is why quality is not measured here (see the README).
    """
    batch = int(case.get("batch", 1))
    path = resolve_input(case.get("input"), media_root)
    if path is not None:
        lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    else:
        lines = [str(case.get("input", "benchmark query"))]
    return [lines[i % len(lines)] for i in range(batch)]


def normalise(text: str) -> list[str]:
    keep = [c.lower() if c.isalnum() or c.isspace() else " " for c in text]
    return "".join(keep).split()


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein over words, the usual definition. Stdlib, so it is here."""
    ref, hyp = normalise(reference), normalise(hypothesis)
    if not ref:
        return float("nan")
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i]
        for j, hyp_word in enumerate(hyp, start=1):
            current.append(
                previous[j - 1]
                if ref_word == hyp_word
                else 1 + min(previous[j - 1], previous[j], current[j - 1])
            )
        previous = current
    return round(previous[-1] / len(ref), 4)


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------


def run_case(worker_url: str, case: dict[str, Any], media_root: Path) -> CaseResult:
    """Run one case against the worker, with both traces running.

    Load time is taken from the ``/status`` trace rather than from the response:
    the manager loads on demand, so the first case of a variant pays for a load
    that the rest do not, and a number that silently mixes the two is the one
    way to make a backend comparison meaningless.
    """
    name = case.get("name", case.get("type", "unnamed"))
    kind = case.get("type")
    bodies = {
        "stt": case_stt,
        "embed": case_embed,
        "image_embed": case_image_embed,
        "frame_query": case_frame_query,
        "ocr": case_ocr,
        "queue": case_queue,
    }
    body = bodies.get(str(kind))
    if body is None:
        return CaseResult(
            case=name,
            ok=False,
            error=f"unknown case type {kind!r}; known: {', '.join(sorted(bodies))}",
            extra={"type": kind, "input": case.get("input")},
        )

    vram = VramSampler(interval=0.5).start()
    status = StatusPoller(worker_url, interval=0.25).start(t0=vram._t0)
    before = get_status(worker_url) or {}
    started = time.perf_counter()
    try:
        result = body(worker_url, case, media_root)
    except Exception as exc:  # a broken case must not lose the rest of the run
        result = CaseResult(case=name, ok=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        elapsed = time.perf_counter() - started
        status.stop()
        vram.stop()

    after = get_status(worker_url) or {}
    result.case = name
    if result.elapsed_s is None:
        result.elapsed_s = round(elapsed, 3)
    loads = {
        task: state["load_count"] - backend_state(before).get(task, {}).get("load_count", 0)
        for task, state in backend_state(after).items()
    }
    result.extra.setdefault("type", kind)
    result.extra["vram_peak_mb"] = vram.peak()
    result.extra["vram_end_mb"] = vram.last()
    result.extra["loads_during_case"] = {t: n for t, n in loads.items() if n}
    result.extra["max_queue_depth"] = status.max_depth()
    result.extra["max_in_flight"] = status.max_in_flight()
    result.extra["running_spans"] = status.running_spans()
    return result


def case_stt(worker_url: str, case: dict[str, Any], media_root: Path) -> CaseResult:
    path = resolve_input(case.get("input"), media_root)
    if path is None:
        return CaseResult(case="", ok=False, error=f"media not found: {case.get('input')}")

    seconds = case.get("audio_seconds") or audio_seconds(path)
    repeat = int(case.get("repeat", 1))
    fields = {"response_format": "verbose_json"}
    if case.get("language"):
        fields["language"] = str(case["language"])

    elapsed = 0.0
    response = None
    for _ in range(repeat):
        response = post_multipart(
            f"{worker_url}/v1/audio/transcriptions", files=[("file", path)], fields=fields
        )
        if response.status != 200:
            return CaseResult(
                case="", ok=False, error=f"HTTP {response.status}: {dict(response)}"
            )
        elapsed += response.elapsed_s

    segments = response.get("segments", []) if response else []
    words = [w for s in segments for w in (s.get("words") or [])]
    extra: dict[str, Any] = {
        "repeat": repeat,
        "audio_seconds": seconds,
        "segments": len(segments),
        "words": len(words),
        "words_with_timestamps": len([w for w in words if w.get("start") is not None]),
        "language": response.get("language") if response else None,
        "mean_request_s": round(elapsed / repeat, 3),
    }
    reference = resolve_input(case.get("reference"), media_root)
    if reference is not None and response is not None:
        extra["wer"] = word_error_rate(reference.read_text(), response.get("text", ""))
    return CaseResult(
        case="",
        ok=True,
        elapsed_s=round(elapsed / repeat, 3),
        realtime_factor=(round(seconds * repeat / elapsed, 2) if seconds else None),
        extra=extra,
    )


def case_embed(worker_url: str, case: dict[str, Any], media_root: Path) -> CaseResult:
    texts = text_inputs(case, media_root)
    repeat = int(case.get("repeat", 1))
    input_type = str(case.get("input_type", "document"))

    elapsed = 0.0
    response = None
    for _ in range(repeat):
        response = post_json(
            f"{worker_url}/v1/embeddings", {"input": texts, "input_type": input_type}
        )
        if response.status != 200:
            return CaseResult(
                case="", ok=False, error=f"HTTP {response.status}: {dict(response)}"
            )
        elapsed += response.elapsed_s

    vectors = response.get("data", []) if response else []
    return CaseResult(
        case="",
        ok=True,
        elapsed_s=round(elapsed / repeat, 4),
        extra={
            "batch": len(texts),
            "repeat": repeat,
            "input_type": input_type,
            "mean_chars": round(sum(len(t) for t in texts) / max(len(texts), 1), 1),
            "dimensions": response.get("dimensions") if response else None,
            "vectors": len(vectors),
            "texts_per_s": round(len(texts) * repeat / elapsed, 1),
        },
    )


def case_image_embed(worker_url: str, case: dict[str, Any], media_root: Path) -> CaseResult:
    images = image_inputs(resolve_input(case.get("input"), media_root), case.get("limit"))
    if not images:
        return CaseResult(case="", ok=False, error=f"no images at {case.get('input')}")
    repeat = int(case.get("repeat", 1))
    fields = {}
    if case.get("max_num_patches"):
        fields["max_num_patches"] = int(case["max_num_patches"])

    elapsed = 0.0
    response = None
    for _ in range(repeat):
        response = post_multipart(
            f"{worker_url}/v1/embeddings/image",
            files=[("file", path) for path in images],
            fields=fields,
        )
        if response.status != 200:
            return CaseResult(
                case="", ok=False, error=f"HTTP {response.status}: {dict(response)}"
            )
        elapsed += response.elapsed_s

    return CaseResult(
        case="",
        ok=True,
        elapsed_s=round(elapsed / repeat, 3),
        extra={
            "frames": len(images),
            "repeat": repeat,
            "max_num_patches": fields.get("max_num_patches"),
            "dimensions": response.get("dimensions") if response else None,
            "frames_per_s": round(len(images) * repeat / elapsed, 2),
        },
    )


def case_frame_query(worker_url: str, case: dict[str, Any], media_root: Path) -> CaseResult:
    texts = text_inputs(case, media_root)
    repeat = int(case.get("repeat", 1))
    elapsed = 0.0
    response = None
    for _ in range(repeat):
        response = post_json(f"{worker_url}/v1/embeddings/frame-query", {"input": texts})
        if response.status != 200:
            return CaseResult(
                case="", ok=False, error=f"HTTP {response.status}: {dict(response)}"
            )
        elapsed += response.elapsed_s
    return CaseResult(
        case="",
        ok=True,
        elapsed_s=round(elapsed / repeat, 4),
        extra={
            "queries": len(texts),
            "repeat": repeat,
            "dimensions": response.get("dimensions") if response else None,
            "queries_per_s": round(len(texts) * repeat / elapsed, 1),
        },
    )


def case_ocr(worker_url: str, case: dict[str, Any], media_root: Path) -> CaseResult:
    images = image_inputs(resolve_input(case.get("input"), media_root), case.get("limit"))
    if not images:
        return CaseResult(case="", ok=False, error=f"no images at {case.get('input')}")
    batch = int(case.get("batch", len(images)))
    fields = {}
    if case.get("min_confidence") is not None:
        fields["min_confidence"] = float(case["min_confidence"])

    elapsed = 0.0
    lines = 0
    confidences: list[float] = []
    for start in range(0, len(images), batch):
        response = post_multipart(
            f"{worker_url}/v1/ocr",
            files=[("file", path) for path in images[start : start + batch]],
            fields=fields,
        )
        if response.status != 200:
            return CaseResult(
                case="", ok=False, error=f"HTTP {response.status}: {dict(response)}"
            )
        elapsed += response.elapsed_s
        for image in response.get("data", []):
            for item in image.get("items", []):
                lines += 1
                if item.get("confidence") is not None:
                    confidences.append(float(item["confidence"]))

    return CaseResult(
        case="",
        ok=True,
        elapsed_s=round(elapsed, 3),
        extra={
            "frames": len(images),
            "batch": batch,
            "lines": lines,
            "frames_per_s": round(len(images) / elapsed, 2),
            "mean_confidence": (
                round(sum(confidences) / len(confidences), 4) if confidences else None
            ),
        },
    )


def case_queue(worker_url: str, case: dict[str, Any], media_root: Path) -> CaseResult:
    """Throughput under queueing, which is the number the manager decides.

    ``concurrency`` requests go out at once; the lifecycle manager runs them one
    at a time. What comes back is jobs/minute end to end plus the evidence that
    they really were serialised — no two tasks ever reported ``running``
    together.
    """
    concurrency = int(case.get("concurrency", 2))
    path = resolve_input(case.get("input"), media_root)
    if path is None:
        return CaseResult(case="", ok=False, error=f"media not found: {case.get('input')}")
    mix = case.get("mix") or (["stt"] * concurrency)
    texts = text_inputs({"input": case.get("texts", "queue depth probe"), "batch": 32}, media_root)

    outcomes: list[dict[str, Any]] = []
    lock = threading.Lock()

    def fire(index: int, kind: str) -> None:
        started = time.perf_counter()
        if kind == "embed":
            response = post_json(f"{worker_url}/v1/embeddings", {"input": texts})
        else:
            response = post_multipart(
                f"{worker_url}/v1/audio/transcriptions",
                files=[("file", path)],
                fields={"response_format": "json"},
            )
        with lock:
            outcomes.append(
                {
                    "index": index,
                    "kind": kind,
                    "status": response.status,
                    "started_at": round(started, 3),
                    "elapsed_s": round(response.elapsed_s, 3),
                }
            )

    threads = [
        threading.Thread(target=fire, args=(i, mix[i % len(mix)])) for i in range(concurrency)
    ]
    wall_start = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall = time.perf_counter() - wall_start

    ok = all(o["status"] == 200 for o in outcomes)
    seconds = case.get("audio_seconds") or audio_seconds(path)
    return CaseResult(
        case="",
        ok=ok,
        elapsed_s=round(wall, 3),
        realtime_factor=(
            round(seconds * sum(1 for o in outcomes if o["kind"] != "embed") / wall, 2)
            if seconds
            else None
        ),
        error=None if ok else f"{sum(1 for o in outcomes if o['status'] != 200)} request(s) failed",
        extra={
            "concurrency": concurrency,
            "mix": mix,
            "jobs_per_minute": round(60.0 * len(outcomes) / wall, 2),
            "sum_of_request_s": round(sum(o["elapsed_s"] for o in outcomes), 2),
            "wall_clock_s": round(wall, 2),
            "requests": sorted(outcomes, key=lambda o: o["index"]),
        },
    )


def media_root(scenario: dict[str, Any]) -> Path:
    """``[media] root``, resolved against the repo rather than the cwd."""
    root = str((scenario.get("media") or {}).get("root", "bench/media"))
    candidate = Path(root)
    return candidate if candidate.is_absolute() else BENCH_DIR.parent / candidate


def run_variant(
    worker_url: str,
    variant: dict[str, Any],
    cases: list[dict[str, Any]],
    *,
    interactive: bool,
    root: Path,
) -> VariantResult:
    env = {str(k): str(v) for k, v in (variant.get("env") or {}).items()}
    name = variant.get("name", ",".join(f"{k}={v}" for k, v in env.items()))
    print(f"\n=== variant: {name}")
    wait_for_variant(worker_url, env, interactive=interactive)

    result = VariantResult(variant=name, env=env, status_before=get_status(worker_url))
    for case in cases:
        started = time.perf_counter()
        case_result = run_case(worker_url, case, root)
        if case_result.elapsed_s is None and case_result.ok:
            case_result.elapsed_s = time.perf_counter() - started
        flag = "ok" if case_result.ok else "SKIP"
        print(f"  [{flag}] {case_result.case}: {case_result.error or case_result.elapsed_s}")
        result.cases.append(case_result)
    result.status_after = get_status(worker_url)
    return result


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenario", nargs="?", help="path to a scenario .toml")
    parser.add_argument("--list", action="store_true", help="list bundled scenarios")
    parser.add_argument("--worker-url", default=DEFAULT_WORKER_URL)
    parser.add_argument("--out", type=Path, help="directory for the result JSON")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan (variants, envs, cases) and exit",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="do not pause between variants (assumes something else restarts the worker)",
    )
    args = parser.parse_args(argv)

    if args.list or not args.scenario:
        print("scenarios:")
        for path in list_scenarios():
            scenario = load_scenario(path)
            print(f"  {path.relative_to(BENCH_DIR.parent)} — {scenario.get('description', '')}")
        return 0

    path = Path(args.scenario)
    scenario = load_scenario(path)
    variants = scenario["variants"]
    cases = scenario["cases"]

    print(f"scenario: {scenario.get('name', path.stem)}")
    print(f"  {scenario.get('description', '')}")
    print(f"  {len(variants)} variant(s) x {len(cases)} case(s) against {args.worker_url}")

    if args.dry_run:
        for variant in variants:
            env = variant.get("env", {})
            print(f"\n  variant {variant.get('name', '?')}")
            for key, value in env.items():
                print(f"    {key}={value}")
            for case in cases:
                print(f"    case {case.get('name')} ({case.get('type')}) <- {case.get('input')}")
        return 0

    root = media_root(scenario)
    run = RunResult(
        scenario=scenario.get("name", path.stem),
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        worker_url=args.worker_url,
        notes=[
            f"media root: {root}",
            (
                "the first case of a variant pays for the model load; "
                "see extra.loads_during_case"
            ),
        ],
    )
    for variant in variants:
        run.variants.append(
            run_variant(
                args.worker_url,
                variant,
                cases,
                interactive=not args.non_interactive,
                root=root,
            )
        )

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = args.out / f"{run.scenario}-{stamp}.json"
        destination.write_text(json.dumps(asdict(run), indent=2) + "\n")
        print(f"\nwrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
