"""命令行入口的单测(H)。"""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from code_agent import __version__
from code_agent.config import Settings
from code_agent.llm import client as client_module
from code_agent.ui import cli
from code_agent.ui.cli import app

runner = CliRunner()


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_missing_prompt_exits_nonzero() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 1


def test_prompt_streams_responses_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def events() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(type="response.created")
        yield SimpleNamespace(type="response.output_text.delta", delta="你好")
        yield SimpleNamespace(type="response.output_text.delta", delta="[bold]")
        yield SimpleNamespace(type="response.completed")

    settings = Settings(_env_file=None, DEEPSEEK_API_KEY="test-key")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    request = AsyncMock(return_value=events())
    monkeypatch.setattr(client_module.litellm, "aresponses", request)

    result = runner.invoke(app, ["-p", "你好"])

    assert result.exit_code == 0
    assert "你好[bold]" in result.stdout
    assert request.await_args.kwargs["input"] == [{"role": "user", "content": "你好"}]


def test_prompt_reports_model_error(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, DEEPSEEK_API_KEY="test-key")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(
        client_module.litellm, "aresponses", AsyncMock(side_effect=RuntimeError("请求失败"))
    )

    result = runner.invoke(app, ["-p", "你好"])

    assert result.exit_code == 1
    assert "调用失败" in result.stdout
    assert "请求失败" in result.stdout
