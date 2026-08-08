"""Measurement plumbing shared by the bench runner and the GPU validation driver.

Stdlib only, same rule as ``run.py``: a checkout with nothing installed can
still measure a running worker.

Three things live here, and none of them know what a scenario is:

* :class:`VramSampler` — a background thread polling ``nvidia-smi`` at a fixed
  interval. Whole-device MiB, not per-process, because that is the number that
  decides whether a co-tenant (llama.cpp) fits, and it is the number the
  lifecycle manager's own NVML probe reasons about.
* a tiny HTTP client (``urllib``) that speaks the worker's two request shapes:
  JSON, and the multipart uploads ``/v1/audio/transcriptions``, ``/v1/ocr``
  and ``/v1/embeddings/image`` take.
* :class:`Worker` — start a worker process with a given environment, wait for
  ``/healthz``, read back its log. Backend selection and every lifecycle knob
  are startup-time, so "a variant" means "a process".
"""

from __future__ import annotations

import itertools
import json
import mimetypes
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# VRAM sampling
# --------------------------------------------------------------------------

NVIDIA_SMI = "nvidia-smi"
_QUERY = ["--query-gpu=memory.used", "--format=csv,noheader,nounits"]


def vram_used_mb(index: int = 0) -> int | None:
    """Whole-device VRAM in use, MiB. ``None`` when nvidia-smi is not there."""
    try:
        out = subprocess.run(
            [NVIDIA_SMI, "-i", str(index), *_QUERY],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    line = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
    try:
        return int(float(line))
    except ValueError:
        return None


@dataclass
class VramSampler:
    """Poll device VRAM on a thread and keep the whole trace.

    The trace is the deliverable, not the peak: "did it come back down" is a
    shape over time, and a max() cannot show a model that unloaded three
    seconds late or not at all.
    """

    interval: float = 0.5
    index: int = 0
    samples: list[tuple[float, int]] = field(default_factory=list)
    marks: list[tuple[float, str]] = field(default_factory=list)
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _t0: float = 0.0

    def start(self) -> VramSampler:
        self._t0 = time.perf_counter()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.is_set():
            used = vram_used_mb(self.index)
            if used is not None:
                self.samples.append((round(time.perf_counter() - self._t0, 3), used))
            self._stop.wait(self.interval)

    def stop(self) -> VramSampler:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval * 4)
        return self

    # -- reading it back ---------------------------------------------------
    def now(self) -> float:
        return round(time.perf_counter() - self._t0, 3)

    def mark(self, label: str) -> float:
        """Annotate the timeline. Marks are how a trace becomes readable."""
        stamp = self.now()
        self.marks.append((stamp, label))
        return stamp

    def window(self, start: float, end: float | None = None) -> list[tuple[float, int]]:
        end = self.now() if end is None else end
        return [(t, mb) for t, mb in self.samples if start <= t <= end]

    def peak(self, start: float = 0.0, end: float | None = None) -> int | None:
        window = self.window(start, end)
        return max(mb for _, mb in window) if window else None

    def last(self) -> int | None:
        return self.samples[-1][1] if self.samples else None

    def settle(
        self,
        *,
        target_mb: int,
        tolerance_mb: int = 200,
        timeout: float = 120.0,
    ) -> tuple[bool, float | None, int | None]:
        """Wait for VRAM to fall back to ``target_mb`` ± tolerance.

        Returns ``(reached, seconds_from_call, final_mb)``. Polls the device
        directly rather than the sampler's buffer so the answer is not
        quantised by the sampling interval.
        """
        started = time.perf_counter()
        final = None
        while time.perf_counter() - started < timeout:
            final = vram_used_mb(self.index)
            if final is not None and final <= target_mb + tolerance_mb:
                return True, round(time.perf_counter() - started, 2), final
            time.sleep(0.25)
        return False, None, final

    def as_dict(self) -> dict[str, Any]:
        return {
            "interval_s": self.interval,
            "samples": [{"t": t, "used_mb": mb} for t, mb in self.samples],
            "marks": [{"t": t, "label": label} for t, label in self.marks],
        }


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class HTTPResult(dict):
    """A response, plus what it cost. ``status`` and ``elapsed_s`` are attributes
    so a caller can treat the body as the dict it usually is."""

    status: int = 200
    elapsed_s: float = 0.0
    headers: dict[str, str]
    raw: bytes = b""


def _finish(body: Any, status: int, headers: Any, elapsed: float) -> HTTPResult:
    result = HTTPResult(body if isinstance(body, dict) else {"body": body})
    result.status = status
    result.elapsed_s = elapsed
    result.headers = {k.lower(): v for k, v in (headers or {})}
    return result


