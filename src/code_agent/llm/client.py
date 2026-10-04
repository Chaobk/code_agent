"""模型层(A):用 litellm 统一调用各家模型。

将 Settings 翻译为 Chat Completions 请求参数，原样返回完整响应或流式 chunk。
文本提取、工具参数组装与工具执行由调用方负责；M6 再接入用量与成本统计。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

import litellm
from litellm.types.utils import ModelResponse, ModelResponseStream

from code_agent.config import Settings, load_settings


class LLMClient:
    """封装 LiteLLM 请求，保留完整响应与流式 chunk。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _request_kwargs(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._settings.model,
            "messages": list(messages),
        }

        if tools is not None:
            kwargs["tools"] = list(tools)
        if self._settings.api_key:
            kwargs["api_key"] = self._settings.api_key
        if self._settings.base_url:
            kwargs["base_url"] = self._settings.base_url
        return kwargs

    async def complete_turn(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        """返回完整响应，由调用方处理文本和工具调用。"""
        return await litellm.acompletion(
            stream=False,
            **self._request_kwargs(messages, tools),
        )

    async def stream_turn(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> AsyncIterator[ModelResponseStream]:
        """逐个返回完整 chunk，不过滤或拼接内容。"""
        stream = await litellm.acompletion(
            stream=True,
            **self._request_kwargs(messages, tools),
        )
        async for chunk in stream:
            yield chunk


if __name__ == "__main__":
    # async def _demo() -> None:
    #     client = LLMClient(load_settings())
    #     messages = [{"role": "user", "content": "你好"}]
    #     async for chunk in client.stream_turn(messages):
    #         if not chunk.choices:
    #             continue
    #         text = chunk.choices[0].delta.content
    #         if text:
    #             print(text, end="", flush=True)
    #     print()
    #
    # asyncio.run(_demo())

    async def _demo2() -> None:
        client = LLMClient(load_settings())
        messages = [{"role": "user", "content": "你好"}]
        res = await client.complete_turn(messages)
        print(res)

    asyncio.run(_demo2())
