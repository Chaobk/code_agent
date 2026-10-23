"""CLI 入口的确认能力接线，不访问真实模型或启动 Bash。"""

from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
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