def _request(req: urllib.request.Request, timeout: float) -> HTTPResult:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            elapsed = time.perf_counter() - started
            try:
                body = json.loads(raw)
            except ValueError:
                body = {"text": raw.decode(errors="replace")}
            return _finish(body, response.status, response.headers.items(), elapsed)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        elapsed = time.perf_counter() - started
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"text": raw.decode(errors="replace")}
        return _finish(body, exc.code, exc.headers.items(), elapsed)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Status 0 = "never reached the worker". A caller that cares checks
        # `.status`; nothing here should raise, because a probe loop waiting
        # for a process to come up is the common case.
        return _finish(
            {"error": {"message": str(exc), "type": "transport"}},
            0,
            [],
            time.perf_counter() - started,
        )


def get_json(url: str, timeout: float = 10.0) -> HTTPResult:
    return _request(urllib.request.Request(url, method="GET"), timeout)


def post_json(url: str, payload: dict[str, Any], timeout: float = 600.0) -> HTTPResult:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _request(req, timeout)


def post_multipart(
    url: str,
    *,
    files: list[tuple[str, Path]],
    fields: dict[str, Any] | None = None,
    timeout: float = 3600.0,
) -> HTTPResult:
    """``files`` is ``[(field_name, path), …]`` — repeat the name for a list."""
    boundary = uuid.uuid4().hex
    body = bytearray()
    for name, value in (fields or {}).items():
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        ).encode()
    for name, path in files:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode()
        body += path.read_bytes()
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    return _request(req, timeout)


# --------------------------------------------------------------------------
# the worker process
# --------------------------------------------------------------------------

LOADED_RE = re.compile(r"loaded (?P<task>\w+) backend=(?P<backend>[\w-]+) in (?P<secs>[\d.]+)s")
UNLOADED_RE = re.compile(r"unloading (?P<task>\w+) \((?P<reason>[^)]+)\)")


@dataclass
class Worker:
    """A worker process under a specific environment.

    ``--no-sync`` on purpose: this box's venv is synced to the worker package
    with the gpu extras, and letting uv re-resolve mid-run would swap the
    environment out from under a measurement.
    """

    env: dict[str, str]
    log_path: Path
    url: str = "http://127.0.0.1:8081"
    cwd: Path = REPO
    command: tuple[str, ...] = ("uv", "run", "--no-sync", "python", "-m", "vidtheque_worker")
    process: subprocess.Popen | None = None
    _log: Any = None

    def start(self, *, timeout: float = 120.0) -> Worker:
        # A worker left over from an earlier run answers /healthz, binds the
        # port and holds a CUDA context — every measurement after that is
        # against the wrong process. Refuse rather than measure a ghost.
        if get_json(f"{self.url}/healthz", timeout=2.0).status == 200:
            raise RuntimeError(
                f"something is already serving {self.url} — stop it before benching"
            )
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = self.log_path.open("w")
        environ = {**os.environ, **self.env}
        self.process = subprocess.Popen(
            list(self.command),
            cwd=str(self.cwd),
            env=environ,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"worker exited {self.process.returncode}; see {self.log_path}"
                )
            probe = get_json(f"{self.url}/healthz", timeout=2.0)
            if probe.status == 200:
                return self
            time.sleep(0.25)
        self.stop()
        raise TimeoutError(f"worker did not come up within {timeout}s; see {self.log_path}")

    def stop(self, *, timeout: float = 60.0) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:  # pragma: no cover - shutdown wedged
                self.process.kill()
                self.process.wait(timeout=10)
        if self._log is not None:
            self._log.flush()
            self._log.close()
            self._log = None
        self.process = None

    def __enter__(self) -> Worker:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- introspection -----------------------------------------------------
    def status(self) -> HTTPResult:
        return get_json(f"{self.url}/status")

    def log_text(self) -> str:
        try:
            return self.log_path.read_text(errors="replace")
        except OSError:  # pragma: no cover
            return ""

    def load_times(self) -> list[dict[str, Any]]:
        """``loaded <task> backend=<name> in <n>s`` lines, in order.

        The worker times its own loads to 0.1 s around ``backend.load()``. That
        is a tighter number than anything the client can see, which necessarily
        includes queueing and the HTTP round trip.
        """
        return [
            {"task": m["task"], "backend": m["backend"], "seconds": float(m["secs"])}
            for m in (LOADED_RE.search(line) for line in self.log_text().splitlines())
            if m
        ]

    def unloads(self) -> list[dict[str, str]]:
        return [
            {"task": m["task"], "reason": m["reason"]}
            for m in (UNLOADED_RE.search(line) for line in self.log_text().splitlines())
            if m
        ]

    def wait_for_loaded(self, task: str, *, timeout: float = 600.0) -> float | None:
        return self._wait_loaded_state(task, True, timeout)

    def wait_for_unloaded(self, task: str, *, timeout: float = 600.0) -> float | None:
        return self._wait_loaded_state(task, False, timeout)

    def _wait_loaded_state(self, task: str, want: bool, timeout: float) -> float | None:
        started = time.perf_counter()
        while time.perf_counter() - started < timeout:
            snap = self.status()
            if snap.status == 200:
                for backend in snap.get("backends", []):
                    if backend["task"] == task and backend["loaded"] is want:
                        return round(time.perf_counter() - started, 3)
            time.sleep(0.25)
        return None


