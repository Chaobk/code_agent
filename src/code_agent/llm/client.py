"""LiteLLM Chat Completions 异步客户端。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import litellm
from litellm.types.utils import ModelResponse, ModelResponseStream

from code_agent.config import Settings


class LLMClient:
    """异步调用 LiteLLM，返回完整响应或原始流式分片。

    额外关键字参数覆盖配置默认值；stream 参数由调用方法决定。
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _request_kwargs(
        self,
        messages: Sequence[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        if "stream" in kwargs:
            raise ValueError("stream 由调用方法决定，请使用 complete_turn 或 stream_turn")

        request_kwargs: dict[str, Any] = {
            "model": self._settings.model,
            "messages": list(messages),
            "timeout": self._settings.request_timeout_seconds,
        }

        if self._settings.api_key:
            request_kwargs["api_key"] = self._settings.api_key
        if self._settings.base_url:
            request_kwargs["base_url"] = self._settings.base_url
        request_kwargs.update(kwargs)
        return request_kwargs

    async def complete_turn(
        self,
        messages: Sequence[dict[str, Any]],
        **kwargs: Any,
    ) -> ModelResponse:
        """返回完整响应，由调用方处理文本和工具调用。"""
        return await litellm.acompletion(
            stream=False,
            **self._request_kwargs(messages, **kwargs),
        )

    async def stream_turn(
        self,
        messages: Sequence[dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncIterator[ModelResponseStream]:
        """逐个返回完整 chunk，不过滤或拼接内容。"""
        stream = await litellm.acompletion(
            stream=True,
            **self._request_kwargs(messages, **kwargs),
        )
        async for chunk in stream:
            yield chunk
