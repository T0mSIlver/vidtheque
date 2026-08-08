#!/usr/bin/env python3
"""GPU validation driver — the lifecycle manager's claims, on real hardware.

``bench/run.py`` compares backends. This compares the worker's *promises* to a
card's actual VRAM: every check here ends by asking whether the memory came
back, because the entire argument for on-demand loading is that a co-tenant
gets the card back afterwards.

Every check is one worker process with one environment (backend selection and
every lifecycle knob are startup-time), driven through the real HTTP endpoints,
with two traces running underneath: ``nvidia-smi`` for device VRAM at 500 ms and
``/status`` for what the manager thinks it is doing at 250 ms. They share a
clock, so a VRAM step can be attributed to a load, an unload or an eviction
rather than guessed at.

    uv run --no-sync python bench/gpu_validation.py --list
    uv run --no-sync python bench/gpu_validation.py stt embed \
        --audio /path/talk.opus --frames /path/keyframes --out bench/results/raw

Results are one JSON file per check (``<check><tag>.json``) so a long matrix
survives a failure in the middle of it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (
    REPO,
    StatusPoller,
    VramSampler,
    Worker,
    audio_seconds,
    backend_state,
    post_json,
    post_multipart,
    vram_used_mb,
)

WORKER_URL = "http://127.0.0.1:8081"
SETTLE_TOLERANCE_MB = 200
"""The core assertion's slack: after an unload, device VRAM must be back within
this of where it was before the load."""


# --------------------------------------------------------------------------
# one check = one worker under one environment, with both traces running
# --------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    env: dict[str, str]
    out_dir: Path
    tag: str = ""

    worker: Worker | None = None
    vram: VramSampler | None = None
    status: StatusPoller | None = None
    result: dict[str, Any] = field(default_factory=dict)

    def __enter__(self) -> Check:
        self.result = {
            "check": self.name,
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "env": dict(self.env),
            "metrics": {},
            "events": [],
            "findings": [],
        }
        self.vram = VramSampler(interval=0.5).start()
        self.result["baseline_device_mb"] = _quiet_device_vram()
        self.mark("worker-start")
        self.worker = Worker(
            env={"DEVICE": "cuda", "LOG_LEVEL": "info", **self.env},
            log_path=self.out_dir / f"{self.name}{self.tag}-worker.log",
            url=WORKER_URL,
        ).start()
        self.status = StatusPoller(WORKER_URL, interval=0.25).start(t0=self.vram._t0)
        time.sleep(1.5)  # let the trace show the pre-load floor
        self.result["baseline_worker_mb"] = vram_used_mb()
        # What the manager's own probe thinks, at the same instant. It is the
        # number admission control gates on, so the gap between the two is a
        # fact about the gate, not a measurement error.
        boot = dict(self.worker.status())
        nvml_used = boot.get("vram", {}).get("used_mb")
        self.result["nvml_used_at_baseline_mb"] = nvml_used
        self.result["nvml_offset_mb"] = (
            None
            if nvml_used is None or self.result["baseline_worker_mb"] is None
            else nvml_used - self.result["baseline_worker_mb"]
        )
        self.mark("baseline")
        return self

    def __exit__(self, *exc: object) -> None:
        assert self.worker and self.vram and self.status
        self.result["status_final"] = dict(self.worker.status())
        self.result["worker_load_log"] = self.worker.load_times()
        self.result["worker_unload_log"] = self.worker.unloads()
        self.mark("worker-stop")
        self.status.stop()
        self.worker.stop()
        time.sleep(1.5)
        self.result["after_process_exit_mb"] = vram_used_mb()
        self.mark("after-exit")
        self.vram.stop()
        self.result["vram_trace"] = self.vram.as_dict()
        self.result["status_trace"] = self.status.as_dict()
        self.result["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        destination = self.out_dir / f"{self.name}{self.tag}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.result, indent=2) + "\n")
        print(f"  -> {destination}")

    # -- trace helpers -----------------------------------------------------
    def mark(self, label: str) -> float:
        assert self.vram
        stamp = self.vram.mark(label)
        self.result.setdefault("events", []).append({"t": stamp, "label": label})
        return stamp

    def peak(self, start: float, end: float | None = None) -> int | None:
        """Highest device VRAM in a window, in ``nvidia-smi`` terms.

        The two traces do **not** agree, and mixing them silently inflates every
        number: NVML's ``nvmlDeviceGetMemoryInfo`` counts the driver's own
        reserve in ``used`` and ``nvidia-smi memory.used`` does not, so the
        worker's ``/status`` reads ~320 MB higher than the device on an idle
        card (:meth:`nvml_offset`). ``nvidia-smi`` is the authority here because
        it is what a human looks at when asking whether llama.cpp fits; the
        NVML trace is only a fallback for a window too short to have been
        sampled at 500 ms.
        """
        assert self.vram and self.status
        sampled = self.vram.peak(start, end)
        if sampled is not None:
            return sampled
        finish = self.vram.now() if end is None else end
        from_status = [
            s["vram_used_mb"] - (self.result.get("nvml_offset_mb") or 0)
            for s in self.status.samples
            if start <= s["t"] <= finish and s["vram_used_mb"] is not None
        ]
        return max(from_status) if from_status else None

    def steady(self, label: str = "steady") -> int | None:
        """Device VRAM with the model loaded and nothing running: what a
        co-tenant actually sees while the worker holds the card."""
        time.sleep(2.0)
        self.mark(label)
        return vram_used_mb()

    def finding(self, text: str) -> None:
        print(f"  ! {text}")
        self.result["findings"].append(text)

    def metric(self, **values: Any) -> None:
        self.result["metrics"].update(values)

    def settle(
        self,
        *,
        timeout: float = 180.0,
        target_mb: int | None = None,
        key: str = "settle",
    ) -> dict[str, Any]:
        """The core assertion: does VRAM come back to the pre-load floor?

        ``target_mb`` overrides the pre-load baseline, which is what the second
        load/unload cycle wants: the floor a process can actually return to is
        the CUDA primary context, allocated by the first load and released only
        at exit. Cycle 1 measures that floor; cycle 2 asks whether it grows,
        and growth is what "leak" means for a worker meant to run for weeks.
        """
        assert self.vram
        baseline = self.result["baseline_worker_mb"] if target_mb is None else target_mb
        started = self.mark(f"{key}-start")
        reached, seconds, final = self.vram.settle(
            target_mb=baseline, tolerance_mb=SETTLE_TOLERANCE_MB, timeout=timeout
        )
        self.mark(f"{key}-end")
        summary = {
            "baseline_worker_mb": baseline,
            "returned_within_tolerance": reached,
            "seconds_to_settle": seconds,
            "final_mb": final,
            "delta_mb": None if final is None else final - baseline,
            "tolerance_mb": SETTLE_TOLERANCE_MB,
            "trace": [
                {"t": t, "used_mb": mb} for t, mb in self.vram.window(started)
            ],
        }
        self.result[key] = summary
        verdict = "returned" if reached else "DID NOT RETURN"
        print(f"  {key}: {verdict} target={baseline}MB final={final}MB in {seconds}s")
        if not reached:
            self.finding(
                f"[{key}] VRAM did not return to {baseline} MB: {final} MB "
                f"(+{None if final is None else final - baseline} MB)"
            )
        return summary

    def second_cycle(self, fire: Callable[[], Any], first: dict[str, Any], timeout: float):
        """Load the same backend again and unload it again, against cycle 1's floor."""
        self.mark("cycle2-request")
        fire()
        self.mark("cycle2-done")
        floor = first.get("final_mb")
        summary = self.settle(timeout=timeout, target_mb=floor, key="settle_cycle2")
        self.result["metrics"]["cuda_context_floor_mb"] = floor
        self.result["metrics"]["floor_growth_mb"] = (
            None if summary["final_mb"] is None or floor is None else summary["final_mb"] - floor
        )
        return summary


