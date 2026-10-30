"""命令行入口测试。"""

from collections.abc import AsyncIterator
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from openai import APITimeoutError
from openai.types.chat import ChatCompletionChunk
from typer.testing import CliRunner

from code_agent import __version__
from code_agent.config import Settings
from code_agent.core import agent as agent_module
from code_agent.events import AnyEvent, ToolResult, ToolStatus
from code_agent.llm import client as client_module
from code_agent.tools import builtin as builtin_module
from code_agent.ui import cli
from code_agent.ui.cli import app
from tests.code_agent.llm_helpers import make_chunk

runner = CliRunner()


async def bash_response() -> AsyncIterator[ChatCompletionChunk]:
    """模拟分两片到达的 Bash 参数，CLI 使用真实组装器与循环。"""
    yield make_chunk(
        choices=[
            {
                "delta": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_cli_1",
                            "type": "function",
                            "function": {"name": "bash", "arguments": '{"command":'},
                        }
                    ],
                },
            }
        ]
    )
    yield make_chunk(
        choices=[
            {
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"pwd"}'}}]},
                "finish_reason": "tool_calls",
            }
        ]
    )


async def text_response(content: str) -> AsyncIterator[ChatCompletionChunk]:
    """正文分片后以原生 stop 结束。"""
    yield make_chunk(choices=[{"delta": {"content": content}}])
    yield make_chunk(choices=[{"delta": {}, "finish_reason": "stop"}])


@pytest.fixture(autouse=True)
def block_model_requests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "configured-bash.exe"
    executable.touch()
    executable.chmod(0o700)
    monkeypatch.setenv("CODE_AGENT_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("CODE_AGENT_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("CODE_AGENT_BASH_PATH", str(executable))
    monkeypatch.setenv("PATHEXT", ".EXE")
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(side_effect=AssertionError("CLI 单测不得启动真实进程")),
    )


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"codeagent {__version__}"


def test_missing_prompt_exits_nonzero() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 1


