"""M2b：组装一轮流式响应；核心逻辑由用户实现。

组装器只处理分片，不打印、不修改历史、不执行工具。
调用方在流正常耗尽后调用 finish；流异常或取消时不提交本轮。
"""

from dataclasses import dataclass
from typing import Any

from litellm.types.utils import ChatCompletionDeltaToolCall, ModelResponseStream


@dataclass
class AssembledTurn:
    """组装后的助手消息，使用 ConversationHistory 接受的消息格式。"""

    message: dict[str, Any]
    finish_reason: str


class TurnAssembler:
    """每轮新建一个实例，push 累计增量，finish 检查并生成完整消息"""

    def __init__(self) -> None:
        self._text_parts: list[str] = []
        self._finish_reason: str | None = None
        self._calls_by_index: dict[int, dict[str, Any]] = {}

    def push(self, chunk: ModelResponseStream) -> str | None:
        if not chunk.choices:
            return None

        choice = chunk.choices[0]
        text = choice.delta.content
        fragments = choice.delta.tool_calls or []

        if self._finish_reason is not None and (text or fragments):
            raise RuntimeError("响应结束后仍收到新增内容")

        if text:
            self._text_parts.append(text)

        for fragment in fragments:
            self._merge_tool_fragment(fragment)

        if choice.finish_reason is not None:
            self._finish_reason = choice.finish_reason

        return text or None

    def _merge_tool_fragment(self, fragment: ChatCompletionDeltaToolCall) -> None:
        cached = self._calls_by_index.setdefault(
            fragment.index,
            {
                "id": None,
                "type": None,
                "function": {"name": None, "arguments": ""},
            },
        )

        for field in ("id", "type"):
            value = getattr(fragment, field)
            if value is not None:
                cached[field] = value

        if cached["type"] not in (None, "function"):
            raise RuntimeError("工具调用类型无效")

        function = fragment.function
        if function is None:
            return

        name = function.name
        saved_name = cached["function"]["name"]
        if name is not None:
            if saved_name is not None and saved_name != name:
                raise RuntimeError("同一工具调用的名称冲突")
            cached["function"]["name"] = name

        cached["function"]["arguments"] += function.arguments or ""

    def finish(self) -> AssembledTurn:
        if self._finish_reason is None:
            raise RuntimeError("流式响应缺少结束原因")
        if self._finish_reason not in ("stop", "tool_calls"):
            raise RuntimeError(f"模型未正常结束: {self._finish_reason}")

        tool_calls: list[dict[str, Any]] = []

        for index in sorted(self._calls_by_index):
            cached = self._calls_by_index[index]
            call_id = cached["id"]
            function = cached["function"]
            name = function["name"]

            if not isinstance(call_id, str) or not call_id.strip():
                raise RuntimeError(f"工具调用 index={index} 缺少有效 ID")

            if cached["type"] != "function":
                raise RuntimeError(f"工具调用 index={index} 的类型无效")
            if not isinstance(name, str) or not name.strip():
                raise RuntimeError(f"工具调用 index={index} 缺少有效名称")

            tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": function["arguments"],
                    },
                }
            )
        if self._finish_reason == "tool_calls" and not tool_calls:
            raise RuntimeError("模型报告工具调用，但没有返回调用信息")

        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(self._text_parts),
        }

        if tool_calls:
            message["tool_calls"] = tool_calls

        return AssembledTurn(message=message, finish_reason=self._finish_reason)
