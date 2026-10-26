"""终端中断分流与取消收尾，不发送真实信号或访问模型服务。"""

import asyncio
import signal
from collections.abc import AsyncIterator
from copy import deepcopy
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from openai.types.chat import ChatCompletionChunk
from rich.console import Console

from code_agent.core.agent import Agent
from code_agent.events import RunResult, TextDelta
from code_agent.tools.registry import ToolRegistry
from code_agent.ui import repl
from tests.code_agent.helpers import make_agent
from tests.code_agent.llm_helpers import make_chunk


@pytest.fixture
def install_handler(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """只替换 REPL 的信号入口，保留 pytest Runner 自己的信号处理。"""
    install = Mock(return_value=object())
    monkeypatch.setattr(repl, "signal", SimpleNamespace(SIGINT=signal.SIGINT, signal=install))
    return install


def _interrupt(install: Mock) -> None:
    install.call_args_list[0].args[1](signal.SIGINT, None)


async def test_input_interrupt_discards_line_and_restores_handler(install_handler: Mock) -> None:
    agent = Mock(spec=Agent)
    agent.run = AsyncMock()
    calls = 0

    def read_input() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            _interrupt(install_handler)
            raise AssertionError("输入中断应抛出 KeyboardInterrupt")
        return "/exit"

    result = await repl.run_session(agent, read_input=read_input, console=Console(file=StringIO()))

    assert result == 0
    assert calls == 2
    agent.run.assert_not_awaited()
    install_handler.assert_called_with(signal.SIGINT, install_handler.return_value)


async def test_repeated_interrupt_waits_for_cleanup_before_next_input(
    install_handler: Mock,
) -> None:
    reader = Mock(side_effect=["first", "next", "/exit"])
    output = StringIO()
    cleanup_done = False

    async def run(prompt: str, *, on_event: Any) -> RunResult:
        nonlocal cleanup_done
        if prompt == "next":
            assert cleanup_done

            return RunResult(status="completed", text="done")
        asyncio.get_running_loop().call_soon(_interrupt, install_handler)
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            _interrupt(install_handler)
            task = asyncio.current_task()
            assert task is not None and task.cancelling() == 1
            await asyncio.sleep(0)
            assert reader.call_count == 1
            cleanup_done = True

            raise

    agent = Mock(spec=Agent)
    agent.run = AsyncMock(side_effect=run)
    result = await repl.run_session(agent, read_input=reader, console=Console(file=output))

    assert result == 0
    assert cleanup_done
    assert agent.run.await_count == 2
    assert output.getvalue().count("运行已取消") == 1
    install_handler.assert_called_with(signal.SIGINT, install_handler.return_value)


async def test_session_cancellation_waits_for_run_then_propagates(install_handler: Mock) -> None:
    session_task = asyncio.current_task()
    assert session_task is not None
    cleanup_done = False

    async def run(prompt: str, *, on_event: Any) -> RunResult:
        nonlocal cleanup_done
        asyncio.get_running_loop().call_soon(session_task.cancel)
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0)
            cleanup_done = True

    agent = Mock(spec=Agent)
    agent.run = AsyncMock(side_effect=run)
    reader = Mock(side_effect=["first", "/exit"])
    with pytest.raises(asyncio.CancelledError):
        await repl.run_session(agent, read_input=reader, console=Console(file=StringIO()))

    assert cleanup_done
    assert reader.call_count == 1
    install_handler.assert_called_with(signal.SIGINT, install_handler.return_value)


async def test_later_run_can_be_cancelled_before_start(
    install_handler: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = 0

    def create_task(coro: Any) -> asyncio.Task[RunResult]:
        nonlocal created
        created += 1
        if created == 2:
            asyncio.get_running_loop().call_soon(_interrupt, install_handler)
        task = asyncio.create_task(coro)
        if created == 2:
            task.cancel()
        return task

    monkeypatch.setattr(
        repl,
        "asyncio",
        SimpleNamespace(
            get_running_loop=asyncio.get_running_loop,
            current_task=asyncio.current_task,
            create_task=create_task,
            CancelledError=asyncio.CancelledError,
        ),
    )
    agent = Mock(spec=Agent)
    agent.run = AsyncMock(return_value=RunResult(status="completed", text="done"))
    output = StringIO()
    result = await repl.run_session(
        agent,
        read_input=Mock(side_effect=["first", "second", "/exit"]),
        console=Console(file=output),
    )

    assert result == 0
    assert created == 2
    agent.run.assert_awaited_once()
    assert output.getvalue().count("运行已取消") == 1


async def test_cancelled_model_stream_can_resume_without_partial_history(
    install_handler: Mock, tmp_path: Path
) -> None:
    requests: list[list[dict[str, Any]]] = []

    class FakeLLM:
        async def stream_turn(
            self, messages: list[dict[str, Any]], **kwargs: Any
        ) -> AsyncIterator[ChatCompletionChunk]:
            requests.append(deepcopy(messages))
            if len(requests) == 1:
                yield make_chunk(choices=[{"delta": {"content": "UNCOMMITTED"}}])
                asyncio.get_running_loop().call_soon(_interrupt, install_handler)
                await asyncio.Future()
            else:
                yield make_chunk(
                    choices=[{"delta": {"content": "RECOVERED"}, "finish_reason": "stop"}]
                )

    agent = make_agent(FakeLLM(), ToolRegistry(), workspace_dir=tmp_path)
    output = StringIO()
    result = await repl.run_session(
        agent,
        read_input=Mock(side_effect=["first", "next", "/exit"]),
        console=Console(file=output),
    )

    assert result == 0
    assert [m["content"] for m in requests[1][1:]] == ["first", "next"]
    assert agent.history.get_pending_calls() == {}
    assert agent.history.get_model_messages()[-1]["content"] == "RECOVERED"
    assert output.getvalue().count("运行已取消") == 1
    assert output.getvalue().count("UNCOMMITTED") == 1
    assert output.getvalue().count("RECOVERED") == 1


@pytest.mark.parametrize("kind", ["unreported", "cleanup", "renderer"])
async def test_unexpected_failure_reports_diagnostic_and_ends_session(
    kind: str, install_handler: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = RuntimeError("UNEXPECTED_FAILURE")
    if kind == "renderer":
        monkeypatch.setattr(repl.EventRenderer, "render_event", Mock(side_effect=failure))

    async def run(prompt: str, *, on_event: Any) -> str:
        if kind == "renderer":
            on_event(TextDelta(text="partial"))
        if kind == "cleanup":
            asyncio.get_running_loop().call_soon(_interrupt, install_handler)
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                raise failure from None
        raise failure

    agent = Mock(spec=Agent)
    agent.run = AsyncMock(side_effect=run)
    reader = Mock(side_effect=["first", "/exit"])
    output = StringIO()
    result = await repl.run_session(
        agent, read_input=reader, console=Console(file=output, width=200)
    )

    assert result == 1
    assert reader.call_count == 1
    assert output.getvalue().count("UNEXPECTED_FAILURE") == 1
    install_handler.assert_called_with(signal.SIGINT, install_handler.return_value)