def _quiet_device_vram(attempts: int = 6) -> int | None:
    """Device VRAM with nothing of ours on it. Retries because a process that
    just exited can take a beat to give its context back."""
    seen = None
    for _ in range(attempts):
        seen = vram_used_mb()
        if seen is not None and seen < 100:
            return seen
        time.sleep(0.5)
    return seen


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------


def frame_paths(directory: Path, limit: int | None = None) -> list[Path]:
    frames = sorted(p for p in directory.glob("*.jpg"))
    return frames[:limit] if limit else frames


TRANSCRIPT_FALLBACK = [
    "the lifecycle manager owns the gpu and every job goes through one queue",
    (
        "word level alignment is what makes a timestamped citation land on the "
        "right sentence rather than somewhere in the paragraph"
    ),
]


def chunks_from_transcript(path: Path | None, count: int = 64) -> list[str]:
    """Realistic embedding input: whisperX segments glued into ~45 s windows.

    Real text matters here — token counts drive both the batch's activation
    peak and its throughput, and lorem ipsum is neither the length nor the
    vocabulary a transcript is.
    """
    segments: list[dict[str, Any]] = []
    if path and path.exists():
        payload = json.loads(path.read_text())
        segments = payload.get("segments", [])
    if not segments:
        return [f"{TRANSCRIPT_FALLBACK[i % 2]} ({i})" for i in range(count)]

    windows: list[str] = []
    window: list[str] = []
    window_start = segments[0].get("start", 0.0)
    for segment in segments:
        window.append(str(segment.get("text", "")).strip())
        if float(segment.get("end", 0.0)) - float(window_start) >= 45.0:
            windows.append(" ".join(window))
            window = []
            window_start = segment.get("end", 0.0)
    if window:
        windows.append(" ".join(window))
    if not windows:
        windows = [" ".join(str(s.get("text", "")) for s in segments)]
    # Repeat to reach the batch size the case asks for; a 5-minute clip is
    # ~7 windows and the point of the case is a 64-item batch.
    return [windows[i % len(windows)] for i in range(count)]


# --------------------------------------------------------------------------
# phase 1 — the backend matrix
# --------------------------------------------------------------------------


