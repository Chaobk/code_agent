"""Agent 编排单测：使用 FakeLLM 和假工具，不访问模型或文件系统。"""

import asyncio
import json
import threading
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.utils import ModelResponse
from pydantic import BaseModel, ConfigDict, Field

from code_agent.core.agent import Agent
from code_agent.llm import client as client_module


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    repeat: int = Field(default=1, ge=1, le=3)


class FakeLLM:
    """依次返回预设响应，并保存请求当时的快照。"""

    def __init__(self, responses: list[ModelResponse | BaseException]) -> None:
        self._responses = iter(responses)
        self.requests: list[dict[str, Any]] = []

    async def complete_turn(self, messages: list[dict[str, Any]], **kwargs: Any) -> ModelResponse:
        self.requests.append(deepcopy({"messages": messages, **kwargs}))
        try:
            response = next(self._responses)
        except StopIteration as exc:
            raise AssertionError("Agent 发起了预期之外的模型请求") from exc
        if isinstance(response, BaseException):
            raise response
        return response


def final_response(content: str | None = "任务完成") -> ModelResponse:
    return ModelResponse(
        choices=[
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ]
    )


def tool_call(
    call_id: str = "call_1", name: str = "echo", arguments: str = '{"text":"你好"}'
) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def tool_response(
    calls: list[dict[str, Any]], *, finish_reason: str = "tool_calls"
) -> ModelResponse:
    return ModelResponse(
        choices=[
            {
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": None, "tool_calls": calls},
            }
        ]
    )


@pytest.fixture(autouse=True)
def block_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("acompletion", "aresponses"):
        monkeypatch.setattr(
            client_module.litellm,
            name,
            AsyncMock(side_effect=AssertionError("Agent 单测不得访问真实模型")),
        )


@pytest.fixture
def handler() -> Mock:
    return Mock(side_effect=lambda text, repeat=1: text * repeat)


@pytest.fixture
def registry(handler: Mock) -> dict[str, dict[str, Any]]:
    return {
        "echo": {
            "description": "返回指定文本，可重复多次",
            "args_model": EchoArgs,
            "handler": handler,
        }
    }


async def test_run_returns_final_answer_and_sends_serializable_tools(
    registry: dict[str, dict[str, Any]], handler: Mock
) -> None:
    client = FakeLLM([final_response("最终答案")])
    agent = Agent(client, registry)

    assert await agent.run("回答问题") == "最终答案"

    assert len(client.requests) == 1
    request = client.requests[0]
    assert [message["role"] for message in request["messages"]] == ["system", "user"]
    assert request["messages"][-1]["content"] == "回答问题"
    assert "echo" in request["messages"][0]["content"]
    function = request["tools"][0]["function"]
    assert request["tools"][0]["type"] == "function"
    assert function["name"] == "echo"
    assert function["description"] == registry["echo"]["description"]
    assert function["parameters"]["required"] == ["text"]
    assert function["parameters"]["properties"]["repeat"]["default"] == 1
    assert "handler" not in function
    assert json.loads(json.dumps(request["tools"])) == request["tools"]
    assert agent.messages[-1]["role"] == "assistant"
    assert agent.messages[-1]["content"] == "最终答案"
    handler.assert_not_called()


@pytest.mark.parametrize("content", ["", None])
async def test_run_returns_string_for_empty_final_content(
    registry: dict[str, dict[str, Any]], content: str | None
) -> None:
    client = FakeLLM([final_response(content)])

    assert await Agent(client, registry).run("回答问题") == ""
    assert len(client.requests) == 1


async def test_run_executes_tool_and_replays_call_and_result(
    registry: dict[str, dict[str, Any]], handler: Mock
) -> None:
    call = tool_call(arguments='{"text":"你好","repeat":2}')
    client = FakeLLM([tool_response([call]), final_response("收到工具结果")])
    agent = Agent(client, registry)

    assert await agent.run("使用工具") == "收到工具结果"

    handler.assert_called_once_with(text="你好", repeat=2)
    assert len(client.requests) == 2
    assert len(client.requests[0]["messages"]) == 2
    second_request = client.requests[1]
    assert second_request["tools"] == client.requests[0]["tools"]
    history = second_request["messages"]
    assert [message["role"] for message in history] == ["system", "user", "assistant", "tool"]
    assert history[-2]["content"] is None
    assert history[-2]["tool_calls"] == [call]
    assert history[-1] == {"role": "tool", "tool_call_id": "call_1", "content": "你好你好"}


async def test_run_replays_all_tool_results_before_next_request(
    registry: dict[str, dict[str, Any]], handler: Mock
) -> None:
    calls = [
        tool_call("call_first", arguments='{"text":"第一"}'),
        tool_call("call_second", arguments='{"text":"第二","repeat":2}'),
    ]
    client = FakeLLM([tool_response(calls), final_response()])

    await Agent(client, registry).run("调用两个工具")

    assert len(client.requests) == 2
    assert [call.kwargs for call in handler.call_args_list] == [
        {"text": "第一", "repeat": 1},
        {"text": "第二", "repeat": 2},
    ]
    history = client.requests[1]["messages"]
    assert history[-3]["tool_calls"] == calls
    assert history[-2:] == [
        {"role": "tool", "tool_call_id": "call_first", "content": "第一"},
        {"role": "tool", "tool_call_id": "call_second", "content": "第二第二"},
    ]