def test_prompt_runs_bash_through_agent_and_displays_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI 执行工具循环,回灌观察并显示最终答案。"""
    workspace = tmp_path / "cli-test-workspace"
    workspace.mkdir()
    executable = tmp_path / "configured-bash.exe"
    settings = Settings(
        _env_file=None,
        DEEPSEEK_API_KEY="test-key",
        max_steps=4,
        workspace_dir=workspace / "child" / "..",
        bash_path=str(executable),
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
            text_response("检查完成[bold]"),
        ]
    )
    requests: list[dict[str, Any]] = []

    async def complete(**kwargs: Any) -> AsyncIterator[ChatCompletionChunk]:
        requests.append(deepcopy(kwargs))
        return next(responses)

    request = AsyncMock(side_effect=complete)
    monkeypatch.setattr(client_module.AsyncOpenAI.return_value, "request", request)

    result = runner.invoke(app, ["-p", "检查当前目录"])

    assert result.exit_code == 0, result.stdout
    assert request.await_count == 2
    assert all(item["stream"] is True for item in requests)
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
        bash_path=str(executable),
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

    async def response() -> AsyncIterator[ChatCompletionChunk]:
        yield make_chunk(choices=[{"delta": {"content": "你好"}}])
        yield make_chunk(choices=[{"delta": {"content": "[bold]"}}])
        yield make_chunk(choices=[{"delta": {}, "finish_reason": "stop"}])

    request = AsyncMock(return_value=response())
    monkeypatch.setattr(client_module.AsyncOpenAI.return_value, "request", request)

    result = runner.invoke(app, ["-p", "你好"])

    assert result.exit_code == 0
    assert result.stdout == "助手： 你好[bold]\n"
    assert "END_TASK" not in result.stdout
    assert "命令解释器：Bash" not in result.stdout
    request.assert_awaited_once()
    assert request.await_args.kwargs["stream"] is True
    assert [tool["function"]["name"] for tool in request.await_args.kwargs["tools"]] == ["bash"]


def test_prompt_preserves_old_marker_as_literal_final_text(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, DEEPSEEK_API_KEY="test-key")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    response = text_response("示例[bold]\nEND_TASK")
    request = AsyncMock(return_value=response)
    monkeypatch.setattr(client_module.AsyncOpenAI.return_value, "request", request)

    result = runner.invoke(app, ["-p", "展示标记示例"])

    assert result.exit_code == 0, result.stdout
    assert "示例[bold]\nEND_TASK" in result.stdout
    request.assert_awaited_once()


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("请求失败[bold]"),
        APITimeoutError(request=httpx.Request("POST", "https://example.invalid")),
    ],
    ids=["model-error", "request-timeout"],
)
def test_prompt_reports_model_error(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    settings = Settings(_env_file=None, DEEPSEEK_API_KEY="test-key")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    request = AsyncMock(side_effect=error)
    monkeypatch.setattr(client_module.AsyncOpenAI.return_value, "request", request)

    result = runner.invoke(app, ["-p", "你好"])

    assert result.exit_code == 1
    assert result.stdout.count(f"{type(error).__name__}: {error}") == 1
    request.assert_awaited_once()


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("断流[red]"),
        APITimeoutError(request=httpx.Request("POST", "https://example.invalid")),
    ],
    ids=["stream-error", "stream-timeout"],
)
def test_prompt_reports_stream_error_after_partial_text_once(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    settings = Settings(_env_file=None, DEEPSEEK_API_KEY="test-key")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    async def interrupted_response() -> AsyncIterator[ChatCompletionChunk]:
        yield make_chunk(choices=[{"delta": {"content": "部分正文[bold]"}}])
        raise error

    request = AsyncMock(return_value=interrupted_response())
    monkeypatch.setattr(client_module.AsyncOpenAI.return_value, "request", request)

    result = runner.invoke(app, ["-p", "你好"])

    assert result.exit_code == 1
    assert result.stdout.count("部分正文[bold]") == 1
    error_text = f"{type(error).__name__}: {error}"
    assert result.stdout.count(error_text) == 1
    assert result.stdout.index("部分正文[bold]") < result.stdout.index(error_text)
    assert "部分正文[bold]\n" in result.stdout
    request.assert_awaited_once()


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
    monkeypatch.setattr(client_module.AsyncOpenAI.return_value, "request", request)

    result = runner.invoke(app, ["-p", "你好"])

    assert result.exit_code == 1
    assert "已获得的工具观察[bold]" in result.stdout
    assert "pwd" in result.stdout
    assert "completed" in result.stdout
    error = "达到最大请求轮数" if max_steps == 1 else "RuntimeError: 连接中断[bold]"
    assert result.stdout.count(error) == 1
    assert result.stdout.index("已获得的工具观察") < result.stdout.index(error)
    assert request.await_count == max_steps
    bash.assert_awaited_once()


@pytest.mark.parametrize(
    "invalid_case, expected_field",
    [
        ("missing-workspace", "workspace_dir"),
        ("file-workspace", "workspace_dir"),
        ("blank-bash", "bash_path"),
        ("missing-bash", "bash_path"),
    ],
)
def test_prompt_reports_invalid_execution_environment_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_case: str,
    expected_field: str,
) -> None:
    workspace = tmp_path
    bash_path = str(tmp_path / "configured-bash.exe")
    if invalid_case == "missing-workspace":
        workspace = tmp_path / "missing-workspace"
    elif invalid_case == "file-workspace":
        workspace = tmp_path / "workspace-file"
        workspace.touch()
    elif invalid_case == "blank-bash":
        bash_path = " "
    else:
        bash_path = str(tmp_path / "missing-bash.exe")
    settings = Settings(
        _env_file=None,
        DEEPSEEK_API_KEY="test-key",
        workspace_dir=workspace,
        bash_path=bash_path,
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    constructor = Mock(side_effect=AssertionError("启动失败不得创建客户端"))
    monkeypatch.setattr(agent_module, "LLMClient", constructor)
    request = AsyncMock()
    monkeypatch.setattr(client_module.AsyncOpenAI.return_value, "request", request)
    handler = AsyncMock()
    monkeypatch.setattr(builtin_module, "run_bash", handler)
    create_process = AsyncMock()
    monkeypatch.setattr("asyncio.create_subprocess_exec", create_process)
    events: list[AnyEvent] = []
    original_render = cli.EventRenderer.render_event

    def render(self: cli.EventRenderer, event: AnyEvent) -> None:
        events.append(event)
        original_render(self, event)

    monkeypatch.setattr(cli.EventRenderer, "render_event", render)

    result = runner.invoke(app, ["-p", "检查当前目录"])

    assert result.exit_code == 1
    assert result.stdout.count("执行环境配置错误") == 1
    assert result.stdout.count(expected_field) == 1
    assert events == []
    constructor.assert_not_called()
    request.assert_not_awaited()
    handler.assert_not_awaited()
    create_process.assert_not_awaited()
