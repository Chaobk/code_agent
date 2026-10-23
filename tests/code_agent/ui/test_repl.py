"""基础多轮会话契约：假输入、假模型，不访问服务或启动进程。"""

from collections.abc import AsyncIterator
from copy import deepcopy
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.utils import ModelResponseStream
from rich.console import Console

from code_agent.core.agent import Agent
from code_agent.events import RunResult, TextDelta
from code_agent.tools.registry import ToolRegistry
from code_agent.ui import repl
from tests.code_agent.helpers import make_agent


@pytest.mark.parametrize("inputs", [["/exit"], ["  /exit  "], [EOFError()]])
async def test_exit_does_not_run_agent(inputs: list[Any]) -> None:
    agent = Mock(spec=Agent)
    agent.run = AsyncMock()
    read_input = Mock(side_effect=inputs)

    result = await repl.run_session(agent, read_input=read_input, console=Console(file=StringIO()))

    assert result == 0
    agent.run.assert_not_awaited()
    read_input.assert_called_once_with()


async def test_blank_input_is_skipped_and_valid_input_is_preserved() -> None:
    agent = Mock(spec=Agent)
    agent.run = AsyncMock(return_value=RunResult(status="completed", text="完成"))
    read_input = Mock(side_effect=["", " \t ", "  保留输入空白  ", EOFError()])

    result = await repl.run_session(agent, read_input=read_input, console=Console(file=StringIO()))

    assert result == 0
    agent.run.assert_awaited_once()
    assert agent.run.await_args.args == ("  保留输入空白  ",)
    assert read_input.call_count == 4


async def test_events_render_once_to_supplied_console() -> None:
    output = StringIO()
    agent = Mock(spec=Agent)

    async def run(prompt: str, *, on_event: Any) -> str:
        on_event(TextDelta(text="回复[bold]"))

        return RunResult(status="completed", text="回复[bold]")

    agent.run = AsyncMock(side_effect=run)

    result = await repl.run_session(
        agent,
        read_input=Mock(side_effect=["提问", "/exit"]),
        console=Console(file=output),
    )

    assert result == 0
    assert output.getvalue().count("回复[bold]") == 1
    assert output.getvalue().endswith("\n")


async def test_default_input_is_resolved_when_session_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = Mock(spec=Agent)
    agent.run = AsyncMock(return_value=RunResult(status="completed", text="完成"))
    read_input = Mock(side_effect=["默认输入", "/exit"])
    monkeypatch.setattr("builtins.input", read_input)

    assert await repl.run_session(agent, console=Console(file=StringIO())) == 0
    assert agent.run.await_args.args == ("默认输入",)
    assert read_input.call_count == 2


class FakeLLM:
    """每次运行先取得工具观察再回答，保存每次模型输入的快照。"""

    def __init__(self) -> None:
        self.requests: list[list[dict[str, Any]]] = []

    async def stream_turn(
        self, messages: list[dict[str, Any]], **kwargs: Any
    ) -> AsyncIterator[ModelResponseStream]:
        self.requests.append(deepcopy(messages))
        request_number = len(self.requests)
        if request_number in (1, 3):
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"call_{request_number}",
                        "type": "function",
                        "function": {"name": "unregistered", "arguments": "{}"},
                    }
                ]
            }
            finish_reason = "tool_calls"
        elif request_number in (2, 4):
            delta = {"content": f"第{request_number // 2}次完成"}
            finish_reason = "stop"
        else:
            raise AssertionError("会话产生了额外模型请求")
        yield ModelResponseStream(choices=[{"delta": delta, "finish_reason": finish_reason}])


async def test_two_runs_reuse_history_and_each_receive_full_step_budget(tmp_path: Path) -> None:
    client = FakeLLM()
    agent = make_agent(client, ToolRegistry(), max_steps=2, workspace_dir=tmp_path)
    original_history = agent.history
    original_registry = agent.registry
    output = StringIO()

    result = await repl.run_session(
        agent,
        read_input=Mock(side_effect=["第一次任务", "第二次任务", "/exit"]),
        console=Console(file=output),
    )

    assert result == 0
    assert len(client.requests) == 4
    second_run = client.requests[2]
    assert [message["role"] for message in second_run] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assert second_run[1]["content"] == "第一次任务"
    assert second_run[3]["tool_call_id"] == "call_1"
    assert second_run[4]["content"] == "第1次完成"
    assert second_run[5]["content"] == "第二次任务"
    assert all(sum(message["role"] == "system" for message in req) == 1 for req in client.requests)
    assert agent.history is original_history
    assert agent.registry is original_registry
    assert agent.workspace_dir == tmp_path.resolve()
    assert agent.history.get_pending_calls() == {}
    assert agent.history.get_model_messages()[-1]["content"] == "第2次完成"
    assert output.getvalue().count("第1次完成") == 1
    assert output.getvalue().count("第2次完成") == 1