async def test_run_returns_unknown_tool_error_to_model(
    registry: dict[str, dict[str, Any]], handler: Mock
) -> None:
    client = FakeLLM([tool_response([tool_call(name="unknown")]), final_response()])

    await Agent(client, registry).run("调用工具")

    result = client.requests[1]["messages"][-1]
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "call_1"
    assert result["content"].startswith("工具错误")
    assert "unknown" in result["content"]
    handler.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    ["{invalid json", '{"repeat":2}', '{"text":"你好","repeat":0}', '{"text":"你好","extra":1}'],
    ids=["invalid_json", "missing_required", "invalid_range", "unknown_parameter"],
)
async def test_run_returns_argument_validation_error_without_executing_tool(
    registry: dict[str, dict[str, Any]], handler: Mock, arguments: str
) -> None:
    client = FakeLLM([tool_response([tool_call(arguments=arguments)]), final_response()])

    await Agent(client, registry).run("调用工具")

    result = client.requests[1]["messages"][-1]
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "call_1"
    assert result["content"].startswith("工具参数错误")
    handler.assert_not_called()


@pytest.mark.parametrize("error", [FileNotFoundError("missing.txt"), RuntimeError("工具失败")])
async def test_run_returns_tool_execution_error_to_model(
    registry: dict[str, dict[str, Any]], handler: Mock, error: Exception
) -> None:
    handler.side_effect = error
    client = FakeLLM([tool_response([tool_call()]), final_response("已处理错误")])

    assert await Agent(client, registry).run("调用工具") == "已处理错误"

    result = client.requests[1]["messages"][-1]
    assert result["tool_call_id"] == "call_1"
    assert result["content"].startswith("工具执行错误")
    assert type(error).__name__ in result["content"]
    assert str(error) in result["content"]
    handler.assert_called_once_with(text="你好", repeat=1)


async def test_run_stops_at_max_steps(registry: dict[str, dict[str, Any]], handler: Mock) -> None:
    client = FakeLLM(
        [tool_response([tool_call(f"call_{index}")]) for index in range(3)]
        + [final_response("不应请求这一轮")]
    )
    agent = Agent(client, registry, max_steps=3)

    with pytest.raises(RuntimeError) as caught:
        await agent.run("重复调用工具")

    assert "3" in str(caught.value)
    assert len(client.requests) == 3
    assert handler.call_count == 3
    assert len([message for message in agent.messages if message["role"] == "tool"]) == 3


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
async def test_run_rejects_abnormal_response_without_executing_tool(
    registry: dict[str, dict[str, Any]], handler: Mock, finish_reason: str
) -> None:
    client = FakeLLM([tool_response([tool_call()], finish_reason=finish_reason)])
    agent = Agent(client, registry)

    with pytest.raises(RuntimeError, match=finish_reason):
        await agent.run("调用工具")

    assert len(client.requests) == 1
    assert [message["role"] for message in agent.messages] == ["system", "user"]
    handler.assert_not_called()


async def test_run_rejects_tool_finish_without_calls(
    registry: dict[str, dict[str, Any]], handler: Mock
) -> None:
    client = FakeLLM([tool_response([])])

    with pytest.raises(RuntimeError, match="没有返回调用信息"):
        await Agent(client, registry).run("调用工具")

    assert len(client.requests) == 1
    handler.assert_not_called()


async def test_run_propagates_model_request_error(
    registry: dict[str, dict[str, Any]], handler: Mock
) -> None:
    error = RuntimeError("模型请求失败")
    client = FakeLLM([error])

    with pytest.raises(RuntimeError, match="模型请求失败") as caught:
        await Agent(client, registry).run("回答问题")

    assert caught.value is error
    assert len(client.requests) == 1
    handler.assert_not_called()


async def test_run_propagates_tool_cancellation(
    registry: dict[str, dict[str, Any]], handler: Mock
) -> None:
    handler.side_effect = asyncio.CancelledError()
    client = FakeLLM([tool_response([tool_call()]), final_response("不应继续请求")])
    agent = Agent(client, registry)

    with pytest.raises(asyncio.CancelledError):
        await agent.run("调用工具")

    assert len(client.requests) == 1
    assert not any(message["role"] == "tool" for message in agent.messages)


async def test_run_preserves_history_between_user_turns(
    registry: dict[str, dict[str, Any]], handler: Mock
) -> None:
    client = FakeLLM([final_response("第一轮答案"), final_response("第二轮答案")])
    agent = Agent(client, registry)

    assert await agent.run("第一轮问题") == "第一轮答案"
    assert await agent.run("第二轮问题") == "第二轮答案"

    history = client.requests[1]["messages"]
    assert [message["role"] for message in history] == ["system", "user", "assistant", "user"]
    assert [message["content"] for message in history[1:]] == [
        "第一轮问题",
        "第一轮答案",
        "第二轮问题",
    ]
    handler.assert_not_called()


async def test_run_executes_sync_tool_outside_event_loop_thread(
    registry: dict[str, dict[str, Any]], handler: Mock
) -> None:
    event_loop_thread = threading.get_ident()
    executed_threads: list[int] = []

    def record_thread(text: str, repeat: int) -> str:
        executed_threads.append(threading.get_ident())
        return text * repeat

    handler.side_effect = record_thread
    client = FakeLLM([tool_response([tool_call()]), final_response()])

    await Agent(client, registry).run("调用工具")

    assert len(executed_threads) == 1
    assert executed_threads[0] != event_loop_thread
