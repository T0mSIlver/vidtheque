"""The shared model client (companion.md §4): each backend's answers and failures.

Nothing here calls a model. `api` runs over ``httpx2.MockTransport``; the
`claude-code` and `codex` backends run small Python scripts standing in for
the binaries, so the real subprocess path (stdin, timeout, cap, kill) is the
one under test.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from vidtheque_mcp import llm
from vidtheque_mcp.config import ConfigError
from vidtheque_mcp.llm import APIModel, ChatClient, CLIModel, LLMSettings, LLMUnavailable

VERDICT = {
    "type": "object",
    "properties": {"watch": {"type": "boolean"}, "why": {"type": "string"}},
    "required": ["watch", "why"],
}


def _fake_bin(tmp_path: Path, body: str) -> str:
    """An executable Python script; it logs argv, cwd and stdin to $FAKE_LOG."""
    path = tmp_path / "fake-bin"
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "argv = sys.argv[1:]\n"
        "stdin = sys.stdin.read()\n"
        "with open(os.environ['FAKE_LOG'], 'w') as fh:\n"
        "    json.dump({'argv': argv, 'cwd': os.getcwd(), 'stdin': stdin}, fh)\n" + body
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@pytest.fixture
def fake_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "fake-log.json"
    monkeypatch.setenv("FAKE_LOG", str(log))
    return log


def _claude(tmp_path: Path, result: dict[str, Any]) -> CLIModel:
    body = f"print(json.dumps({result!r}))\n"
    return CLIModel("claude-code", "sonnet", 10, binary=_fake_bin(tmp_path, body))


def _claude_result(**fields: Any) -> dict[str, Any]:
    # The shape `claude -p --output-format json` prints.
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 1200,
        "num_turns": 1,
        "result": "",
        "session_id": "00000000-0000-0000-0000-000000000000",
        "total_cost_usd": 0.0,
        **fields,
    }


# ---------------------------------------------------------------- claude-code


async def test_claude_answers_from_stdin_with_no_tools_in_an_empty_dir(
    tmp_path: Path, fake_log: Path
) -> None:
    model = _claude(tmp_path, _claude_result(result="worth it"))

    assert await model.complete("the transcript", system="be terse") == "worth it"

    ran = json.loads(fake_log.read_text())
    assert ran["stdin"] == "the transcript"
    assert ran["argv"][ran["argv"].index("--system-prompt") + 1] == "be terse"
    assert ran["argv"][ran["argv"].index("--model") + 1] == "sonnet"
    assert ran["argv"][-2:] == ["--tools", ""]
    # A fresh directory, gone afterwards: the binary never runs in the repo.
    assert not Path(ran["cwd"]).exists()


async def test_claude_structured_output_is_validated(tmp_path: Path, fake_log: Path) -> None:
    verdict = {"watch": True, "why": "new benchmark"}
    model = _claude(tmp_path, _claude_result(structured_output=verdict))

    assert await model.complete("t", schema=VERDICT) == verdict
    ran = json.loads(fake_log.read_text())
    assert json.loads(ran["argv"][ran["argv"].index("--json-schema") + 1]) == VERDICT


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (_claude_result(structured_output={"watch": "yes"}), "invalid_output"),
        (_claude_result(result='```json\n{"watch": true}\n```'), "invalid_output"),
        (_claude_result(is_error=True, subtype="error_during_execution"), "upstream_unavailable"),
    ],
    ids=["schema-mismatch", "missing-field-in-text", "is-error"],
)
async def test_claude_failures(
    tmp_path: Path, fake_log: Path, result: dict[str, Any], reason: str
) -> None:
    with pytest.raises(LLMUnavailable) as exc:
        await _claude(tmp_path, result).complete("t", schema=VERDICT)
    assert exc.value.reason == reason


async def test_claude_json_in_a_fence_is_accepted(tmp_path: Path, fake_log: Path) -> None:
    text = '```json\n{"watch": false, "why": "rehash"}\n```'
    model = _claude(tmp_path, _claude_result(result=text))
    assert await model.complete("t", schema=VERDICT) == {"watch": False, "why": "rehash"}


# ---------------------------------------------------------------- the process


async def test_a_nonzero_exit_is_unavailable(tmp_path: Path, fake_log: Path) -> None:
    binary = _fake_bin(tmp_path, "sys.stderr.write('not logged in'); sys.exit(1)\n")
    with pytest.raises(LLMUnavailable) as exc:
        await CLIModel("claude-code", None, 10, binary=binary).complete("t")
    assert exc.value.reason == "upstream_unavailable"


async def test_a_missing_binary_is_not_configured(tmp_path: Path) -> None:
    model = CLIModel("codex", None, 10, binary=str(tmp_path / "no-such-codex"))
    with pytest.raises(LLMUnavailable) as exc:
        await model.complete("t")
    assert exc.value.reason == "not_configured"


async def test_output_past_the_cap_is_cut_off(
    tmp_path: Path, fake_log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm, "OUTPUT_CAP_BYTES", 1000)
    binary = _fake_bin(tmp_path, "while True: sys.stdout.write('x' * 4096)\n")
    with pytest.raises(LLMUnavailable) as exc:
        await CLIModel("claude-code", None, 10, binary=binary).complete("t")
    assert exc.value.reason == "invalid_output"


async def test_a_timeout_kills_the_process_and_its_children(
    tmp_path: Path, fake_log: Path
) -> None:
    pids = tmp_path / "pids"
    binary = _fake_bin(
        tmp_path,
        "import subprocess, time\n"
        "child = subprocess.Popen(['sleep', '60'])\n"
        f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{child.pid}}')\n"
        "time.sleep(60)\n",
    )
    started = time.monotonic()
    with pytest.raises(LLMUnavailable) as exc:
        await CLIModel("claude-code", None, 1.0, binary=binary).complete("t")
    assert exc.value.reason == "upstream_unavailable"
    assert time.monotonic() - started < 10

    for pid in map(int, pids.read_text().split()):
        for _ in range(50):
            if not _alive(pid):
                break
            await asyncio.sleep(0.05)
        assert not _alive(pid), pid


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().split(") ")[-1][0]
    except FileNotFoundError:
        return False
    return state != "Z"


# ---------------------------------------------------------------- codex


def _codex(tmp_path: Path, message: str) -> CLIModel:
    body = (
        "out = argv[argv.index('-o') + 1]\n"
        "schema = argv[argv.index('--output-schema') + 1] if '--output-schema' in argv else None\n"
        "if schema:\n"
        "    json.load(open(schema))\n"
        f"open(out, 'w').write({message!r})\n"
    )
    return CLIModel("codex", "gpt-6.1-sol", 10, binary=_fake_bin(tmp_path, body))


async def test_codex_reads_the_final_message_in_a_read_only_sandbox(
    tmp_path: Path, fake_log: Path
) -> None:
    model = _codex(tmp_path, '{"watch": true, "why": "demo"}')

    assert await model.complete("the transcript", system="judge", schema=VERDICT) == {
        "watch": True,
        "why": "demo",
    }
    ran = json.loads(fake_log.read_text())
    assert ran["argv"][0] == "exec"
    assert ran["argv"][ran["argv"].index("--sandbox") + 1] == "read-only"
    assert ran["argv"][-1] == "-"
    assert ran["stdin"] == "judge\n\nthe transcript"


async def test_codex_without_a_final_message_is_unavailable(
    tmp_path: Path, fake_log: Path
) -> None:
    binary = _fake_bin(tmp_path, "")
    with pytest.raises(LLMUnavailable) as exc:
        await CLIModel("codex", None, 10, binary=binary).complete("t")
    assert exc.value.reason == "upstream_unavailable"


async def test_codex_prose_where_json_was_asked_is_invalid(
    tmp_path: Path, fake_log: Path
) -> None:
    with pytest.raises(LLMUnavailable) as exc:
        await _codex(tmp_path, "I think you should watch it.").complete("t", schema=VERDICT)
    assert exc.value.reason == "invalid_output"


# ---------------------------------------------------------------- api


def _api(
    answer: Any, seen: list[dict[str, Any]], status: int = 200, effort: str | None = None
) -> APIModel:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "auth": request.headers.get("authorization"),
                     "body": json.loads(request.content)})
        return httpx.Response(status, json=answer)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return APIModel(ChatClient(client, "http://llm.test/v1/", "k-1"), "mistral-small", 30, effort)


def _completion(content: str | None) -> dict[str, Any]:
    return {
        "id": "cmpl-1",
        "object": "chat.completion",
        "model": "mistral-small",
        "choices": [
            {"index": 0, "finish_reason": "stop",
             "message": {"role": "assistant", "content": content}}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


async def test_api_text_answer(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []
    model = _api(_completion("worth it"), seen)

    assert await model.complete("the transcript", system="be terse") == "worth it"
    assert seen[0]["url"] == "http://llm.test/v1/chat/completions"
    assert seen[0]["auth"] == "Bearer k-1"
    assert seen[0]["body"]["model"] == "mistral-small"
    assert seen[0]["body"]["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "the transcript"},
    ]
    assert "response_format" not in seen[0]["body"]
    assert "reasoning_effort" not in seen[0]["body"]


async def test_api_structured_answer_asks_for_the_schema() -> None:
    seen: list[dict[str, Any]] = []
    model = _api(_completion('{"watch": false, "why": "rehash"}'), seen)

    assert await model.complete("t", schema=VERDICT) == {"watch": False, "why": "rehash"}
    fmt = seen[0]["body"]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["schema"] == VERDICT



async def test_api_reasoning_model_sends_the_effort_and_skips_the_thinking() -> None:
    seen: list[dict[str, Any]] = []
    answer = _completion(None)
    answer["choices"][0]["message"]["content"] = [
        {"type": "thinking", "thinking": [{"type": "text", "text": "{\"watch\": true}"}]},
        {"type": "text", "text": '{"watch": false, "why": "rehash"}'},
    ]
    model = _api(answer, seen, effort="high")

    assert await model.complete("t", schema=VERDICT) == {"watch": False, "why": "rehash"}
    assert seen[0]["body"]["reasoning_effort"] == "high"

@pytest.mark.parametrize(
    ("answer", "status", "reason"),
    [
        (_completion('{"watch": false}'), 200, "invalid_output"),
        (_completion("not json"), 200, "invalid_output"),
        (_completion('["watch"]'), 200, "invalid_output"),
        (_completion(None), 200, "upstream_unavailable"),
        ({"choices": []}, 200, "upstream_unavailable"),
        ({"error": {"message": "bad key"}}, 401, "upstream_rejected"),
    ],
    ids=["schema-mismatch", "not-json", "not-an-object", "null-content", "no-choices", "401"],
)
async def test_api_failures(answer: Any, status: int, reason: str) -> None:
    with pytest.raises(LLMUnavailable) as exc:
        await _api(answer, [], status).complete("t", schema=VERDICT)
    assert exc.value.reason == reason


# ---------------------------------------------------------------- selection


def test_backend_defaults_to_api_and_needs_a_url_and_a_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("VIDTHEQUE_LLM_BACKEND", "VIDTHEQUE_LLM_BASE_URL", "VIDTHEQUE_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    client = httpx.AsyncClient()
    assert LLMSettings.from_env().backend == "api"
    assert llm.build_model(LLMSettings.from_env(), client) is None

    monkeypatch.setenv("VIDTHEQUE_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("VIDTHEQUE_LLM_MODEL", "qwen")
    assert isinstance(llm.build_model(LLMSettings.from_env(), client), APIModel)

    monkeypatch.setenv("VIDTHEQUE_LLM_BACKEND", "Codex")
    assert isinstance(llm.build_model(LLMSettings.from_env(), client), CLIModel)


def test_an_unknown_backend_fails_at_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDTHEQUE_LLM_BACKEND", "claude")
    with pytest.raises(ConfigError):
        LLMSettings.from_env()
