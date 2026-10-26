"""OpenAI 兼容的 Chat Completions 异步客户端。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from code_agent.config import Settings


class LLMClient:
    """异步调用 OpenAI 兼容服务，返回完整响应或原始流式分片。

    额外关键字参数覆盖配置默认值；stream 参数由调用方法决定。
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: AsyncOpenAI | None = None

    def _request_client(self, kwargs: dict[str, Any]) -> AsyncOpenAI:
        """复用连接池；请求级连接参数通过副本覆盖，不改变默认值。"""
        api_key = kwargs.pop("api_key", self._settings.api_key)
        base_url = kwargs.pop("base_url", self._settings.base_url)
        if not api_key:
            raise ValueError("缺少 DEEPSEEK_API_KEY，请配置模型服务密钥")
        if not base_url:
            raise ValueError("缺少 CODE_AGENT_BASE_URL，请配置模型服务地址")
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._settings.api_key or api_key,
                base_url=self._settings.base_url or base_url,
                max_retries=0,
            )
        return self._client.with_options(api_key=api_key, base_url=base_url)

    async def close(self) -> None:
        """在事件循环关闭前释放共享 HTTP 连接。"""
        if self._client is not None:
            await self._client.close()
            self._client = None

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

        request_kwargs.update(kwargs)
        # 兼容迁移前 .env 中的 DeepSeek 路由名称；SDK 直接接收服务端模型名。
        request_kwargs["model"] = request_kwargs["model"].removeprefix("deepseek/")
        return request_kwargs

    async def complete_turn(
        self,
        messages: Sequence[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatCompletion:
        """返回完整响应，由调用方处理文本和工具调用。"""
        request_kwargs = self._request_kwargs(messages, **kwargs)
        client = self._request_client(request_kwargs)
        return await client.chat.completions.create(stream=False, **request_kwargs)

    async def stream_turn(
        self,
        messages: Sequence[dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncIterator[ChatCompletionChunk]:
        """逐个返回完整 chunk，不过滤或拼接内容。"""
        request_kwargs = self._request_kwargs(messages, **kwargs)
        client = self._request_client(request_kwargs)
        stream = await client.chat.completions.create(stream=True, **request_kwargs)
        try:
            async with stream:
                async for chunk in stream:
                    yield chunk
        except httpx.TimeoutException as exc:
            # SDK 建连时已转换异常，但读取 SSE 期间可能原样抛出 HTTPX 异常。
            raise APITimeoutError(request=stream.response.request) from exc
        except httpx.RequestError as exc:
            raise APIConnectionError(request=stream.response.request) from exc
