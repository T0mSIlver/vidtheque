#!/usr/bin/env python3
"""vidtheque benchmark harness — scenario runner (skeleton).

Drives a *running* worker over HTTP. It never imports ``vidtheque_worker``:
same boundary rule as the MCP service, and it means these scenarios can be
pointed at a hosted OpenAI-compatible endpoint for comparison.

Stdlib only, so ``make bench`` works in any checkout.

    python bench/run.py --list
    python bench/run.py bench/scenarios/stt-backends.toml --dry-run
    python bench/run.py bench/scenarios/stt-backends.toml \
        --worker-url http://localhost:8081 --out bench/runs/
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
# measurement (stub)
# --------------------------------------------------------------------------


def run_case(worker_url: str, case: dict[str, Any]) -> CaseResult:
    """Run one case against the worker.

    TODO(bench): the measurement bodies land with the first real comparison.
    Planned shape, per ``case.type``:

    * ``stt``   — POST the media file to /v1/audio/transcriptions with
      ``response_format=verbose_json``; record elapsed, realtime factor
      (``case.audio_seconds / elapsed``), segment and word counts; when
      ``case.reference`` is set, compute WER against it.
    * ``embed`` — POST N texts to /v1/embeddings at several batch sizes;
      record texts/second and the reported ``dimensions``.
    * ``ocr``   — POST a directory of keyframes to /v1/ocr; record
      images/second and the mean confidence.
    * ``queue`` — fire ``case.concurrency`` requests at once and record
      end-to-end throughput, which is the figure the serialising lifecycle
      manager actually determines.

    Peak VRAM and load time come from polling /status around the case rather
    than from the response.
    """
    name = case.get("name", case.get("type", "unnamed"))
    return CaseResult(
        case=name,
        ok=False,
        error="not implemented: bench measurement bodies are TODO(bench)",
        extra={"type": case.get("type"), "input": case.get("input")},
    )


def run_variant(
    worker_url: str, variant: dict[str, Any], cases: list[dict[str, Any]], *, interactive: bool
) -> VariantResult:
    env = {str(k): str(v) for k, v in (variant.get("env") or {}).items()}
    name = variant.get("name", ",".join(f"{k}={v}" for k, v in env.items()))
    print(f"\n=== variant: {name}")
    wait_for_variant(worker_url, env, interactive=interactive)

    result = VariantResult(variant=name, env=env, status_before=get_status(worker_url))
    for case in cases:
        started = time.perf_counter()
        case_result = run_case(worker_url, case)
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

    run = RunResult(
        scenario=scenario.get("name", path.stem),
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        worker_url=args.worker_url,
        notes=["measurement bodies are TODO(bench); this run records the plan only"],
    )
    for variant in variants:
        run.variants.append(
            run_variant(
                args.worker_url, variant, cases, interactive=not args.non_interactive
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
