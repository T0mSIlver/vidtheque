"""The model client every LLM caller shares — `docs/design/companion.md` §4.

One OpenAI-compatible chat client, moved here from the demo's ask so the
companion can reuse it, and one interface over it: :class:`Model` completes a
prompt into text, or into a JSON object validated against a schema. The
backend is the owner's choice (``VIDTHEQUE_LLM_BACKEND``). The demo keeps its own key, URL and budget
(``public/ask.py``'s ``OpenRouter``); nothing in this module reads the demo's
settings.
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

from .config import ConfigError, _env, _float_env

logger = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    """The model cannot serve this request. Carries a reason, never a body."""

    def __init__(self, reason: str, retry_after_s: int = 60) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after_s = retry_after_s


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
        self, prompt: str, *, system: str | None = None, schema: dict[str, Any] | None = None
    ) -> Any:
        """Text, or with ``schema`` a JSON object that validates against it.

        Raises :class:`LLMUnavailable`; ``invalid_output`` when the model
        answered something that is not the object asked for.
        """
        ...


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


class APIModel:
    def __init__(self, chat: ChatClient, model: str, timeout_s: float) -> None:
        self._chat = chat
        self._model = model
        self._timeout_s = timeout_s

    async def complete(
        self, prompt: str, *, system: str | None = None, schema: dict[str, Any] | None = None
    ) -> Any:
        messages = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {"model": self._model, "messages": messages}
        if schema is not None:
            # Not `strict`: OpenAI's strict mode rejects schemas without
            # `additionalProperties: false`, and the answer is validated here anyway.
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": schema},
            }
        payload = await self._chat.chat(body, time.monotonic() + self._timeout_s)
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            text = None
        if not isinstance(text, str):
            logger.warning("llm: upstream answered without message content")
            raise LLMUnavailable("upstream_unavailable")
        return text if schema is None else parse_json(text, schema)


# ---------------------------------------------------------------------------
# Backends `claude-code` and `codex`: the owner's signed-in binary, headless
#
# The binary reads its own credentials; this process never opens them. It runs
# in an empty temporary directory, with no tools (claude) or a read-only
# sandbox (codex), bounded by a timeout and an output cap.

OUTPUT_CAP_BYTES = 1 << 20
_BINARIES = {"claude-code": "claude", "codex": "codex"}


class CLIModel:
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

    async def complete(
        self, prompt: str, *, system: str | None = None, schema: dict[str, Any] | None = None
    ) -> Any:
        with tempfile.TemporaryDirectory(prefix="vidtheque-llm-") as tmp:
            if self._backend == "claude-code":
                return await self._claude(Path(tmp), prompt, system, schema)
            return await self._codex(Path(tmp), prompt, system, schema)

    async def _claude(
        self, tmp: Path, prompt: str, system: str | None, schema: dict[str, Any] | None
    ) -> Any:
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
        if not isinstance(result, dict) or result.get("is_error"):
            logger.warning("llm: claude reported an error")
            raise LLMUnavailable("upstream_unavailable")
        if schema is not None and result.get("structured_output") is not None:
            return validated(result["structured_output"], schema)
        text = result.get("result")
        if not isinstance(text, str):
            logger.warning("llm: claude's result has no text")
            raise LLMUnavailable("upstream_unavailable")
        return text if schema is None else parse_json(text, schema)

    async def _codex(
        self, tmp: Path, prompt: str, system: str | None, schema: dict[str, Any] | None
    ) -> Any:
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
        return decoded if schema is None else parse_json(decoded, schema)


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
        )


def build_model(settings: LLMSettings, client: httpx.AsyncClient) -> Model | None:
    """The configured backend, or None when `api` lacks a URL or a model."""
    if settings.backend == "api":
        if not (settings.base_url and settings.model):
            return None
        chat = ChatClient(
            client, settings.base_url, settings.api_key, request_cap_s=settings.timeout_s
        )
        return APIModel(chat, settings.model, settings.timeout_s)
    return CLIModel(settings.backend, settings.model, settings.timeout_s)
