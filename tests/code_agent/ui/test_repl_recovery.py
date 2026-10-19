"""失败与轮数耗尽后的会话续用，不访问模型或启动 Bash。"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from copy import deepcopy
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.utils import ModelResponseStream
from rich.console import Console

from code_agent.core.agent import Agent
from code_agent.tools.registry import ToolRegistry
from code_agent.ui import repl


def _chunk(
    *, content: str | None = None, tool_id: str | None = None, finish_reason: str | None = None
) -> ModelResponseStream:
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    if tool_id is not None:
        delta["tool_calls"] = [
            {
                "index": 0,
                "id": tool_id,
                "type": "function",
                "function": {"name": "unregistered", "arguments": "{}"},
            }
        ]
    return ModelResponseStream(choices=[{"delta": delta, "finish_reason": finish_reason}])


class ScriptedLLM:
    """按脚本产出分片或异常，保留每次模型输入的独立快照。"""

    def __init__(self, turns: Sequence[Sequence[ModelResponseStream | BaseException]]) -> None:
        self.turns = iter(turns)
        self.requests: list[list[dict[str, Any]]] = []

    async def stream_turn(
        self, messages: list[dict[str, Any]], **kwargs: Any
    ) -> AsyncIterator[ModelResponseStream]:
        self.requests.append(deepcopy(messages))
        for item in next(self.turns):
            if isinstance(item, BaseException):
                raise item
            yield item


@pytest.mark.parametrize("partial", [False, True], ids=["request-error", "partial-stream-error"])
async def test_failure_then_next_input_preserves_only_committed_history(
    tmp_path: Path, partial: bool
) -> None:
    broken: list[ModelResponseStream | BaseException] = (
        [_chunk(content="UNCOMMITTED")] if partial else []
    )
    broken.append(RuntimeError("STREAM_FAILED"))
    client = ScriptedLLM(
        [
            [_chunk(content="PREVIOUS", finish_reason="stop")],
            broken,
            [_chunk(content="RECOVERED", finish_reason="stop")],
        ]
    )
    agent = Agent(client, ToolRegistry(), max_steps=2, workspace_dir=tmp_path)
    history = agent.history
    output = StringIO()
    result = await repl.run_session(
        agent,
        read_input=Mock(side_effect=["initial", "broken", "next", "/exit"]),
        console=Console(file=output, width=200),
    )
    assert result == 0
    assert agent.history is history
    assert len(client.requests) == 3
    assert [m["content"] for m in client.requests[-1][1:]] == [
        "initial",
        "PREVIOUS",
        "broken",
        "next",
    ]
    assert agent.history.get_pending_calls() == {}
    assert all(m.get("content") != "UNCOMMITTED" for m in agent.history.get_model_messages())
    assert output.getvalue().count("STREAM_FAILED") == 1
    assert output.getvalue().count("RECOVERED") == 1
    assert output.getvalue().count("UNCOMMITTED") == int(partial)


async def test_exhausted_run_then_next_input_receives_full_step_budget(tmp_path: Path) -> None:
    client = ScriptedLLM(
        [
            [_chunk(tool_id="first", finish_reason="tool_calls")],
            [_chunk(tool_id="second", finish_reason="tool_calls")],
            [_chunk(tool_id="third", finish_reason="tool_calls")],
            [_chunk(content="RECOVERED", finish_reason="stop")],
        ]
    )
    agent = Agent(client, ToolRegistry(), max_steps=2, workspace_dir=tmp_path)
    output = StringIO()
    result = await repl.run_session(
        agent,
        read_input=Mock(side_effect=["exhaust", "next", "/exit"]),
        console=Console(file=output, width=200),
    )
    assert result == 0
    assert len(client.requests) == 4
    messages = client.requests[2]
    assert [m["role"] for m in messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "user",
    ]
    assert [m["tool_call_id"] for m in messages if m["role"] == "tool"] == ["first", "second"]
    assert messages[-1]["content"] == "next"
    assert sum(m["role"] == "system" for m in messages) == 1
    assert agent.history.get_pending_calls() == {}
    assert agent.history.get_model_messages()[-1]["content"] == "RECOVERED"
    assert output.getvalue().count("达到最大请求轮数") == 1
    assert output.getvalue().count("RECOVERED") == 1


async def test_external_cancellation_is_not_swallowed() -> None:
    agent = Mock(spec=Agent)
    agent.run = AsyncMock(side_effect=asyncio.CancelledError())
    reader = Mock(side_effect=["cancel", "/exit"])
    with pytest.raises(asyncio.CancelledError):
        await repl.run_session(agent, read_input=reader, console=Console(file=StringIO()))
    assert reader.call_count == 1
