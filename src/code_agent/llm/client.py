"""模型层(A):用 litellm 统一调用各家模型。

将 Settings 翻译为 Chat Completions 请求参数，原样返回完整响应或流式 chunk。
文本与工具参数分片由 llm.streaming 组装，历史提交与工具执行由 core 编排；
M6 再接入用量与成本统计。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import litellm
from litellm.types.utils import ModelResponse, ModelResponseStream

from code_agent.config import Settings


class LLMClient:
    """封装 LiteLLM 请求，保留完整响应与流式 chunk。

    通过 ``**kwargs`` 透传 tools、tool_choice 等请求参数，并覆盖 Settings 默认值。
    stream 由两个调用方法固定控制，不接受作为额外参数传入。
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
