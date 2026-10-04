"""模型层(A):用 litellm 统一调用各家模型。

这一层刻意保持很薄 —— 它只做三件事:

1. 把 pydantic ``Settings`` 翻译成 litellm 的调用参数;
2. 把 Responses API 的流式事件归一化成"文本增量";
3. (M6)记录 token 用量与成本。

它**不认识**工具、模式与界面;那些是上层的事(M1 起)。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

import litellm

from code_agent.config import Settings, load_settings


class LLMClient:
    """通过 Responses API 提供完整文本与流式文本响应。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _request_kwargs(self, input_items: Sequence[Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._settings.model,
            "input": list(input_items),
            "store": False,
        }

        if self._settings.api_key:
            kwargs["api_key"] = self._settings.api_key
        if self._settings.base_url:
            kwargs["base_url"] = self._settings.base_url
        return kwargs

    async def complete_turn(self, input_items: Sequence[Any]) -> str:
        """返回一次请求的完整文本。"""
        response = await litellm.aresponses(**self._request_kwargs(input_items))
        return response.output_text

    async def stream_turn(self, input_items: Sequence[Any]) -> AsyncIterator[str]:
        """逐段产出文本增量，跳过生命周期、工具调用和完成事件。"""
        response = await litellm.aresponses(
            stream=True,
            **self._request_kwargs(input_items),
        )
        async for event in response:
            if event.type == "response.output_text.delta" and event.delta:
                yield event.delta


if __name__ == "__main__":

    async def _demo() -> None:
        client = LLMClient(load_settings())
        input_items = [{"role": "user", "content": "你好"}]
        async for text in client.stream_turn(input_items):
            print(text, end="", flush=True)
        print()

    asyncio.run(_demo())
