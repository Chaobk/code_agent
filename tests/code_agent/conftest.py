"""默认阻止真实 SDK 请求，传输集成测试显式提供内存 HTTP 客户端。"""

from collections.abc import AsyncGenerator, AsyncIterator
from unittest.mock import AsyncMock, Mock

import pytest
from openai.types.chat import ChatCompletionChunk

from code_agent.llm import client as client_module


class FakeSDKStream:
    """让脚本生成器具有 SDK 流的上下文管理和关闭语义。"""

    def __init__(self, iterator: AsyncGenerator[ChatCompletionChunk, None]) -> None:
        self.iterator = iterator

    async def __aenter__(self) -> "FakeSDKStream":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.iterator.aclose()

    def __aiter__(self) -> AsyncIterator[ChatCompletionChunk]:
        return self.iterator


@pytest.fixture(autouse=True)
def sdk(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """只模拟 SDK 调用，生产客户端与组装器照常执行。"""
    sdk = Mock()
    sdk.request = AsyncMock(side_effect=AssertionError("测试不得访问真实模型"))
    sdk.close = AsyncMock()
    sdk.with_options.return_value = sdk

    async def create(**kwargs: object) -> object:
        result = await sdk.request(**kwargs)
        return FakeSDKStream(result) if kwargs.get("stream") else result

    sdk.chat.completions.create = create
    monkeypatch.setattr(client_module, "AsyncOpenAI", Mock(return_value=sdk))
    return sdk
