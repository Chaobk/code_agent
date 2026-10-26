"""模型层接口的单测：模拟 Chat Completions，不访问真实模型。"""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APITimeoutError
from openai.types.chat import ChatCompletionChunk

from code_agent.config import Settings
from code_agent.llm import client as client_module
from code_agent.llm.client import LLMClient
from tests.code_agent.llm_helpers import make_chunk, make_completion


@pytest.fixture(autouse=True)
def block_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODE_AGENT_REQUEST_TIMEOUT_SECONDS", raising=False)


@pytest.fixture
def client() -> LLMClient:
    return LLMClient(
        Settings(
            _env_file=None,
            DEEPSEEK_API_KEY="test-key",
            model="deepseek-chat",
            base_url="https://example.invalid",
        )
    )


@pytest.fixture
def tools() -> list[dict[str, Any]]:
    return [
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
    ]


async def test_complete_turn_returns_original_response_with_tool_calls_and_usage(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient, tools: list[dict[str, Any]]
) -> None:
    response = make_completion(
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
    monkeypatch.setattr(client_module.AsyncOpenAI.return_value, "request", request)
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
        model="deepseek-chat",
        messages=list(messages),
        tools=list(tools),
        stream=False,
        timeout=60.0,
    )


async def test_stream_turn_preserves_all_chunks_incrementally(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient, tools: list[dict[str, Any]]
) -> None:
    chunks = [
        make_chunk(choices=[{"delta": {"role": "assistant", "content": ""}}]),
        make_chunk(choices=[{"delta": {"content": "你好"}}]),
        make_chunk(
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
        make_chunk(
            choices=[
                {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"README.md"}'}}]}}
            ]
        ),
        make_chunk(choices=[{"delta": {}, "finish_reason": "tool_calls"}]),
        make_chunk(
            choices=[],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        ),
    ]
    consumed: list[ChatCompletionChunk] = []

    async def events() -> AsyncIterator[ChatCompletionChunk]:
        for chunk in chunks:
            consumed.append(chunk)
            yield chunk

    request = AsyncMock(return_value=events())
    monkeypatch.setattr(client_module.AsyncOpenAI.return_value, "request", request)
    messages = ({"role": "user", "content": "你好"},)
    stream = client.stream_turn(messages, tools=tools)

    assert await anext(stream) is chunks[0]
    assert len(consumed) == 1  # 不得等待或提前消费整个响应。
    remaining = [chunk async for chunk in stream]
    assert len(remaining) == len(chunks) - 1
    assert all(actual is original for actual, original in zip(remaining, chunks[1:], strict=True))
    request.assert_awaited_once_with(
        model="deepseek-chat",
        messages=list(messages),
        tools=list(tools),
        stream=True,
        timeout=60.0,
    )


@pytest.mark.parametrize("streaming", [False, True])
async def test_turn_requires_explicit_service_credentials(
    monkeypatch: pytest.MonkeyPatch, streaming: bool
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
    client = LLMClient(Settings(_env_file=None, DEEPSEEK_API_KEY=""))
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        if streaming:
            await anext(client.stream_turn([]))
        else:
            await client.complete_turn([])
    client_module.AsyncOpenAI.assert_not_called()


@pytest.mark.parametrize("streaming", [False, True])
async def test_turn_preserves_explicit_empty_tools(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient, streaming: bool
) -> None:
    async def events() -> AsyncIterator[ChatCompletionChunk]:
        if False:
            yield make_chunk(choices=[])

    request = AsyncMock(return_value=events() if streaming else make_completion())
    monkeypatch.setattr(client_module.AsyncOpenAI.return_value, "request", request)

    if streaming:
        assert [chunk async for chunk in client.stream_turn([], tools=[])] == []
    else:
        await client.complete_turn([], tools=[])
    assert request.await_args.kwargs["tools"] == []


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("请求失败"),
        APITimeoutError(request=httpx.Request("POST", "https://example.invalid")),
    ],
    ids=["request-error", "request-timeout"],
)
async def test_turn_propagates_request_error(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient, streaming: bool, error: Exception
) -> None:
    monkeypatch.setattr(
        client_module.AsyncOpenAI.return_value, "request", AsyncMock(side_effect=error)
    )

    with pytest.raises(type(error)) as caught:
        if streaming:
            await anext(client.stream_turn([]))
        else:
            await client.complete_turn([])
    assert caught.value is error


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("连接中断"),
        APITimeoutError(request=httpx.Request("POST", "https://example.invalid")),
    ],
    ids=["stream-error", "stream-timeout"],
)
async def test_stream_turn_preserves_chunk_before_stream_error(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient, error: Exception
) -> None:
    chunk = make_chunk(choices=[{"delta": {"content": "已收到的文本"}}])

    async def events() -> AsyncIterator[ChatCompletionChunk]:
        yield chunk
        raise error

    monkeypatch.setattr(
        client_module.AsyncOpenAI.return_value, "request", AsyncMock(return_value=events())
    )
    stream = client.stream_turn([])

    assert await anext(stream) is chunk
    with pytest.raises(type(error)) as caught:
        await anext(stream)
    assert caught.value is error


