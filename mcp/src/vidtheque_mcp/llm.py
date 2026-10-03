"""The model client every LLM caller shares — `docs/design/companion.md` §4.

One OpenAI-compatible chat client, moved here from the demo's ask so the
companion can reuse it, and one interface over it: :class:`Model` completes a
prompt into text, or into a JSON object validated against a schema. The
backend is the owner's choice (``VIDTHEQUE_LLM_BACKEND``). The demo keeps its own key, URL and budget
(``public/ask.py``'s ``OpenRouter``); nothing in this module reads the demo's
settings.

Every completion is recorded in `llm_calls` (companion.md §4.1): the backend
answers with its `usage`, and :class:`Meter` prices it and writes the row.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx2 as httpx
import jsonschema

from .config import LLM_PRICES, ConfigError, _env, _float_env

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Usage:
    """What one completion consumed, as the backend reported it; None is unknown.

    `prompt` includes `cached`, and `completion` includes `reasoning`, as in
    the OpenAI-compatible `usage` object. `cost_micro_usd` is a cost the
    backend reported itself (claude's `total_cost_usd`); otherwise the price
    table supplies it.
    """

    prompt: int | None = None
    completion: int | None = None
    cached: int | None = None
    reasoning: int | None = None
    cost_micro_usd: int | None = None


class LLMUnavailable(Exception):
    """The model cannot serve this request. Carries a reason, never a body.

    `usage` is set when the model did answer, but not with what was asked.
    """

    def __init__(self, reason: str, retry_after_s: int = 60, usage: Usage | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after_s = retry_after_s
        self.usage = usage


@dataclass
class Billing:
    """Did this ask already buy upstream work? One flag, per request (§4.4).

    The daily budget is refunded for an ask that cost nothing, and *cost* is
    the only honest test. "Did the visitor get an answer" is not the same
    question, and using it was a way to get paid completions for free: the
    first `activity` event is emitted only after a completion came back with a
    tool call in it, so a client that waits for that line, disconnects, and
    repeats spends the model's tokens while the ``finally`` hands the day's
    token back every time. Reported by the 2026-08-09 review.

    So the flag is set by :meth:`ChatClient.chat` **before it dispatches**,
    not after the provider answers. That ordering is the whole point, and the
    2026-08-10 audit (B-3) is why it changed: setting it afterwards left a
    window the length of a completion in which the flag was still false. A
    visitor who disconnects inside that window cancels the task; `CancelledError`
    derives from `BaseException`, so the `except Exception` below never sees it,
    the assignment never runs, and the ``finally`` refunds the day's token —
    while the provider has already generated and billed the answer. Repeat and
    the daily cap never moves.

    The honest question is not "did the provider answer" but "might this have
    been billed", and once the request is on the wire the answer is yes. So the
    flag goes up first and comes back down only where nothing can have been
    generated: a failure to connect at all, and an explicit refusal
    (401/403/429) which the provider answers without running a model. A read
    timeout, a 5xx and a cancellation all stay paid, because any of them can sit
    on top of a real generation.

    The cost of this direction is that a genuinely dead upstream can charge a
    visitor a token they did not get an answer from. That is the right way round
    for a budget that guards money: the alternative charged Tom instead.
    """

    paid: bool = False


class ChatClient:
    """The thinnest possible OpenAI-compatible chat client.

    Takes its ``httpx`` client from the caller so tests can hand it a
    ``MockTransport`` instead of the internet. ``key`` may be None for a local
    endpoint (llama.cpp) that wants no ``Authorization`` header.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        key: str | None,
        *,
        headers: dict[str, str] | None = None,
        request_cap_s: float = 60.0,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._key = key
        self._headers = headers or {}
        self._request_cap_s = request_cap_s

    async def chat(
        self, body: dict[str, Any], deadline: float, billing: Billing | None = None
    ) -> dict[str, Any]:
        """POST ``/chat/completions`` and return the parsed response."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LLMUnavailable("upstream_unavailable")
        headers = dict(self._headers)
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        # Whatever this call was worth, it was already worth it before this
        # round: a later round must never be able to hand back an earlier one.
        was_paid = billing.paid if billing is not None else False
        if billing is not None:
            billing.paid = True
        try:
            response = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=body,
                headers=headers,
                timeout=min(remaining, self._request_cap_s),
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Never reached the provider, so nothing can have been generated.
            # This is the narrow case the flag may be handed back for — it is
            # also the flapping-upstream case §4.4 exists to forgive.
            if billing is not None:
                billing.paid = was_paid
            logger.warning("llm: upstream unreachable: %s", type(exc).__name__)
            raise LLMUnavailable("upstream_unavailable") from None
        except Exception as exc:  # read timeout, TLS mid-stream, protocol error
            # The request was on the wire. It may have been generated and
            # billed, so the flag stays up. Cancellation does not land here at
            # all — CancelledError is a BaseException — which is precisely why
            # the flag had to go up before the await.
            logger.warning("llm: upstream request failed: %s", type(exc).__name__)
            raise LLMUnavailable("upstream_unavailable") from None

        if response.status_code >= 400:
            # A status line is proof the provider answered *instead of*
            # generating — a refusal (401/403/429), a rejected body (4xx), or a
            # flap (5xx). None of those ran a model, so the day's token goes
            # back. This is §4.4's original reason for existing: without it one
            # visitor retrying through an upstream flap burns the whole day for
            # everybody. It is also the line between this and a cancellation,
            # which produces no status at all and therefore stays charged.
            if billing is not None:
                billing.paid = was_paid

        if response.status_code in (401, 403):
            logger.warning("llm: upstream rejected the key (%s)", response.status_code)
            raise LLMUnavailable("upstream_rejected", retry_after_s=300)
        if response.status_code == 429:
            logger.warning("llm: upstream rate limited")
            raise LLMUnavailable("upstream_rate_limited")
        if response.status_code >= 400:
            # Status only. An upstream body is attacker-influenced text, and a
            # provider that echoes the request would copy the prepaid key
            # straight into this log line (2026-08-10 audit, F-8).
            logger.warning("llm: upstream returned %s", response.status_code)
            raise LLMUnavailable("upstream_unavailable")
        try:
            return response.json()
        except ValueError:
            logger.warning("llm: upstream returned a non-JSON body")
            raise LLMUnavailable("upstream_unavailable") from None


# ---------------------------------------------------------------------------
# The one interface


class Model(Protocol):
    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        purpose: str = "unknown",
        video_id: int | None = None,
    ) -> Any:
        """Text, or with ``schema`` a JSON object that validates against it.

        Raises :class:`LLMUnavailable`; ``invalid_output`` when the model
        answered something that is not the object asked for. ``purpose`` and
        ``video_id`` (the videos row id) label the call's `llm_calls` row.
        """
        ...


# ---------------------------------------------------------------------------
# Prices and the call log


@dataclass(frozen=True)
class Price:
    """List prices in USD per million tokens, which is micro-USD per token."""

    input: float
    output: float
    cached_input: float | None = None

    def cost_micro_usd(self, usage: Usage) -> int | None:
        if usage.prompt is None or usage.completion is None:
            return None
        cached = min(usage.cached or 0, usage.prompt)
        cached_rate = self.input if self.cached_input is None else self.cached_input
        return round(
            (usage.prompt - cached) * self.input
            + cached * cached_rate
            + usage.completion * self.output
        )


_PRICE_FIELDS = ("input", "cached_input", "output")


def parse_price(raw: str) -> Price:
    """`input=1.4,cached_input=0.14,output=4.4`; `cached_input` is optional."""
    values: dict[str, float] = {}
    for part in raw.split(","):
        key, sep, value = part.partition("=")
        key = key.strip()
        if not sep or key not in _PRICE_FIELDS or key in values:
            raise ConfigError(
                f"VIDTHEQUE_LLM_PRICE must read input=…,cached_input=…,output=…, got {raw!r}"
            )
        try:
            values[key] = float(value)
        except ValueError:
            raise ConfigError(
                f"VIDTHEQUE_LLM_PRICE: {key} is not a number, got {value!r}"
            ) from None
        if values[key] < 0:
            raise ConfigError(f"VIDTHEQUE_LLM_PRICE: {key} is negative")
    if "input" not in values or "output" not in values:
        raise ConfigError("VIDTHEQUE_LLM_PRICE needs at least input=… and output=…")
    return Price(values["input"], values["output"], values.get("cached_input"))


def price_for(settings: "LLMSettings") -> Price | None:
    """The configured model's price: the env override, else the table, else None.

    The CLI backends run on the owner's subscription; they get no table price.
    """
    if settings.price is not None:
        return settings.price
    if settings.backend != "api" or not settings.model:
        return None
    row = LLM_PRICES.get(settings.model)
    return Price(**row) if row is not None else None


class Meter:
    """Writes one `llm_calls` row per completion. A failed write never fails the call."""

    def __init__(self, db: Any, backend: str, model: str | None, price: Price | None) -> None:
        self._db = db
        self._backend = backend
        self._model = model
        self._price = price
        # Rows written after a cancellation, kept so the loop does not drop them.
        self._pending: set[asyncio.Task[None]] = set()

    def cost(self, usage: Usage) -> int | None:
        if usage.cost_micro_usd is not None:
            return usage.cost_micro_usd
        return self._price.cost_micro_usd(usage) if self._price is not None else None

    async def record(
        self,
        *,
        at: int,
        purpose: str,
        video_id: int | None,
        usage: Usage | None,
        latency_ms: int,
        outcome: str,
    ) -> None:
        usage = usage or Usage()
        row = (
            at,
            purpose,
            video_id,
            self._backend,
            self._model,
            usage.prompt,
            usage.completion,
            usage.cached,
            usage.reasoning,
            latency_ms,
            outcome,
            self.cost(usage),
        )

        def write(c: Any) -> None:
            # The video may have been deleted while the model ran.
            vid = row[2]
            if vid is not None:
                exists = c.execute("SELECT 1 FROM videos WHERE id = ?", (vid,)).fetchone()
                vid = vid if exists else None
            c.execute(
                "INSERT INTO llm_calls (at, purpose, video_id, backend, model, prompt_tokens,"
                " completion_tokens, cached_tokens, reasoning_tokens, latency_ms, outcome,"
                " cost_micro_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*row[:2], vid, *row[3:]),
            )

        try:
            await self._db.write(write)
        except Exception as exc:  # noqa: BLE001 - the log must never cost the answer
            logger.warning("llm: the call log write failed: %s", type(exc).__name__)

    def record_later(self, **fields: Any) -> None:
        """Record from a cancelled call, which can no longer await."""
        task = asyncio.get_running_loop().create_task(self.record(**fields))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)


class _Metered:
    """`complete` for every backend: time the call, then record it."""

    meter: Meter | None = None

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        purpose: str = "unknown",
        video_id: int | None = None,
    ) -> Any:
        at, started = int(time.time()), time.monotonic()
        usage: Usage | None = None
        outcome = "ok"
        try:
            answer, usage = await self._complete(prompt, system, schema)
            return answer
        except LLMUnavailable as exc:
            usage, outcome = exc.usage, exc.reason
            raise
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            if self.meter is not None:
                fields = dict(
                    at=at,
                    purpose=purpose,
                    video_id=video_id,
                    usage=usage,
                    latency_ms=round((time.monotonic() - started) * 1000),
                    outcome=outcome,
                )
                if outcome == "cancelled":
                    self.meter.record_later(**fields)
                else:
                    await self.meter.record(**fields)

    async def _complete(
        self, prompt: str, system: str | None, schema: dict[str, Any] | None
    ) -> tuple[Any, Usage]:
        raise NotImplementedError


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def api_usage(payload: Any) -> Usage | None:
    """The OpenAI-compatible `usage` object, as Mistral and OpenRouter send it."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None
    prompt_details = usage.get("prompt_tokens_details")
    completion_details = usage.get("completion_tokens_details")
    return Usage(
        prompt=_int(usage.get("prompt_tokens")),
        completion=_int(usage.get("completion_tokens")),
        cached=(
            _int(prompt_details.get("cached_tokens"))
            if isinstance(prompt_details, dict)
            else None
        ),
        reasoning=(
            _int(completion_details.get("reasoning_tokens"))
            if isinstance(completion_details, dict)
            else None
        ),
    )


_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n(.*)\n```\s*$", re.DOTALL)


def parse_json(text: str, schema: dict[str, Any]) -> dict[str, Any]:
    """The model's answer as an object that validates against ``schema``."""
    text = text.strip()
    fenced = _FENCE.match(text)
    if fenced:  # models wrap JSON in a code fence even when told not to
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except ValueError:
        logger.warning("llm: the answer is not JSON")
        raise LLMUnavailable("invalid_output") from None
    return validated(value, schema)


def validated(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        logger.warning("llm: the answer is %s, not an object", type(value).__name__)
        raise LLMUnavailable("invalid_output")
    try:
        jsonschema.validate(value, schema)
    except jsonschema.ValidationError as exc:
        logger.warning("llm: the answer fails its schema at %s", exc.json_path)
        raise LLMUnavailable("invalid_output") from None
    return value


# ---------------------------------------------------------------------------
# Backend `api`: any OpenAI-compatible endpoint


class APIModel(_Metered):
    def __init__(
        self, chat: ChatClient, model: str, timeout_s: float, reasoning_effort: str | None = None
    ) -> None:
        self._chat = chat
        self._model = model
        self._timeout_s = timeout_s
        self._reasoning_effort = reasoning_effort

    async def _complete(
        self, prompt: str, system: str | None, schema: dict[str, Any] | None
    ) -> tuple[Any, Usage]:
        messages = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {"model": self._model, "messages": messages}
        if self._reasoning_effort:
            body["reasoning_effort"] = self._reasoning_effort
        if schema is not None:
            # Not `strict`: OpenAI's strict mode rejects schemas without
            # `additionalProperties: false`, and the answer is validated here anyway.
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": schema},
            }
        payload = await self._chat.chat(body, time.monotonic() + self._timeout_s)
        usage = api_usage(payload) or Usage()
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            text = None
        if isinstance(text, list):
            # Reasoning models on Mistral's API answer in chunks; the thinking is not the answer.
            parts = [c.get("text") for c in text if isinstance(c, dict) and c.get("type") == "text"]
            text = "".join(t for t in parts if isinstance(t, str)) or None
        if not isinstance(text, str):
            logger.warning("llm: upstream answered without message content")
            raise LLMUnavailable("upstream_unavailable", usage=usage)
        return _answer(text, schema, usage), usage


def _answer(text: str, schema: dict[str, Any] | None, usage: Usage) -> Any:
    """The text, or the object parsed from it; a refusal carries what it cost."""
    if schema is None:
        return text
    try:
        return parse_json(text, schema)
    except LLMUnavailable as exc:
        exc.usage = usage
        raise


# ---------------------------------------------------------------------------
# Backends `claude-code` and `codex`: the owner's signed-in binary, headless
#
# The binary reads its own credentials; this process never opens them. It runs
# in an empty temporary directory, with no tools (claude) or a read-only
# sandbox (codex), bounded by a timeout and an output cap.

OUTPUT_CAP_BYTES = 1 << 20
_BINARIES = {"claude-code": "claude", "codex": "codex"}


class CLIModel(_Metered):
    def __init__(
        self,
        backend: str,
        model: str | None,
        timeout_s: float,
        *,
        binary: str | None = None,
    ) -> None:
        self._backend = backend
        self._binary = binary or _BINARIES[backend]
        self._model = model
        self._timeout_s = timeout_s

    async def _complete(
        self, prompt: str, system: str | None, schema: dict[str, Any] | None
    ) -> tuple[Any, Usage]:
        with tempfile.TemporaryDirectory(prefix="vidtheque-llm-") as tmp:
            if self._backend == "claude-code":
                return await self._claude(Path(tmp), prompt, system, schema)
            return await self._codex(Path(tmp), prompt, system, schema)

    async def _claude(
        self, tmp: Path, prompt: str, system: str | None, schema: dict[str, Any] | None
    ) -> tuple[Any, Usage]:
        argv = [self._binary, "-p", "--output-format", "json", "--no-session-persistence"]
        argv += ["--strict-mcp-config"]
        if self._model:
            argv += ["--model", self._model]
        if system:
            argv += ["--system-prompt", system]
        if schema is not None:
            argv += ["--json-schema", json.dumps(schema)]
        argv += ["--tools", ""]  # last: the option is variadic
        out = await _run(argv, prompt.encode(), tmp, self._timeout_s)
        try:
            result = json.loads(out)
        except ValueError:
            logger.warning("llm: claude printed something other than its JSON result")
            raise LLMUnavailable("upstream_unavailable") from None
        usage = claude_usage(result)
        if not isinstance(result, dict) or result.get("is_error"):
            logger.warning("llm: claude reported an error")
            raise LLMUnavailable("upstream_unavailable", usage=usage)
        if schema is not None and result.get("structured_output") is not None:
            try:
                return validated(result["structured_output"], schema), usage
            except LLMUnavailable as exc:
                exc.usage = usage
                raise
        text = result.get("result")
        if not isinstance(text, str):
            logger.warning("llm: claude's result has no text")
            raise LLMUnavailable("upstream_unavailable", usage=usage)
        return _answer(text, schema, usage), usage

    async def _codex(
        self, tmp: Path, prompt: str, system: str | None, schema: dict[str, Any] | None
    ) -> tuple[Any, Usage]:
        last = tmp / "last-message.txt"
        argv = [self._binary, "exec", "--sandbox", "read-only", "--skip-git-repo-check"]
        argv += ["--ephemeral", "--color", "never", "-C", str(tmp), "-o", str(last)]
        if self._model:
            argv += ["-m", self._model]
        if schema is not None:
            schema_file = tmp / "schema.json"
            schema_file.write_text(json.dumps(schema))
            argv += ["--output-schema", str(schema_file)]
        argv.append("-")  # the prompt comes on stdin
        # codex exec has no system-prompt flag; the instructions lead the prompt.
        text = f"{system}\n\n{prompt}" if system else prompt
        await _run(argv, text.encode(), tmp, self._timeout_s)
        try:
            with last.open("rb") as fh:
                answer = fh.read(OUTPUT_CAP_BYTES + 1)
        except OSError:
            logger.warning("llm: codex wrote no final message")
            raise LLMUnavailable("upstream_unavailable") from None
        if len(answer) > OUTPUT_CAP_BYTES:
            logger.warning("llm: codex's answer is over %d bytes", OUTPUT_CAP_BYTES)
            raise LLMUnavailable("invalid_output")
        decoded = answer.decode(errors="replace")
        # `codex exec -o` reports no usage: tokens and cost stay unknown.
        return _answer(decoded, schema, Usage()), Usage()


def claude_usage(result: Any) -> Usage:
    """claude's JSON result: Anthropic's `usage` and its own `total_cost_usd`."""
    if not isinstance(result, dict):
        return Usage()
    usage = result.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    parts = [
        _int(usage.get(k))
        for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    ]
    cost = result.get("total_cost_usd")
    return Usage(
        prompt=sum(p for p in parts if p is not None) if parts[0] is not None else None,
        completion=_int(usage.get("output_tokens")),
        cached=parts[2],
        cost_micro_usd=(
            round(cost * 1_000_000)
            if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0
            else None
        ),
    )


async def _run(argv: list[str], stdin: bytes, cwd: Path, timeout_s: float) -> bytes:
    """Run ``argv`` without a shell and return its stdout, or raise."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            # Its own process group, so a kill reaches the children it spawns.
            start_new_session=True,
        )
    except OSError as exc:
        logger.warning("llm: cannot start %s: %s", argv[0], type(exc).__name__)
        raise LLMUnavailable("not_configured") from None
    try:
        async with asyncio.timeout(timeout_s):
            (out, too_long), (err, _), _ = await asyncio.gather(
                _drain(proc, proc.stdout), _drain(proc, proc.stderr), _feed(proc, stdin)
            )
            code = await proc.wait()
    except TimeoutError:
        logger.warning("llm: %s ran past %ss", argv[0], timeout_s)
        raise LLMUnavailable("upstream_unavailable") from None
    finally:
        if proc.returncode is None:
            _kill(proc)
            await proc.wait()
    if too_long:
        logger.warning("llm: %s printed over %d bytes", argv[0], OUTPUT_CAP_BYTES)
        raise LLMUnavailable("invalid_output")
    if code != 0:
        tail = err[-300:].decode(errors="replace").strip()
        logger.warning("llm: %s exited %s: %s", argv[0], code, tail)
        raise LLMUnavailable("upstream_unavailable")
    return out


async def _drain(proc: asyncio.subprocess.Process, stream: Any) -> tuple[bytes, bool]:
    """Read to EOF, or kill the process once it passes the cap."""
    chunks: list[bytes] = []
    size = 0
    while chunk := await stream.read(65536):
        size += len(chunk)
        if size > OUTPUT_CAP_BYTES:
            _kill(proc)
            return b"".join(chunks), True
        chunks.append(chunk)
    return b"".join(chunks), False


async def _feed(proc: asyncio.subprocess.Process, data: bytes) -> None:
    try:
        proc.stdin.write(data)
        await proc.stdin.drain()
        proc.stdin.close()
    except (BrokenPipeError, ConnectionResetError):
        pass  # it exited without reading; its exit code says why


def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


# ---------------------------------------------------------------------------
# Selection

BACKENDS = ("api", "claude-code", "codex")


@dataclass(frozen=True)
class LLMSettings:
    backend: str = "api"
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    timeout_s: float = 300.0
    reasoning_effort: str | None = None
    price: Price | None = None  # VIDTHEQUE_LLM_PRICE, over the table in config.LLM_PRICES

    @classmethod
    def from_env(cls) -> "LLMSettings":
        backend = (_env("VIDTHEQUE_LLM_BACKEND", "api") or "api").strip().lower()
        if backend not in BACKENDS:
            raise ConfigError(
                f"VIDTHEQUE_LLM_BACKEND must be one of {', '.join(BACKENDS)}, got {backend!r}"
            )
        return cls(
            backend=backend,
            base_url=_env("VIDTHEQUE_LLM_BASE_URL"),
            api_key=_env("VIDTHEQUE_LLM_API_KEY"),
            model=_env("VIDTHEQUE_LLM_MODEL"),
            timeout_s=_float_env("VIDTHEQUE_LLM_TIMEOUT_S", 300.0),
            reasoning_effort=_env("VIDTHEQUE_LLM_REASONING_EFFORT"),
            price=parse_price(raw) if (raw := _env("VIDTHEQUE_LLM_PRICE")) else None,
        )


def is_configured(settings: LLMSettings) -> bool:
    """`api` needs a URL and a model; the CLI backends bring their own."""
    return settings.backend != "api" or bool(settings.base_url and settings.model)


def build_model(settings: LLMSettings, client: httpx.AsyncClient, db: Any) -> Model | None:
    """The configured backend, or None when `api` lacks a URL or a model.

    `db` takes every call's `llm_calls` row; it is required so that no caller
    can build a model whose calls go unrecorded.
    """
    if not is_configured(settings):
        return None
    model: APIModel | CLIModel
    if settings.backend == "api":
        chat = ChatClient(
            client, settings.base_url, settings.api_key, request_cap_s=settings.timeout_s
        )
        model = APIModel(chat, settings.model, settings.timeout_s, settings.reasoning_effort)
    else:
        model = CLIModel(settings.backend, settings.model, settings.timeout_s)
    model.meter = Meter(db, settings.backend, settings.model, price_for(settings))
    return model