def check_stt(args: argparse.Namespace) -> None:
    audio = Path(args.audio)
    seconds = audio_seconds(audio)
    env = {"IDLE_UNLOAD_SECONDS": str(args.idle_ttl), "STT_BATCH_SIZE": str(args.stt_batch)}
    with Check("stt", env, args.out, args.tag) as check:
        assert check.worker and check.status

        t_request = check.mark("stt-request-cold")
        response = post_multipart(
            f"{WORKER_URL}/v1/audio/transcriptions",
            files=[("file", audio)],
            fields={"response_format": "verbose_json", "language": "en"},
            timeout=3600,
        )
        t_done = check.mark("stt-done-cold")
        if response.status != 200:
            check.finding(f"cold STT request failed: {response.status} {dict(response)}")
            return

        loaded_at = check.status.first_loaded_at("stt")
        load_log = check.worker.load_times()
        segments = response.get("segments", [])
        words = [w for s in segments for w in (s.get("words") or [])]
        timed = [w for w in words if w.get("start") is not None]

        check.metric(
            audio_file=audio.name,
            audio_seconds=seconds,
            cold_request_s=round(response.elapsed_s, 2),
            model_load_s=(load_log[0]["seconds"] if load_log else None),
            load_observed_at_s=loaded_at,
            vram_peak_during_load_mb=check.peak(t_request, loaded_at or t_done),
            vram_peak_during_inference_mb=check.peak(loaded_at or t_request, t_done),
            vram_peak_overall_mb=check.peak(t_request, t_done),
            segments=len(segments),
            words=len(words),
            words_with_timestamps=len(timed),
            language=response.get("language"),
            first_words=[
                {k: w.get(k) for k in ("word", "start", "end", "score")} for w in words[:5]
            ],
        )
        if seconds:
            inference_only = response.elapsed_s - (load_log[0]["seconds"] if load_log else 0.0)
            check.metric(
                realtime_factor_incl_load=round(seconds / response.elapsed_s, 2),
                realtime_factor_inference_only=round(seconds / max(inference_only, 1e-6), 2),
            )
        if not timed:
            check.finding("no word-level timestamps in verbose_json output")

        # Warm: same process, model already resident. Repeated, because a
        # single request can be shorter than the sampling interval and a peak
        # you did not sample is not a peak you measured.
        warm_audio = Path(args.audio2) if args.audio2 else audio
        warm_seconds = audio_seconds(warm_audio)
        t_warm = check.mark("stt-request-warm")
        warm_elapsed = 0.0
        warm = None
        for _ in range(args.repeat):
            warm = post_multipart(
                f"{WORKER_URL}/v1/audio/transcriptions",
                files=[("file", warm_audio)],
                fields={"response_format": "verbose_json", "language": "en"},
                timeout=3600,
            )
            warm_elapsed += warm.elapsed_s
        t_warm_done = check.mark("stt-done-warm")
        check.metric(
            warm_audio_file=warm_audio.name,
            warm_audio_seconds=warm_seconds,
            warm_repeats=args.repeat,
            warm_request_s=round(warm_elapsed / args.repeat, 2),
            warm_realtime_factor=(
                round(warm_seconds * args.repeat / warm_elapsed, 2) if warm_seconds else None
            ),
            vram_peak_warm_inference_mb=check.peak(t_warm, t_warm_done),
            vram_steady_loaded_mb=check.steady("stt-steady"),
            warm_status=(warm.status if warm else None),
        )
        (args.out / f"transcript{args.tag}.json").write_text(json.dumps(dict(response)))

        first = check.settle(timeout=args.idle_ttl + 75)
        check.second_cycle(
            lambda: post_multipart(
                f"{WORKER_URL}/v1/audio/transcriptions",
                files=[("file", warm_audio)],
                fields={"response_format": "json", "language": "en"},
                timeout=3600,
            ),
            first,
            timeout=args.idle_ttl + 75,
        )
        check.metric(
            reload_s=(
                check.worker.load_times()[1]["seconds"]
                if len(check.worker.load_times()) > 1
                else None
            )
        )


def check_embed(args: argparse.Namespace) -> None:
    texts = chunks_from_transcript(args.out / f"transcript{args.tag}.json", args.embed_batch)
    env = {"IDLE_UNLOAD_SECONDS": str(args.idle_ttl)}
    with Check("embed", env, args.out, args.tag) as check:
        assert check.worker and check.status

        t_request = check.mark("embed-request-cold")
        cold = post_json(f"{WORKER_URL}/v1/embeddings", {"input": texts}, timeout=900)
        t_done = check.mark("embed-done-cold")
        if cold.status != 200:
            check.finding(f"cold embed request failed: {cold.status} {dict(cold)}")
            return

        loaded_at = check.status.first_loaded_at("embed")
        load_log = check.worker.load_times()
        vectors = cold.get("data", [])

        t_warm = check.mark("embed-request-warm")
        warm_elapsed = 0.0
        for _ in range(args.repeat):
            warm = post_json(f"{WORKER_URL}/v1/embeddings", {"input": texts}, timeout=900)
            warm_elapsed += warm.elapsed_s
        t_warm_done = check.mark("embed-done-warm")
        steady = check.steady("embed-steady")
        query_elapsed = 0.0
        for _ in range(args.repeat):
            query = post_json(
                f"{WORKER_URL}/v1/embeddings",
                {"input": ["what is a turing machine"], "input_type": "query"},
                timeout=900,
            )
            query_elapsed += query.elapsed_s

        check.metric(
            batch=len(texts),
            mean_chars=round(sum(len(t) for t in texts) / max(len(texts), 1), 1),
            model_load_s=(load_log[0]["seconds"] if load_log else None),
            load_observed_at_s=loaded_at,
            cold_request_s=round(cold.elapsed_s, 2),
            warm_repeats=args.repeat,
            warm_request_s=round(warm_elapsed / args.repeat, 3),
            embeddings_per_s_warm=round(len(texts) * args.repeat / warm_elapsed, 1),
            single_query_warm_s=round(query_elapsed / args.repeat, 3),
            dims=(len(vectors[0]["embedding"]) if vectors else None),
            vectors=len(vectors),
            vram_peak_during_load_mb=check.peak(t_request, loaded_at or t_done),
            vram_peak_cold_request_mb=check.peak(t_request, t_done),
            vram_peak_warm_mb=check.peak(t_warm, t_warm_done),
            vram_steady_loaded_mb=steady,
        )
        first = check.settle(timeout=args.idle_ttl + 75)
        check.second_cycle(
            lambda: post_json(f"{WORKER_URL}/v1/embeddings", {"input": texts}, timeout=900),
            first,
            timeout=args.idle_ttl + 75,
        )
        reloads = check.worker.load_times()
        check.metric(reload_s=reloads[1]["seconds"] if len(reloads) > 1 else None)


