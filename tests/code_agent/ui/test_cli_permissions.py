"""CLI 入口的确认能力接线，不访问真实模型或启动 Bash。"""

import asyncio
import signal
from collections.abc import AsyncIterator
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from openai.types.chat import ChatCompletionChunk
from rich.console import Console

from code_agent.approval import prompt as prompt_module
from code_agent.config import Settings
from code_agent.core import agent as agent_module
from code_agent.events import ToolResult, ToolStatus
from code_agent.safety.policy import ExecutionEnvironment
from code_agent.tools import builtin as builtin_module
from code_agent.ui import cli
from tests.code_agent.core.test_agent import FakeLLM, final_response, tool_call, tool_response


@pytest.fixture
def scripted_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Settings, FakeLLM, AsyncMock]:
    settings = Settings(
        _env_file=None,
        DEEPSEEK_API_KEY="test-key",
        workspace_dir=tmp_path,
        bash_path="unused-bash",
    )
    client = FakeLLM(
        [
            tool_response([tool_call(name="bash", arguments='{"command":"echo hello"}')]),
            final_response("done"),
        ]
    )
    handler = AsyncMock(return_value=ToolResult(output="hello", status=ToolStatus.COMPLETED))
    monkeypatch.setattr(agent_module, "LLMClient", Mock(return_value=client))
    monkeypatch.setattr(
        agent_module,
        "resolve_execution_environment",
        lambda **kwargs: ExecutionEnvironment(tmp_path, "unused-bash"),
    )
    monkeypatch.setattr(builtin_module, "run_bash", handler)
    monkeypatch.setattr(cli, "console", Console(file=StringIO()))
    return settings, client, handler


@pytest.mark.parametrize("terminal", [True, False])
async def test_single_run_never_prompts_or_automatically_approves_an_ask(
    scripted_cli: tuple[Settings, FakeLLM, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
    terminal: bool,
) -> None:
    settings, client, handler = scripted_cli
    monkeypatch.setattr(prompt_module.sys, "stdin", SimpleNamespace(isatty=lambda: terminal))
    reader = Mock(side_effect=AssertionError("-p 不得读取确认输入"))
    monkeypatch.setattr("builtins.input", reader)

    assert await cli._run_once("run bash", settings) == 0

    handler.assert_not_awaited()
    reader.assert_not_called()
    observation = ToolResult.model_validate_json(client.requests[1]["messages"][-1]["content"])
    assert observation.status == ToolStatus.ERROR
    assert observation.output["execution"] == "not_started"
    assert "不能进行交互确认" in observation.output["error"]


@pytest.mark.parametrize("choice", ["o", "r"])
async def test_interactive_entry_uses_actual_confirmation_and_replays_its_result(
    scripted_cli: tuple[Settings, FakeLLM, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
    choice: str,
) -> None:
    settings, client, handler = scripted_cli
    monkeypatch.setattr(prompt_module.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    reader = Mock(side_effect=["run bash", choice, "/exit"])
    monkeypatch.setattr("builtins.input", reader)

    assert await cli._run_interactive(settings) == 0

    assert reader.call_count == 3
    observation = ToolResult.model_validate_json(client.requests[1]["messages"][-1]["content"])
    if choice == "o":
        handler.assert_awaited_once_with(
            command="echo hello",
            timeout_seconds=30,
            workspace_dir=settings.workspace_dir,
            bash_path="unused-bash",
        )
        assert observation.output == "hello"
    else:
        handler.assert_not_awaited()
        assert observation.output["execution"] == "not_started"
        assert "用户拒绝" in observation.output["error"]


async def test_approval_sigint_denies_without_cancelling_run_and_restores_repl_input(
    scripted_cli: tuple[Settings, FakeLLM, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """调用当前已注册的信号处理器，验证分流；不模拟真实终端按键。"""
    settings, client, handler = scripted_cli
    monkeypatch.setattr(prompt_module.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    original_stream = client.stream_turn

    async def yielding_stream(
        *args: object, **kwargs: object
    ) -> AsyncIterator[ChatCompletionChunk]:
        # 下一次请求真正让出事件循环，防止待处理的 Task.cancel 被同步假模型掩盖。
        await asyncio.sleep(0)
        async for chunk in original_stream(*args, **kwargs):
            yield chunk

    monkeypatch.setattr(client, "stream_turn", yielding_stream)
    repl_handler = None
    calls = 0

    def read_input(prompt: str) -> str:
        nonlocal repl_handler, calls
        calls += 1
        current_handler = signal.getsignal(signal.SIGINT)
        if calls == 1:
            repl_handler = current_handler
            return "run bash"
        if calls == 2:
            assert prompt == "> "
            assert current_handler is signal.default_int_handler
        elif calls == 3:
            assert prompt == "你 > "
            assert current_handler is repl_handler
        else:
            assert calls == 4
            return "/exit"
        assert callable(current_handler)
        current_handler(signal.SIGINT, None)
        raise AssertionError("审批和普通输入的 SIGINT 应抛出 KeyboardInterrupt")

    monkeypatch.setattr("builtins.input", read_input)
    previous_handler = signal.getsignal(signal.SIGINT)

    assert await cli._run_interactive(settings) == 0

    assert signal.getsignal(signal.SIGINT) is previous_handler
    assert calls == 4
    handler.assert_not_awaited()
    assert len(client.requests) == 2
    observation = ToolResult.model_validate_json(client.requests[1]["messages"][-1]["content"])
    assert observation.output["reason"] == "permission_denied"
    assert observation.output["execution"] == "not_started"
    output = cli.console.file.getvalue()
    assert "运行已取消" not in output
    assert "done" in output
