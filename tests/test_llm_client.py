"""模型层接口的单测：模拟 Chat Completions，不访问真实模型。"""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from litellm.types.utils import ModelResponse, ModelResponseStream

from code_agent.config import Settings
from code_agent.llm import client as client_module
from code_agent.llm.client import LLMClient


@pytest.fixture(autouse=True)
def block_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    # 每个测试必须显式模拟请求，避免错误实现访问真实服务。
    for name in ("acompletion", "aresponses"):
        monkeypatch.setattr(
            client_module.litellm,
            name,
            AsyncMock(side_effect=AssertionError("测试不得访问真实模型")),
        )


@pytest.fixture
def client() -> LLMClient:
    return LLMClient(
        Settings(
            _env_file=None,
            DEEPSEEK_API_KEY="test-key",
            model="deepseek/deepseek-chat",
            base_url="https://example.invalid",
        )
    )


@pytest.fixture
def tools() -> tuple[dict[str, Any], ...]:
    return (
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取文件",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
    )


async def test_complete_turn_returns_original_response_with_tool_calls_and_usage(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient, tools: tuple[dict[str, Any], ...]
) -> None:
    response = ModelResponse(
        choices=[
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"README.md"}',
                            },
                        }
                    ],
                },
            }
        ],
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )
    request = AsyncMock(return_value=response)
    monkeypatch.setattr(client_module.litellm, "acompletion", request)
    messages = ({"role": "user", "content": "读取 README"},)

    result = await client.complete_turn(messages, tools=tools)

    assert result is response
    assert result.choices[0].message.content is None
    call = result.choices[0].message.tool_calls[0]
    assert call.id == "call_1"
    assert call.function.name == "read_file"
    assert call.function.arguments == '{"path":"README.md"}'
    assert result.choices[0].finish_reason == "tool_calls"
    assert result.usage.total_tokens == 15
    request.assert_awaited_once_with(
        model="deepseek/deepseek-chat",
        messages=list(messages),
        tools=list(tools),
        stream=False,
        api_key="test-key",
        base_url="https://example.invalid",
    )


async def test_stream_turn_preserves_all_chunks_incrementally(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient, tools: tuple[dict[str, Any], ...]
) -> None:
    chunks = [
        ModelResponseStream(choices=[{"delta": {"role": "assistant", "content": ""}}]),
        ModelResponseStream(choices=[{"delta": {"content": "你好"}}]),
        ModelResponseStream(
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
        ),
        ModelResponseStream(
            choices=[
                {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"README.md"}'}}]}}
            ]
        ),
        ModelResponseStream(choices=[{"delta": {}, "finish_reason": "tool_calls"}]),
        ModelResponseStream(
            choices=[],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        ),
    ]
    consumed: list[ModelResponseStream] = []

    async def events() -> AsyncIterator[ModelResponseStream]:
        for chunk in chunks:
            consumed.append(chunk)
            yield chunk

    request = AsyncMock(return_value=events())
    monkeypatch.setattr(client_module.litellm, "acompletion", request)
    messages = ({"role": "user", "content": "你好"},)
    stream = client.stream_turn(messages, tools=tools)

    assert await anext(stream) is chunks[0]
    assert len(consumed) == 1  # 不得等待或提前消费整个响应。
    remaining = [chunk async for chunk in stream]
    assert len(remaining) == len(chunks) - 1
    assert all(actual is original for actual, original in zip(remaining, chunks[1:], strict=True))
    request.assert_awaited_once_with(
        model="deepseek/deepseek-chat",
        messages=list(messages),
        tools=list(tools),
        stream=True,
        api_key="test-key",
        base_url="https://example.invalid",
    )


@pytest.mark.parametrize("streaming", [False, True])
async def test_turn_omits_unset_credentials_and_tools(
    monkeypatch: pytest.MonkeyPatch, streaming: bool
) -> None:
    response = ModelResponse(choices=[{"message": {"role": "assistant", "content": "完整文本"}}])

    async def events() -> AsyncIterator[ModelResponseStream]:
        yield ModelResponseStream(choices=[])

    request = AsyncMock(return_value=events() if streaming else response)
    monkeypatch.setattr(client_module.litellm, "acompletion", request)
    client = LLMClient(
        Settings(_env_file=None, DEEPSEEK_API_KEY="", base_url="", model="test-model")
    )

    if streaming:
        assert len([chunk async for chunk in client.stream_turn([])]) == 1
    else:
        assert await client.complete_turn([]) is response
        assert response.choices[0].message.content == "完整文本"
    request.assert_awaited_once_with(model="test-model", messages=[], stream=streaming)


@pytest.mark.parametrize("streaming", [False, True])
async def test_turn_preserves_explicit_empty_tools(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient, streaming: bool
) -> None:
    async def events() -> AsyncIterator[ModelResponseStream]:
        if False:
            yield ModelResponseStream(choices=[])

    request = AsyncMock(return_value=events() if streaming else ModelResponse())
    monkeypatch.setattr(client_module.litellm, "acompletion", request)

    if streaming:
        assert [chunk async for chunk in client.stream_turn([], tools=())] == []
    else:
        await client.complete_turn([], tools=())
    assert request.await_args.kwargs["tools"] == []


@pytest.mark.parametrize("streaming", [False, True])
async def test_turn_propagates_request_error(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient, streaming: bool
) -> None:
    error = RuntimeError("请求失败")
    monkeypatch.setattr(client_module.litellm, "acompletion", AsyncMock(side_effect=error))

    with pytest.raises(RuntimeError, match="请求失败") as caught:
        if streaming:
            await anext(client.stream_turn([]))
        else:
            await client.complete_turn([])
    assert caught.value is error


async def test_stream_turn_preserves_chunk_before_stream_error(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient
) -> None:
    error = RuntimeError("连接中断")
    chunk = ModelResponseStream(choices=[{"delta": {"content": "已收到的文本"}}])

    async def events() -> AsyncIterator[ModelResponseStream]:
        yield chunk
        raise error

    monkeypatch.setattr(client_module.litellm, "acompletion", AsyncMock(return_value=events()))
    stream = client.stream_turn([])

    assert await anext(stream) is chunk
    with pytest.raises(RuntimeError, match="连接中断") as caught:
        await anext(stream)
    assert caught.value is error
