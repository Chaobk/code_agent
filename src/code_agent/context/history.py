"""内存消息与工具调用配对校验，不处理文件保存。"""

from copy import deepcopy
from typing import Any

from code_agent.events import ToolCall


class ConversationHistory:
    """保存会话消息，并校验工具调用与结果的配对关系。"""

    def __init__(self) -> None:
        self._messages: list[dict[str, Any]] = []
        self._pending_calls: dict[str, ToolCall] = {}

    def add_message(self, message: dict[str, Any]) -> None:
        saved = deepcopy(message)
        role = saved.get("role")

        if role not in ("system", "user", "assistant", "tool"):
            raise ValueError(f"不支持的消息角色：{role}")

        content = saved.get("content")
        if role == "assistant":
            if content is not None and not isinstance(content, str):
                raise ValueError("助手消息正文必须是字符串或 None")
        elif not isinstance(content, str):
            raise ValueError("消息正文必须是字符串")

        if self._pending_calls and role != "tool":
            raise ValueError("必须先补齐当前工具调用的结果")

        # 暂存配对变更，避免校验失败时留下部分状态。
        new_calls: dict[str, ToolCall] = {}
        completed_id: str | None = None

        if role == "assistant":
            raw_calls = saved.get("tool_calls")
            if raw_calls is None:
                raw_calls = []
            if not isinstance(raw_calls, list):
                raise ValueError("tool_calls 必须是列表")

            for raw in raw_calls:
                if not isinstance(raw, dict) or raw.get("type") != "function":
                    raise ValueError("工具调用必须使用 function 格式")

                try:
                    call = ToolCall.model_validate(
                        {
                            "id": raw["id"],
                            "name": raw["function"]["name"],
                            "arguments": raw["function"]["arguments"],
                        },
                        strict=True,
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("工具调用结构不完整或字段类型错误") from exc

                if not call.id.strip() or not call.name.strip():
                    raise ValueError("工具调用 ID 和名称不能为空")
                if call.id in new_calls:
                    raise ValueError(f"工具调用 ID 重复：{call.id}")

                new_calls[call.id] = call

        else:
            if saved.get("tool_calls"):
                raise ValueError("只有助手消息可以包含工具调用")

            if role == "tool":
                call_id = saved.get("tool_call_id")
                if not isinstance(call_id, str) or call_id not in self._pending_calls:
                    raise ValueError(f"没有对应的待配对工具调用：{call_id}")

                completed_id = call_id

        self._messages.append(saved)
        self._pending_calls.update(new_calls)

        if completed_id is not None:
            del self._pending_calls[completed_id]

    def get_messages(self) -> list[dict[str, Any]]:
        return deepcopy(self._messages)

    def get_pending_calls(self) -> dict[str, ToolCall]:
        return deepcopy(self._pending_calls)

    def get_model_messages(self) -> list[dict[str, Any]]:
        if self._pending_calls:
            raise RuntimeError("尚未配对结果")
        return self.get_messages()