@dataclass
class StatusPoller:
    """Poll ``/status`` on a thread and keep the trace.

    The lifecycle manager's whole contract is visible here and nowhere else:
    which slots are loaded, what is running on the GPU right now, how deep the
    queue is. Sampling it while requests are in flight is how "the manager
    serialises GPU work" stops being a claim — two tasks that never appear as
    ``running`` in overlapping windows is the evidence.
    """

    url: str
    interval: float = 0.25
    clock_offset: float = 0.0
    """Set to a :class:`VramSampler`'s ``_t0`` so both traces share a timeline."""

    samples: list[dict[str, Any]] = field(default_factory=list)
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _t0: float = 0.0

    def start(self, t0: float | None = None) -> StatusPoller:
        self._t0 = time.perf_counter() if t0 is None else t0
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.is_set():
            snap = get_json(f"{self.url}/status", timeout=5.0)
            if snap.status == 200:
                self.samples.append(
                    {
                        "t": round(time.perf_counter() - self._t0, 3),
                        "loaded": loaded_tasks(snap),
                        "running": snap.get("queue", {}).get("running"),
                        "depth": snap.get("queue", {}).get("depth"),
                        "in_flight": snap.get("queue", {}).get("in_flight"),
                        "vram_used_mb": snap.get("vram", {}).get("used_mb"),
                        "lease_acquired": snap.get("lease", {}).get("acquired"),
                        "load_counts": {
                            b["task"]: b["load_count"] for b in snap.get("backends", [])
                        },
                    }
                )
            self._stop.wait(self.interval)

    def stop(self) -> StatusPoller:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval * 8)
        return self

    # -- derived facts -----------------------------------------------------
    def transitions(self, task: str) -> list[dict[str, Any]]:
        """``[{t, loaded}]`` every time ``task`` flipped loaded state."""
        out: list[dict[str, Any]] = []
        previous: bool | None = None
        for sample in self.samples:
            now = task in sample["loaded"]
            if previous is None or now != previous:
                out.append({"t": sample["t"], "loaded": now})
                previous = now
        return out

    def first_loaded_at(self, task: str) -> float | None:
        for sample in self.samples:
            if task in sample["loaded"]:
                return sample["t"]
        return None

    def running_spans(self) -> list[dict[str, Any]]:
        """Contiguous windows where ``queue.running`` held one task."""
        spans: list[dict[str, Any]] = []
        for sample in self.samples:
            running = sample["running"]
            if running is None:
                continue
            if spans and spans[-1]["task"] == running and sample["t"] - spans[-1]["end"] <= (
                self.interval * 2.5
            ):
                spans[-1]["end"] = sample["t"]
            else:
                spans.append({"task": running, "start": sample["t"], "end": sample["t"]})
        return spans

    def max_depth(self) -> int:
        return max((s["depth"] or 0) for s in self.samples) if self.samples else 0

    def max_in_flight(self) -> int:
        return max((s["in_flight"] or 0) for s in self.samples) if self.samples else 0

    def concurrent_running(self) -> bool:
        """True if two tasks were ever reported running at once. Always False if
        the manager keeps its promise — ``running`` is a single slot, so this is
        really a check that spans of different tasks never overlap."""
        spans = self.running_spans()
        for first, second in itertools.pairwise(spans):
            if second["start"] < first["end"] and first["task"] != second["task"]:
                return True
        return False

    def as_dict(self) -> dict[str, Any]:
        return {"interval_s": self.interval, "samples": self.samples}


def backend_state(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {b["task"]: b for b in status.get("backends", [])}


def loaded_tasks(status: dict[str, Any]) -> list[str]:
    return [b["task"] for b in status.get("backends", []) if b["loaded"]]


# --------------------------------------------------------------------------
# small helpers the scenarios share
# --------------------------------------------------------------------------


def audio_seconds(path: Path) -> float | None:
    """Duration via ffprobe, so a scenario need not hardcode it."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return float(out.stdout.strip())
    except ValueError:
        return None


def pct(numerator: float, denominator: float) -> float | None:
    return None if not denominator else round(100.0 * numerator / denominator, 1)


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)