def check_image_embed(args: argparse.Namespace) -> None:
    frames = frame_paths(Path(args.frames), args.frames_limit)
    env = {"IDLE_UNLOAD_SECONDS": str(args.idle_ttl)}
    with Check("image_embed", env, args.out, args.tag) as check:
        assert check.worker and check.status

        t_request = check.mark("image-request-cold")
        cold = post_multipart(
            f"{WORKER_URL}/v1/embeddings/image",
            files=[("file", path) for path in frames],
            timeout=1800,
        )
        t_done = check.mark("image-done-cold")
        if cold.status != 200:
            check.finding(f"image embed failed: {cold.status} {dict(cold)}")
            return

        loaded_at = check.status.first_loaded_at("image_embed")
        load_log = check.worker.load_times()

        t_warm = check.mark("image-request-warm")
        warm_elapsed = 0.0
        for _ in range(args.repeat):
            warm = post_multipart(
                f"{WORKER_URL}/v1/embeddings/image",
                files=[("file", path) for path in frames],
                timeout=1800,
            )
            warm_elapsed += warm.elapsed_s
        t_warm_done = check.mark("image-done-warm")
        steady = check.steady("image-steady")

        queries = [
            "a terminal with a rust compiler error",
            "a slide with a diagram of a turing machine",
            "source code in an editor",
        ]
        t_query = check.mark("frame-query")
        query_elapsed = 0.0
        for _ in range(args.repeat * 4):
            query = post_json(
                f"{WORKER_URL}/v1/embeddings/frame-query", {"input": queries}, timeout=600
            )
            query_elapsed += query.elapsed_s
        t_query_done = check.mark("frame-query-done")

        state = backend_state(dict(check.worker.status()))
        image_slot = state.get("image_embed", {})
        both_towers_one_load = image_slot.get("load_count") == 1
        if not both_towers_one_load:
            check.finding(
                "the text tower did not share the image tower's load: "
                f"image_embed load_count={image_slot.get('load_count')}"
            )

        image_vectors = cold.get("data", [])
        query_vectors = query.get("data", [])
        check.metric(
            frames=len(frames),
            model_load_s=(load_log[0]["seconds"] if load_log else None),
            load_observed_at_s=loaded_at,
            cold_request_s=round(cold.elapsed_s, 2),
            warm_repeats=args.repeat,
            warm_request_s=round(warm_elapsed / args.repeat, 3),
            frames_per_s_warm=round(len(frames) * args.repeat / warm_elapsed, 2),
            image_dims=(len(image_vectors[0]["embedding"]) if image_vectors else None),
            frame_query_s=round(query_elapsed / (args.repeat * 4), 4),
            frame_query_dims=(len(query_vectors[0]["embedding"]) if query_vectors else None),
            frame_query_count=len(query_vectors),
            image_embed_load_count=image_slot.get("load_count"),
            image_embed_job_count=image_slot.get("job_count"),
            both_towers_one_load=both_towers_one_load,
            vram_peak_during_load_mb=check.peak(t_request, loaded_at or t_done),
            vram_peak_during_inference_mb=check.peak(loaded_at or t_request, t_done),
            vram_peak_warm_mb=check.peak(t_warm, t_warm_done),
            vram_peak_frame_query_mb=check.peak(t_query, t_query_done),
            vram_steady_loaded_mb=steady,
        )
        first = check.settle(timeout=args.idle_ttl + 75)
        check.second_cycle(
            lambda: post_json(
                f"{WORKER_URL}/v1/embeddings/frame-query", {"input": queries}, timeout=600
            ),
            first,
            timeout=args.idle_ttl + 75,
        )
        reloads = check.worker.load_times()
        check.metric(
            reload_s=reloads[1]["seconds"] if len(reloads) > 1 else None,
            reload_triggered_by="frame-query (text tower)",
        )


def check_ocr(args: argparse.Namespace) -> None:
    frames = frame_paths(Path(args.frames), min(args.frames_limit, 64))
    env = {"IDLE_UNLOAD_SECONDS": str(args.idle_ttl)}
    with Check("ocr", env, args.out, args.tag) as check:
        assert check.worker

        t_request = check.mark("ocr-request")
        response = post_multipart(
            f"{WORKER_URL}/v1/ocr", files=[("file", p) for p in frames], timeout=900
        )
        t_done = check.mark("ocr-done")
        if response.status != 200:
            check.finding(f"ocr failed: {response.status} {dict(response)}")
            return

        results = response.get("results", response.get("data", []))
        lines = sum(len(r.get("items", r.get("lines", []))) for r in results)
        status = dict(check.worker.status())
        check.metric(
            frames=len(frames),
            request_s=round(response.elapsed_s, 2),
            frames_per_s=round(len(frames) / response.elapsed_s, 2),
            lines=lines,
            vram_before_mb=check.result["baseline_worker_mb"],
            vram_peak_during_ocr_mb=check.peak(t_request, t_done),
            vram_after_mb=vram_used_mb(),
            ocr_vram_estimate_mb=backend_state(status).get("ocr", {}).get("vram_estimate_mb"),
            lease_acquired_for_cpu_backend=status.get("lease", {}).get("acquired"),
        )
        delta = (check.peak(t_request, t_done) or 0) - (check.result["baseline_worker_mb"] or 0)
        if delta > 50:
            check.finding(f"OCR moved device VRAM by {delta} MB — it is supposed to be CPU-only")
        if status.get("lease", {}).get("acquired"):
            check.finding(
                "a CPU-only OCR request acquired the GPU lease "
                "(lifecycle._ensure_loaded runs the acquire hook before every first "
                "load, whatever the backend's VRAM estimate)"
            )
        check.settle(timeout=args.idle_ttl + 60)


# --------------------------------------------------------------------------
# phase 2 — lifecycle under pressure
# --------------------------------------------------------------------------


