"""模型层流式接口的单测：模拟 Responses API，不访问真实模型。"""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from code_agent.config import Settings
from code_agent.llm import client as client_module
from code_agent.llm.client import LLMClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> LLMClient:
    # 阻止回归到 Chat Completions，也确保错误实现不会发起真实请求。
    monkeypatch.setattr(
        client_module.litellm,
        "acompletion",
        AsyncMock(side_effect=AssertionError("stream_turn 必须使用 Responses API")),
    )
    return LLMClient(
        Settings(
            _env_file=None,
            DEEPSEEK_API_KEY="test-key",
            model="deepseek/deepseek-chat",
            base_url="https://example.invalid",
        )
    )


async def test_stream_turn_yields_text_incrementally(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient
) -> None:
    consumed: list[str] = []

    async def events() -> AsyncIterator[SimpleNamespace]:
        for event in (
            SimpleNamespace(type="response.created"),
            SimpleNamespace(type="response.output_text.delta", delta="你好"),
            SimpleNamespace(type="response.output_text.delta", delta=""),
            SimpleNamespace(type="response.function_call_arguments.delta", delta='{"path":'),
            SimpleNamespace(type="response.output_text.delta", delta="，世界"),
            SimpleNamespace(type="response.output_text.done", text="你好，世界"),
            SimpleNamespace(type="response.completed"),
        ):
            consumed.append(event.type)
            yield event

    request = AsyncMock(return_value=events())
    monkeypatch.setattr(client_module.litellm, "aresponses", request)
    input_items = ({"role": "user", "content": "你好"},)
    stream = client.stream_turn(input_items)

    # 第一段文本应立即产出，不能等待整个响应结束。
    assert await anext(stream) == "你好"
    assert consumed == ["response.created", "response.output_text.delta"]
    assert [text async for text in stream] == ["，世界"]
    request.assert_awaited_once_with(
        model="deepseek/deepseek-chat",
        input=list(input_items),
        store=False,
        stream=True,
        api_key="test-key",
        base_url="https://example.invalid",
    )


async def test_stream_turn_empty_stream_omits_unset_credentials(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient
) -> None:
    async def events() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(type="response.completed")

    request = AsyncMock(return_value=events())
    monkeypatch.setattr(client_module.litellm, "aresponses", request)
    client = LLMClient(
        Settings(_env_file=None, DEEPSEEK_API_KEY="", base_url="", model="test-model")
    )

    assert [text async for text in client.stream_turn([])] == []
    request.assert_awaited_once_with(model="test-model", input=[], store=False, stream=True)


async def test_stream_turn_propagates_request_error(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient
) -> None:
    error = RuntimeError("请求失败")
    monkeypatch.setattr(client_module.litellm, "aresponses", AsyncMock(side_effect=error))

    with pytest.raises(RuntimeError, match="请求失败") as caught:
        await anext(client.stream_turn([]))
    assert caught.value is error


async def test_stream_turn_preserves_text_before_stream_error(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient
) -> None:
    error = RuntimeError("连接中断")

    async def events() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(type="response.output_text.delta", delta="已收到的文本")
        raise error

    monkeypatch.setattr(client_module.litellm, "aresponses", AsyncMock(return_value=events()))
    stream = client.stream_turn([])

    assert await anext(stream) == "已收到的文本"
    with pytest.raises(RuntimeError, match="连接中断") as caught:
        await anext(stream)
    assert caught.value is error


async def test_complete_turn_still_returns_full_text(
    monkeypatch: pytest.MonkeyPatch, client: LLMClient
) -> None:
    request = AsyncMock(return_value=SimpleNamespace(output_text="完整响应"))
    monkeypatch.setattr(client_module.litellm, "aresponses", request)

    assert await client.complete_turn([]) == "完整响应"
    request.assert_awaited_once_with(
        model="deepseek/deepseek-chat",
        input=[],
        store=False,
        api_key="test-key",
        base_url="https://example.invalid",
    )
