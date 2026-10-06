"""命令行入口的单测(H)。"""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from litellm.types.utils import ModelResponseStream
from typer.testing import CliRunner

from code_agent import __version__
from code_agent.config import Settings
from code_agent.llm import client as client_module
from code_agent.ui import cli
from code_agent.ui.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def block_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("acompletion", "aresponses"):
        monkeypatch.setattr(
            client_module.litellm,
            name,
            AsyncMock(side_effect=AssertionError("测试不得访问真实模型")),
        )


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_missing_prompt_exits_nonzero() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 1


def test_prompt_streams_chat_completion_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def events() -> AsyncIterator[ModelResponseStream]:
        yield ModelResponseStream(choices=[{"delta": {"role": "assistant", "content": ""}}])
        yield ModelResponseStream(choices=[{"delta": {"content": "你好"}}])
        yield ModelResponseStream(
            choices=[
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": '{"path":'},
                            }
                        ]
                    }
                }
            ]
        )
        yield ModelResponseStream(choices=[{"delta": {"content": "[bold]"}}])
        yield ModelResponseStream(choices=[{"delta": {}, "finish_reason": "stop"}])
        yield ModelResponseStream(
            choices=[], usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        )

    settings = Settings(_env_file=None, DEEPSEEK_API_KEY="test-key")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    request = AsyncMock(return_value=events())
    monkeypatch.setattr(client_module.litellm, "acompletion", request)

    result = runner.invoke(app, ["-p", "你好"])

    assert result.exit_code == 0
    assert result.stdout == "你好[bold]\n"
    assert request.await_args.kwargs["messages"] == [{"role": "user", "content": "你好"}]
    assert request.await_args.kwargs["stream"] is True


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


def test_prompt_preserves_text_before_stream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def events() -> AsyncIterator[ModelResponseStream]:
        yield ModelResponseStream(choices=[{"delta": {"content": "已收到的文本"}}])
        raise RuntimeError("连接中断")

    settings = Settings(_env_file=None, DEEPSEEK_API_KEY="test-key")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(client_module.litellm, "acompletion", AsyncMock(return_value=events()))

    result = runner.invoke(app, ["-p", "你好"])

    assert result.exit_code == 1
    assert "已收到的文本" in result.stdout
    assert "调用失败" in result.stdout
    assert "连接中断" in result.stdout