def check_resident(args: argparse.Namespace) -> None:
    texts = chunks_from_transcript(args.out / f"transcript{args.tag}.json", 32)
    frames = frame_paths(Path(args.frames), args.frames_limit)
    env = {"IDLE_UNLOAD_SECONDS": str(args.idle_ttl), "EMBED_RESIDENT": "1"}
    with Check("resident", env, args.out, args.tag) as check:
        assert check.worker and check.status

        check.mark("embed-request")
        embed = post_json(f"{WORKER_URL}/v1/embeddings", {"input": texts}, timeout=900)
        check.mark("image-request")
        image = post_multipart(
            f"{WORKER_URL}/v1/embeddings/image",
            files=[("file", p) for p in frames],
            timeout=1800,
        )
        check.mark("ocr-request")
        ocr = post_multipart(
            f"{WORKER_URL}/v1/ocr",
            files=[("file", p) for p in frames[:4]],
            timeout=900,
        )
        loaded_peak = vram_used_mb()
        check.mark("all-loaded")

        wait = args.idle_ttl * 2 + 20
        print(f"  waiting {wait:.0f}s for the reaper…")
        time.sleep(wait)
        check.mark("after-reaper")

        status = dict(check.worker.status())
        state = backend_state(status)
        resident_survived = state["embed"]["loaded"]
        others_gone = not any(state[t]["loaded"] for t in ("image_embed", "ocr"))
        settled_mb = vram_used_mb()

        check.metric(
            embed_status=embed.status,
            image_status=image.status,
            ocr_status=ocr.status,
            vram_all_loaded_mb=loaded_peak,
            vram_after_reaper_mb=settled_mb,
            resident_embed_still_loaded=resident_survived,
            evictable_backends_unloaded=others_gone,
            embed_unload_count=state["embed"]["unload_count"],
            image_embed_unload_count=state["image_embed"]["unload_count"],
            resident_cost_mb=(
                None
                if settled_mb is None or check.result["baseline_worker_mb"] is None
                else settled_mb - check.result["baseline_worker_mb"]
            ),
            lease_still_acquired=status.get("lease", {}).get("acquired"),
        )
        if not resident_survived:
            check.finding("EMBED_RESIDENT=1 did not survive the idle reaper")
        if not others_gone:
            check.finding("non-resident backends were still loaded past the idle TTL")


def check_queue(args: argparse.Namespace) -> None:
    """One GPU job at a time: fire STT and an embedding batch together."""
    audio = Path(args.audio2 or args.audio)
    texts = chunks_from_transcript(args.out / f"transcript{args.tag}.json", args.embed_batch)
    env = {"IDLE_UNLOAD_SECONDS": "0"}
    with Check("queue", env, args.out, args.tag) as check:
        assert check.worker and check.status

        results: dict[str, dict[str, Any]] = {}

        def fire_stt() -> None:
            start = check.vram.now()
            response = post_multipart(
                f"{WORKER_URL}/v1/audio/transcriptions",
                files=[("file", audio)],
                fields={"response_format": "json", "language": "en"},
                timeout=3600,
            )
            results["stt"] = {
                "status": response.status,
                "start": start,
                "end": check.vram.now(),
                "elapsed_s": round(response.elapsed_s, 2),
            }

        def fire_embed() -> None:
            start = check.vram.now()
            response = post_json(f"{WORKER_URL}/v1/embeddings", {"input": texts}, timeout=1800)
            results["embed"] = {
                "status": response.status,
                "start": start,
                "end": check.vram.now(),
                "elapsed_s": round(response.elapsed_s, 2),
            }

        check.mark("concurrent-fire")
        threads = [threading.Thread(target=fire_stt), threading.Thread(target=fire_embed)]
        threads[0].start()
        time.sleep(0.2)  # deterministic order: STT is first in the queue
        threads[1].start()
        for thread in threads:
            thread.join()
        check.mark("both-done")

        spans = check.status.running_spans()
        overlap = check.status.concurrent_running()
        check.metric(
            requests=results,
            wall_clock_s=round(
                max(r["end"] for r in results.values())
                - min(r["start"] for r in results.values()),
                2,
            ),
            sum_of_request_s=round(sum(r["elapsed_s"] for r in results.values()), 2),
            running_spans=spans,
            max_queue_depth=check.status.max_depth(),
            max_in_flight=check.status.max_in_flight(),
            concurrent_gpu_jobs_observed=overlap,
        )
        if overlap:
            check.finding("two tasks were reported running at once — jobs are not serialised")
        if check.status.max_in_flight() < 2:
            check.finding(
                "never saw two in-flight requests; the concurrency test did not "
                "actually overlap"
            )
        # IDLE_UNLOAD_SECONDS=0 is documented to disable idle unloading, so the
        # models are supposed to still be up here. Same trace, opposite
        # assertion to every other check.
        time.sleep(20)
        check.mark("ttl-zero-wait")
        still = [b["task"] for b in dict(check.worker.status())["backends"] if b["loaded"]]
        check.metric(loaded_20s_after_last_job=still, vram_held_mb=vram_used_mb())
        if sorted(still) != ["embed", "stt"]:
            check.finding(
                f"IDLE_UNLOAD_SECONDS=0 should disable idle unloading; loaded={still}"
            )


