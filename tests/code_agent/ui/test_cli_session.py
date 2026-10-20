"""M4a 入口分流与会话创建契约；既有 -p 测试继续保留。"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from typer.testing import CliRunner

from code_agent.config import Settings
from code_agent.core.agent import Agent
from code_agent.ui import cli, repl

runner = CliRunner()


@pytest.fixture
def entrypoints(monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, AsyncMock, AsyncMock]:
    settings = Settings(_env_file=None, DEEPSEEK_API_KEY="test-key")
    load = Mock(return_value=settings)
    once = AsyncMock(return_value=0)
    interactive = AsyncMock(return_value=0)
    monkeypatch.setattr(cli, "load_settings", load)
    monkeypatch.setattr(cli, "_run_once", once)
    monkeypatch.setattr(cli, "_run_interactive", interactive)
    monkeypatch.setattr(
        cli, "create_agent", Mock(side_effect=AssertionError("分流测试不得创建真实 Agent"))
    )
    return load, once, interactive


def test_terminal_without_prompt_starts_session(
    monkeypatch: pytest.MonkeyPatch, entrypoints: tuple[Mock, AsyncMock, AsyncMock]
) -> None:
    load, once, interactive = entrypoints
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)

    result = runner.invoke(cli.app, [])

    assert result.exit_code == 0, result.stdout
    load.assert_called_once_with()
    interactive.assert_awaited_once_with(load.return_value)
    once.assert_not_awaited()


def test_nonterminal_without_prompt_rejects_before_loading_settings(
    monkeypatch: pytest.MonkeyPatch, entrypoints: tuple[Mock, AsyncMock, AsyncMock]
) -> None:
    load, once, interactive = entrypoints
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: False)

    result = runner.invoke(cli.app, [], input="不应被读取的任务\n")

    assert result.exit_code == 1
    assert 'codeagent -p "你的任务"' in result.stdout
    load.assert_not_called()
    once.assert_not_awaited()
    interactive.assert_not_awaited()


@pytest.mark.parametrize("is_terminal", [False, True])
def test_prompt_keeps_single_run_and_preserves_text(
    monkeypatch: pytest.MonkeyPatch,
    entrypoints: tuple[Mock, AsyncMock, AsyncMock],
    is_terminal: bool,
) -> None:
    load, once, interactive = entrypoints
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: is_terminal)

    result = runner.invoke(cli.app, ["-p", "  原样任务  "])

    assert result.exit_code == 0, result.stdout
    once.assert_awaited_once_with("  原样任务  ", load.return_value)
    interactive.assert_not_awaited()


@pytest.mark.parametrize("prompt", ["", "   "])
def test_explicit_blank_prompt_does_not_start_session(
    monkeypatch: pytest.MonkeyPatch,
    entrypoints: tuple[Mock, AsyncMock, AsyncMock],
    prompt: str,
) -> None:
    load, once, interactive = entrypoints
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)

    result = runner.invoke(cli.app, ["-p", prompt])

    assert result.exit_code == 1
    assert result.stdout.strip()
    load.assert_not_called()
    once.assert_not_awaited()
    interactive.assert_not_awaited()


async def test_interactive_entry_creates_one_agent_and_passes_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, DEEPSEEK_API_KEY="test-key")
    agent = Mock(spec=Agent)
    create = Mock(return_value=agent)
    session = AsyncMock(return_value=0)
    monkeypatch.setattr(cli, "create_agent", create)
    monkeypatch.setattr(repl, "run_session", session)

    assert await cli._run_interactive(settings) == 0

    create.assert_called_once_with(settings)
    session.assert_awaited_once_with(agent, console=cli.console)


@pytest.mark.parametrize("interactive", [False, True])
def test_ctrl_c_at_cli_boundary_returns_130(
    monkeypatch: pytest.MonkeyPatch,
    entrypoints: tuple[Mock, AsyncMock, AsyncMock],
    interactive: bool,
) -> None:
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: interactive)

    # 不向 pytest 发信号；真实键盘实验独立进行。
    def interrupted(coro: object) -> int:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "run", interrupted)
    result = runner.invoke(cli.app, [] if interactive else ["-p", "stream"])

    assert result.exit_code == 130
    assert result.stdout.count("运行已取消") == 1


def test_interactive_creation_error_is_visible_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, DEEPSEEK_API_KEY="test-key")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    create = Mock(side_effect=ValueError("workspace_dir 无效"))
    session = AsyncMock()
    monkeypatch.setattr(cli, "create_agent", create)
    monkeypatch.setattr(repl, "run_session", session)

    result = runner.invoke(cli.app, [])

    assert result.exit_code == 1
    assert result.stdout.count("执行环境配置错误") == 1
    assert result.stdout.count("workspace_dir 无效") == 1
    create.assert_called_once_with(settings)
    session.assert_not_awaited()