@pytest.mark.parametrize("streaming", [False, True])
async def test_turn_forwards_configured_timeout(
    monkeypatch: pytest.MonkeyPatch, streaming: bool
) -> None:
    client = LLMClient(
        Settings(_env_file=None, DEEPSEEK_API_KEY="test-key", request_timeout_seconds=12.5)
    )

    async def events() -> AsyncIterator[ChatCompletionChunk]:
        yield make_chunk(choices=[])

    request = AsyncMock(return_value=events() if streaming else make_completion())
    monkeypatch.setattr(client_module.AsyncOpenAI.return_value, "request", request)

    if streaming:
        assert len([chunk async for chunk in client.stream_turn([])]) == 1
    else:
        await client.complete_turn([])

    request.assert_awaited_once()
    assert request.await_args.kwargs["timeout"] == 12.5


@pytest.mark.parametrize("streaming", [False, True])
async def test_turn_forwards_extra_request_parameters(
    monkeypatch: pytest.MonkeyPatch,
    client: LLMClient,
    tools: list[dict[str, Any]],
    streaming: bool,
) -> None:
    response = make_completion()

    async def events() -> AsyncIterator[ChatCompletionChunk]:
        yield make_chunk(choices=[])

    request = AsyncMock(return_value=events() if streaming else response)
    monkeypatch.setattr(client_module.AsyncOpenAI.return_value, "request", request)
    messages = ({"role": "user", "content": "读取 README"},)
    options = {
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 0.2,
        "max_tokens": 256,
        "response_format": {"type": "json_object"},
        "timeout": 15,
    }

    if streaming:
        assert len([chunk async for chunk in client.stream_turn(messages, **options)]) == 1
    else:
        assert await client.complete_turn(messages, **options) is response
    request.assert_awaited_once_with(
        model="deepseek-chat",
        messages=list(messages),
        stream=streaming,
        **options,
    )


@pytest.mark.parametrize("streaming", [False, True])
async def test_turn_request_overrides_do_not_change_client_defaults(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient, streaming: bool
) -> None:
    async def events() -> AsyncIterator[ChatCompletionChunk]:
        yield make_chunk(choices=[])

    request = AsyncMock(side_effect=lambda **kwargs: events() if streaming else make_completion())
    monkeypatch.setattr(client_module.AsyncOpenAI.return_value, "request", request)
    overrides = {
        "model": "other-model",
        "api_key": "other-test-key",
        "base_url": "https://other.example.invalid",
        "timeout": 2.5,
    }
    if streaming:
        assert len([chunk async for chunk in client.stream_turn([], **overrides)]) == 1
        assert len([chunk async for chunk in client.stream_turn([])]) == 1
    else:
        await client.complete_turn([], **overrides)
        await client.complete_turn([])

    assert request.await_args_list[0].kwargs == {
        "messages": [],
        "stream": streaming,
        "model": "other-model",
        "timeout": 2.5,
    }
    sdk = client_module.AsyncOpenAI.return_value
    assert sdk.with_options.call_args_list[0].kwargs == {
        "api_key": "other-test-key",
        "base_url": "https://other.example.invalid",
    }
    assert sdk.with_options.call_args_list[1].kwargs == {
        "api_key": "test-key",
        "base_url": "https://example.invalid",
    }
    assert request.await_args_list[1].kwargs == {
        "model": "deepseek-chat",
        "messages": [],
        "stream": streaming,
        "timeout": 60.0,
    }


@pytest.mark.parametrize("streaming", [False, True])
async def test_turn_rejects_stream_option_before_request(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient, streaming: bool
) -> None:
    request = AsyncMock()
    monkeypatch.setattr(client_module.AsyncOpenAI.return_value, "request", request)

    with pytest.raises(ValueError, match="stream"):
        if streaming:
            await anext(client.stream_turn([], stream=False))
        else:
            await client.complete_turn([], stream=True)
    request.assert_not_awaited()
