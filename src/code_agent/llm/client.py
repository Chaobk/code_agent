"""模型层(A):用 litellm 统一调用各家模型。

这一层刻意保持很薄 —— 它只做三件事:

1. 把 pydantic ``Settings`` 翻译成 litellm 的调用参数;
2. 把 litellm 的流式响应归一化成"文本增量";
3. (M6)记录 token 用量与成本。

它**不认识**工具、模式与界面;那些是上层的事(M1 起)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import litellm

from code_agent.config import Settings


class LLMClient:
    """对 litellm 的最小封装。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _request_kwargs(self, messages: Sequence[dict[str, Any]]) -> dict[str, Any]:
        """把配置翻译成 litellm 调用参数。"""
        kwargs: dict[str, Any] = {
            "model": self._settings.model,
            "messages": list(messages),
        }
        if self._settings.api_key:
            kwargs["api_key"] = self._settings.api_key
        if self._settings.base_url:
            kwargs["base_url"] = self._settings.base_url
        return kwargs

    async def astream_text(self, messages: Sequence[dict[str, Any]]) -> AsyncIterator[str]:
        """流式产出助手回复的文本增量。"""
        response = await litellm.acompletion(stream=True, **self._request_kwargs(messages))
        async for chunk in response:
            text = _delta_text(chunk)
            if text:
                yield text


def _delta_text(chunk: Any) -> str:
    """从 litellm 的流式 chunk 里取出文本增量。

    litellm 把各家响应归一化成了 OpenAI 风格,但仍有两种常见情况需要兜住:

    - ``choices`` 为空:部分供应商的收尾帧;
    - ``delta.content`` 为 ``None``:纯工具调用帧(M1 起会出现)。
    """
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return ""
    return getattr(delta, "content", None) or ""