@dataclass
class Ballast:
    """A separate process squatting on VRAM."""

    mb: int
    log: Path
    process: subprocess.Popen | None = None

    def start(self, timeout: float = 180.0) -> Ballast:
        self.process = subprocess.Popen(
            ["uv", "run", "--no-sync", "python", "bench/ballast.py", "--mb", str(self.mb)],
            cwd=str(REPO),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            line = self.process.stdout.readline() if self.process.stdout else ""
            if line.startswith("ready"):
                self.log.write_text(line)
                return self
            if self.process.poll() is not None:
                raise RuntimeError(f"ballast exited: {line}")
        raise TimeoutError("ballast never became ready")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.process.kill()
        self.process = None


def check_evict(args: argparse.Namespace) -> None:
    """Shrink free VRAM under a co-tenant and watch admission control decide.

    The specified behaviour (lifecycle.py ``_admit``/``_eviction_candidate``):
    evict the least-recently-used non-resident backend until the estimate fits,
    and only when nothing is left to evict raise ``InsufficientVRAM`` — which
    ``app.py`` turns into 503 with ``Retry-After: 30``. Both halves are checked.
    """
    texts = chunks_from_transcript(args.out / f"transcript{args.tag}.json", 16)
    frames = frame_paths(Path(args.frames), args.frames_limit)
    audio = Path(args.audio2 or args.audio)
    env = {"IDLE_UNLOAD_SECONDS": "0"}
    ballast: Ballast | None = None

    with Check("evict", env, args.out, args.tag) as check:
        assert check.worker and check.status
        try:
            # Fill two slots so there is an LRU order to respect: embed first,
            # so it is the older of the two by last_used.
            check.mark("load-embed")
            post_json(f"{WORKER_URL}/v1/embeddings", {"input": texts}, timeout=900)
            check.mark("load-stt")
            post_multipart(
                f"{WORKER_URL}/v1/audio/transcriptions",
                files=[("file", audio)],
                fields={"response_format": "json", "language": "en"},
                timeout=3600,
            )
            check.mark("two-loaded")
            two_loaded_mb = vram_used_mb()
            status = dict(check.worker.status())
            estimates = {
                b["task"]: b["vram_estimate_mb"] for b in status.get("backends", [])
            }
            image_need = estimates["image_embed"] + int(
                check.env.get("VRAM_HEADROOM_MB", 512)
            )

            # Leave the frame embedder short by ~1 GB with both other models up,
            # but comfortable once they are evicted.
            free_now = status["vram"]["free_mb"]
            squeeze_mb = max(1000, free_now - image_need + 1000)
            print(
                f"  free={free_now}MB image_embed needs ~{image_need}MB; "
                f"ballast {squeeze_mb}MB"
            )
            ballast = Ballast(squeeze_mb, args.out / f"ballast{args.tag}.txt").start()
            check.mark("ballast-up")
            time.sleep(2)
            before = dict(check.worker.status())

            check.mark("image-request-under-pressure")
            response = post_multipart(
                f"{WORKER_URL}/v1/embeddings/image",
                files=[("file", p) for p in frames],
                timeout=1800,
            )
            check.mark("image-response")
            after = dict(check.worker.status())
            unloads = check.worker.unloads()

            check.metric(
                vram_two_models_loaded_mb=two_loaded_mb,
                estimates_mb=estimates,
                free_before_ballast_mb=free_now,
                ballast_mb=squeeze_mb,
                free_under_ballast_mb=before["vram"]["free_mb"],
                image_embed_need_mb=image_need,
                pressure_response_status=response.status,
                pressure_response_body=dict(response) if response.status != 200 else "ok",
                retry_after=response.headers.get("retry-after"),
                unload_reasons=unloads,
                loaded_before=[b["task"] for b in before["backends"] if b["loaded"]],
                loaded_after=[b["task"] for b in after["backends"] if b["loaded"]],
            )
            if response.status == 200:
                evicted = [u["task"] for u in unloads if u["reason"] == "vram pressure"]
                check.result["metrics"]["evicted_for_pressure"] = evicted
                if evicted[:1] != ["embed"]:
                    check.finding(
                        f"eviction order was {evicted}; LRU says embed should go first"
                    )
            elif response.status != 503:
                check.finding(
                    f"pressure produced {response.status}, expected 200 (after eviction) "
                    "or 503 insufficient_vram"
                )

        finally:
            if ballast is not None:
                ballast.stop()
            time.sleep(2)
        check.settle(timeout=120)


def check_no_room(args: argparse.Namespace) -> None:
    """The other branch of ``_admit``: nothing evictable and it still does not fit.

    Specified as ``InsufficientVRAM`` → ``app.py`` → 503 with ``Retry-After: 30``.
    A fresh worker with nothing loaded is the honest way to reach it: with a
    model already up, eviction would free enough and the branch never runs.
    """
    frames = frame_paths(Path(args.frames), 8)
    env = {"IDLE_UNLOAD_SECONDS": "0"}
    ballast: Ballast | None = None
    with Check("no-room", env, args.out, args.tag) as check:
        assert check.worker
        try:
            status = dict(check.worker.status())
            estimate = {b["task"]: b["vram_estimate_mb"] for b in status["backends"]}
            need = estimate["image_embed"] + 512
            free = status["vram"]["free_mb"]
            # Leave ~2 GB: less than the frame embedder's estimate + headroom,
            # more than zero, so the refusal is admission control and not an
            # allocator failure.
            squeeze = max(1000, free - 2000)
            print(f"  free={free}MB, image_embed needs ~{need}MB, ballast {squeeze}MB")
            ballast = Ballast(squeeze, args.out / f"ballast-noroom{args.tag}.txt").start()
            check.mark("ballast-up")
            time.sleep(2)
            under = dict(check.worker.status())

            check.mark("request-no-room")
            refused = post_multipart(
                f"{WORKER_URL}/v1/embeddings/image",
                files=[("file", p) for p in frames],
                timeout=900,
            )
            check.mark("refusal")
            check.metric(
                estimates_mb=estimate,
                free_before_ballast_mb=free,
                ballast_mb=squeeze,
                free_under_ballast_mb=under["vram"]["free_mb"],
                image_embed_need_mb=need,
                refusal_status=refused.status,
                refusal_retry_after=refused.headers.get("retry-after"),
                refusal_type=(refused.get("error") or {}).get("type"),
                refusal_message=(refused.get("error") or {}).get("message"),
                loaded_after=[b["task"] for b in dict(check.worker.status())["backends"] if b["loaded"]],
                lease_acquired_after=dict(check.worker.status())["lease"]["acquired"],
            )
            if refused.status != 503:
                check.finding(
                    f"no-room request returned {refused.status}, not the specified 503"
                )
            else:
                if refused.headers.get("retry-after") != "30":
                    check.finding(
                        "503 came back without the specified Retry-After: 30 "
                        f"(got {refused.headers.get('retry-after')!r})"
                    )
                if (refused.get("error") or {}).get("type") != "insufficient_vram":
                    check.finding(
                        "503 body did not carry type=insufficient_vram: "
                        f"{dict(refused)}"
                    )
            # A refused load must not leave the lease held: nothing is loaded,
            # so a co-tenant should have been handed the card back.
            if dict(check.worker.status())["lease"]["acquired"]:
                check.finding(
                    "the GPU lease is still held after a load was refused and "
                    "nothing is loaded — a co-tenant never gets the card back"
                )
        finally:
            if ballast is not None:
                ballast.stop()
            time.sleep(2)


def check_stt_underestimate(args: argparse.Namespace) -> None:
    """Admission control gates the *load*; inference is what actually peaks.

    ``WhisperXBackend.default_vram_mb`` is 3200 and the measured inference peak
    on this card is more than twice that, so there is a band of free VRAM where
    the load is admitted and the job then has nowhere to run. This check parks a
    co-tenant inside that band and reports what the caller gets.
    """
    audio = Path(args.audio2 or args.audio)
    env = {"IDLE_UNLOAD_SECONDS": str(args.idle_ttl)}
    ballast: Ballast | None = None
    with Check("stt-underestimate", env, args.out, args.tag) as check:
        assert check.worker
        try:
            status = dict(check.worker.status())
            estimate = {b["task"]: b["vram_estimate_mb"] for b in status["backends"]}
            need = estimate["stt"] + 512
            free = status["vram"]["free_mb"]
            target_free = args.admit_band_mb
            squeeze = max(1000, free - target_free)
            print(
                f"  stt admits at {need}MB; leaving ~{target_free}MB free "
                f"(measured inference peak is far above it)"
            )
            ballast = Ballast(squeeze, args.out / f"ballast-band{args.tag}.txt").start()
            check.mark("ballast-up")
            time.sleep(2)
            under = dict(check.worker.status())

            t_request = check.mark("stt-request-in-band")
            response = post_multipart(
                f"{WORKER_URL}/v1/audio/transcriptions",
                files=[("file", audio)],
                fields={"response_format": "json", "language": "en"},
                timeout=1800,
            )
            t_done = check.mark("stt-response")
            after = dict(check.worker.status())
            check.metric(
                stt_estimate_mb=estimate["stt"],
                admission_need_mb=need,
                free_left_mb=under["vram"]["free_mb"],
                admitted=bool(check.worker.load_times()),
                status=response.status,
                error_type=(response.get("error") or {}).get("type"),
                error_message=str((response.get("error") or {}).get("message"))[:400],
                vram_peak_mb=check.peak(t_request, t_done),
                loaded_after=[b["task"] for b in after["backends"] if b["loaded"]],
            )
            if response.status == 200:
                print("  the job fitted after all — widen --admit-band-mb to reproduce")
            else:
                check.finding(
                    f"admission control admitted an STT load with "
                    f"{under['vram']['free_mb']} MB free (estimate {estimate['stt']} + "
                    f"headroom) and the job then failed: {response.status} "
                    f"{(response.get('error') or {}).get('type')}"
                )
            # Recovery: with the co-tenant gone, does the same request work?
            ballast.stop()
            ballast = None
            time.sleep(3)
            check.mark("retry-after-ballast")
            retry = post_multipart(
                f"{WORKER_URL}/v1/audio/transcriptions",
                files=[("file", audio)],
                fields={"response_format": "json", "language": "en"},
                timeout=1800,
            )
            check.metric(
                retry_status=retry.status,
                retry_elapsed_s=round(retry.elapsed_s, 2),
                recovered=retry.status == 200,
            )
            if retry.status != 200:
                check.finding(
                    "the worker did not recover once the co-tenant released VRAM: "
                    f"{retry.status} {dict(retry)} — the failed backend stays "
                    "`loaded` and every later job hits the same broken context"
                )
                # If the idle reaper is on, it will eventually unload the wedged
                # backend and the next request reloads it. Whether that is the
                # recovery path is worth knowing: it means IDLE_UNLOAD_SECONDS=0
                # turns one OOM into a permanent outage.
                if args.idle_ttl > 0:
                    wait = args.idle_ttl * 2 + 10
                    print(f"  waiting {wait:.0f}s to see if the reaper unwedges it…")
                    time.sleep(wait)
                    check.mark("retry-after-reaper")
                    reaped = post_multipart(
                        f"{WORKER_URL}/v1/audio/transcriptions",
                        files=[("file", audio)],
                        fields={"response_format": "json", "language": "en"},
                        timeout=1800,
                    )
                    check.metric(
                        unloads_before_reaper_retry=check.worker.unloads(),
                        retry_after_reaper_status=reaped.status,
                        recovered_after_idle_unload=reaped.status == 200,
                    )
                    if reaped.status == 200:
                        check.finding(
                            "recovery came from the idle reaper unloading the wedged "
                            "backend, not from any error handling — with "
                            "IDLE_UNLOAD_SECONDS=0 the slot never recovers"
                        )
        finally:
            if ballast is not None:
                ballast.stop()
            time.sleep(2)


HOOK_STUB = """#!/bin/sh
printf '%s %s %s\\n' "$(date +%s.%N)" "$1" "$(nvidia-smi --query-gpu=memory.used \
--format=csv,noheader,nounits | head -1)" >> "{path}"
"""


def _hook_stub(out: Path, tag: str) -> tuple[Path, Path]:
    log = out / f"hooks{tag}.log"
    log.unlink(missing_ok=True)
    script = out / f"hook{tag}.sh"
    script.write_text(HOOK_STUB.format(path=log))
    script.chmod(0o755)
    return script, log


def _hook_lines(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    out = []
    for line in log.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 3:
            out.append({"epoch": float(parts[0]), "event": parts[1], "vram_mb": int(parts[2])})
    return out


def check_hooks(args: argparse.Namespace) -> None:
    script, log = _hook_stub(args.out, args.tag)
    texts = chunks_from_transcript(args.out / f"transcript{args.tag}.json", 16)
    frames = frame_paths(Path(args.frames), 8)
    env = {
        "IDLE_UNLOAD_SECONDS": str(args.idle_ttl),
        "GPU_ACQUIRE_CMD": f"{script} acquire",
        "GPU_RELEASE_CMD": f"{script} release",
    }
    with Check("hooks", env, args.out, args.tag) as check:
        assert check.worker

        boot_lines = _hook_lines(log)
        check.mark("embed-request")
        embed_start = time.time()
        post_json(f"{WORKER_URL}/v1/embeddings", {"input": texts}, timeout=900)
        check.mark("embed-done")
        after_first = _hook_lines(log)

        check.mark("image-request")
        post_multipart(
            f"{WORKER_URL}/v1/embeddings/image",
            files=[("file", p) for p in frames],
            timeout=1800,
        )
        check.mark("image-done")
        after_second = _hook_lines(log)

        wait = args.idle_ttl * 2 + 20
        print(f"  waiting {wait:.0f}s for the reaper to unload everything…")
        time.sleep(wait)
        check.mark("after-reaper")
        final = _hook_lines(log)

        acquires = [line for line in final if line["event"] == "acquire"]
        releases = [line for line in final if line["event"] == "release"]
        load_log = check.worker.load_times()
        check.metric(
            hook_lines=final,
            hooks_at_boot=len(boot_lines),
            acquires=len(acquires),
            releases=len(releases),
            acquires_after_first_request=len(
                [line for line in after_first if line["event"] == "acquire"]
            ),
            acquires_after_second_request=len(
                [line for line in after_second if line["event"] == "acquire"]
            ),
            acquire_vram_mb=(acquires[0]["vram_mb"] if acquires else None),
            release_vram_mb=(releases[-1]["vram_mb"] if releases else None),
            acquire_before_first_load=(
                bool(acquires) and acquires[0]["epoch"] >= embed_start
            ),
            loads=load_log,
            unloads=check.worker.unloads(),
            lease_acquired_now=dict(check.worker.status()).get("lease", {}).get("acquired"),
        )
        if len(boot_lines) != 0:
            check.finding("a hook ran at boot, before any load was requested")
        if len(acquires) != 1:
            check.finding(f"expected exactly one acquire, saw {len(acquires)}")
        if len(releases) != 1:
            check.finding(f"expected exactly one release, saw {len(releases)}")
        if acquires and acquires[0]["vram_mb"] > (check.result["baseline_worker_mb"] or 0) + 200:
            check.finding(
                "acquire fired with model VRAM already allocated — it is specified to "
                "run before the first load"
            )
        check.settle(timeout=args.idle_ttl + 75)


def check_hooks_ocr(args: argparse.Namespace) -> None:
    """Does a CPU-only backend take the GPU lease? (It is documented not to.)"""
    script, log = _hook_stub(args.out, f"-ocr{args.tag}")
    frames = frame_paths(Path(args.frames), 4)
    env = {
        "IDLE_UNLOAD_SECONDS": str(args.idle_ttl),
        "GPU_ACQUIRE_CMD": f"{script} acquire",
        "GPU_RELEASE_CMD": f"{script} release",
    }
    with Check("hooks-ocr", env, args.out, args.tag) as check:
        assert check.worker
        t_request = check.mark("ocr-request")
        response = post_multipart(
            f"{WORKER_URL}/v1/ocr", files=[("file", p) for p in frames], timeout=900
        )
        t_done = check.mark("ocr-done")
        lines = _hook_lines(log)
        wait = args.idle_ttl * 2 + 10
        time.sleep(wait)
        check.mark("after-reaper")
        final = _hook_lines(log)
        check.metric(
            ocr_status=response.status,
            hook_lines_after_ocr=lines,
            hook_lines_final=final,
            acquires=len([line for line in final if line["event"] == "acquire"]),
            vram_during_ocr_mb=check.peak(t_request, t_done),
        )
        if any(line["event"] == "acquire" for line in lines):
            check.finding(
                "GPU_ACQUIRE_CMD fired for a CPU-only OCR request: rapidocr_ocr.py "
                "documents that it 'never contends for the GPU lease', but "
                "lifecycle._ensure_loaded acquires before every first load "
                "regardless of vram_estimate_mb"
            )


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

CHECKS: dict[str, Callable[[argparse.Namespace], None]] = {
    "stt": check_stt,
    "embed": check_embed,
    "image_embed": check_image_embed,
    "ocr": check_ocr,
    "resident": check_resident,
    "queue": check_queue,
    "evict": check_evict,
    "no-room": check_no_room,
    "stt-underestimate": check_stt_underestimate,
    "hooks": check_hooks,
    "hooks-ocr": check_hooks_ocr,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checks", nargs="*", help=f"one or more of: {', '.join(CHECKS)}")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--out", type=Path, default=REPO / "bench" / "results" / "raw")
    parser.add_argument("--tag", default="", help="suffix for output filenames")
    parser.add_argument("--audio", help="primary STT clip")
    parser.add_argument("--audio2", help="second, shorter clip for the warm case")
    parser.add_argument("--frames", help="directory of keyframe JPEGs")
    parser.add_argument("--frames-limit", type=int, default=44)
    parser.add_argument("--embed-batch", type=int, default=64)
    parser.add_argument("--stt-batch", type=int, default=16)
    parser.add_argument(
        "--repeat",
        type=int,
        default=3,
        help="warm-path repetitions, so the sampled window outlives the poll interval",
    )
    parser.add_argument("--idle-ttl", type=float, default=15.0)
    parser.add_argument(
        "--admit-band-mb",
        type=int,
        default=4500,
        help="free VRAM to leave for stt-underestimate: above the estimate, below "
        "the measured inference peak",
    )
    args = parser.parse_args(argv)

    if args.list or not args.checks:
        print("checks:", ", ".join(CHECKS))
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    for name in args.checks:
        if name not in CHECKS:
            print(f"unknown check {name!r}; try --list", file=sys.stderr)
            return 2

    for name in args.checks:
        print(f"\n=== {name}")
        started = time.perf_counter()
        CHECKS[name](args)
        print(f"=== {name} done in {time.perf_counter() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
