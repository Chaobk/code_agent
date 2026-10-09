"""命令行入口的单测(H)。"""

from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from litellm.types.utils import ModelResponse
from typer.testing import CliRunner

from code_agent import __version__
from code_agent.config import Settings
from code_agent.events import ToolResult, ToolStatus
from code_agent.llm import client as client_module
from code_agent.tools import builtin as builtin_module
from code_agent.ui import cli
from code_agent.ui.cli import app

runner = CliRunner()


def bash_response() -> ModelResponse:
    """模拟一次完整的 Bash 工具调用响应。"""
    return ModelResponse(
        choices=[
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_cli_1",
                            "type": "function",
                            "function": {"name": "bash", "arguments": '{"command":"pwd"}'},
                        }
                    ],
                },
            }
        ]
    )


@pytest.fixture(autouse=True)
def block_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("acompletion", "aresponses"):
        monkeypatch.setattr(
            client_module.litellm,
            name,
            AsyncMock(side_effect=AssertionError("测试不得访问真实模型")),
        )
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(side_effect=AssertionError("CLI 单测不得启动真实进程")),
    )


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_missing_prompt_exits_nonzero() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 1


def test_prompt_runs_bash_through_agent_and_displays_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI 执行工具循环,回灌观察并显示最终答案。"""
    workspace = Path.cwd() / "cli-test-workspace"
    settings = Settings(
        _env_file=None,
        DEEPSEEK_API_KEY="test-key",
        max_steps=4,
        workspace_dir=workspace / "child" / "..",
        bash_path="configured-bash",
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    observation = ToolResult(
        output={"stdout": "[bold]观察内容[/bold]\n", "stderr": "", "exit_code": 0},
        status=ToolStatus.COMPLETED,
    )
    bash = AsyncMock(return_value=observation)
    monkeypatch.setattr(builtin_module, "run_bash", bash)
    responses = iter(
        [
            bash_response(),
            ModelResponse(
                choices=[
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "检查完成[bold]\nEND_TASK"},
                    }
                ]
            ),
        ]
    )
    requests: list[dict[str, Any]] = []

    async def complete(**kwargs: Any) -> ModelResponse:
        requests.append(deepcopy(kwargs))
        return next(responses)

    request = AsyncMock(side_effect=complete)
    monkeypatch.setattr(client_module.litellm, "acompletion", request)

    result = runner.invoke(app, ["-p", "检查当前目录"])

    assert result.exit_code == 0, result.stdout
    assert request.await_count == 2
    assert all(item["stream"] is False for item in requests)
    assert [tool["function"]["name"] for tool in requests[0]["tools"]] == ["bash"]
    assert requests[0]["messages"][-1] == {"role": "user", "content": "检查当前目录"}
    assert requests[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call_cli_1",
        "content": observation.model_dump_json(),
    }
    bash.assert_awaited_once_with(
        command="pwd",
        timeout_seconds=30,
        workspace_dir=workspace.resolve(),
        bash_path="configured-bash",
    )
    assert "bash" in result.stdout
    assert "pwd" in result.stdout
    assert "completed" in result.stdout
    assert "exit_code" in result.stdout
    assert "[bold]观察内容[/bold]" in result.stdout
    assert result.stdout.count("检查完成[bold]") == 1
    assert "END_TASK" not in result.stdout
    assert "命令解释器：Bash" not in result.stdout


def test_prompt_displays_final_answer_without_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, DEEPSEEK_API_KEY="test-key")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    response = ModelResponse(
        choices=[
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "你好[bold]\nEND_TASK"},
            }
        ]
    )
    request = AsyncMock(return_value=response)
    monkeypatch.setattr(client_module.litellm, "acompletion", request)

    result = runner.invoke(app, ["-p", "你好"])

    assert result.exit_code == 0
    assert result.stdout.count("你好[bold]") == 1
    assert "END_TASK" not in result.stdout
    assert "命令解释器：Bash" not in result.stdout
    request.assert_awaited_once()
    assert request.await_args.kwargs["stream"] is False
    assert [tool["function"]["name"] for tool in request.await_args.kwargs["tools"]] == ["bash"]


def test_prompt_reports_model_error(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, DEEPSEEK_API_KEY="test-key")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(
        client_module.litellm, "acompletion", AsyncMock(side_effect=RuntimeError("请求失败"))
    )

    result = runner.invoke(app, ["-p", "你好"])

    assert result.exit_code == 1
    assert "调用失败" in result.stdout
    assert "请求失败" in result.stdout


@pytest.mark.parametrize("max_steps", [1, 2], ids=["step-limit", "model-error"])
def test_prompt_preserves_tool_history_when_run_fails(
    max_steps: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(_env_file=None, DEEPSEEK_API_KEY="test-key", max_steps=max_steps)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    observation = ToolResult(
        output={"stdout": "已获得的工具观察[bold]", "stderr": "", "exit_code": 0},
        status=ToolStatus.COMPLETED,
    )
    bash = AsyncMock(return_value=observation)
    monkeypatch.setattr(builtin_module, "run_bash", bash)
    request = AsyncMock(side_effect=[bash_response(), RuntimeError("连接中断[bold]")])
    monkeypatch.setattr(client_module.litellm, "acompletion", request)

    result = runner.invoke(app, ["-p", "你好"])

    assert result.exit_code == 1
    assert "已获得的工具观察[bold]" in result.stdout
    assert "pwd" in result.stdout
    assert "completed" in result.stdout
    assert "调用失败" in result.stdout
    error = "达到最大请求轮数：1" if max_steps == 1 else "连接中断[bold]"
    assert error in result.stdout
    assert result.stdout.index("已获得的工具观察") < result.stdout.index("调用失败")
    assert request.await_count == max_steps
    bash.assert_awaited_once()
