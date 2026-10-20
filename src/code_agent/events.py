"""消息、工具结果与运行事件的数据模型。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, JsonValue


class Role(StrEnum):
    """消息角色。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ModelRequest(BaseModel):
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None


class ToolCall(BaseModel):
    """模型请求的工具调用，arguments 保存尚未解析的 JSON 字符串。"""

    id: str
    name: str
    arguments: str = "{}"


class ToolStatus(StrEnum):
    """工具调用的结束状态；进程退出码保存在 ToolResult.output 中。

    取消通过 CancelledError 传播。
    """

    # 已取得结果，包括 Bash 非零退出。
    COMPLETED = "completed"

    # 参数校验或执行失败，原因保存在 output 中。
    ERROR = "error"

    # 执行超时，保留已取得的部分输出。
    TIMED_OUT = "timed_out"


class ToolResult(BaseModel):
    """工具输出、结束状态与截断标记。"""

    output: JsonValue
    status: ToolStatus
    truncated: bool = False


@dataclass(frozen=True)
class RunResult:
    """一次运行的受控结束结果；取消和内部故障仍向外传播。"""

    status: Literal["completed", "failed", "max_steps"]
    text: str = ""
    error: str | None = None


class Message(BaseModel):
    """对话消息，包含正文及工具调用关联信息。"""

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None


class EventType(StrEnum):
    """运行事件类型。"""

    TEXT_DELTA = "text_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"


class AgentEvent(BaseModel):
    """所有事件的基类。"""

    type: EventType


class TextDelta(AgentEvent):
    """流式文本增量。"""

    type: Literal[EventType.TEXT_DELTA] = EventType.TEXT_DELTA
    text: str


class ToolCallEvent(AgentEvent):
    """模型请求了一次工具调用。"""

    type: Literal[EventType.TOOL_CALL] = EventType.TOOL_CALL
    tool_call: ToolCall


class ToolResultEvent(AgentEvent):
    type: Literal[EventType.TOOL_RESULT] = EventType.TOOL_RESULT
    tool_result: ToolResult
    tool_call_id: str


class ToolExecutionCancelled(asyncio.CancelledError):
    """携带已取得工具结果的取消异常。"""

    def __init__(self, observation: ToolResult) -> None:
        super().__init__("工具执行被取消")
        self.observation = observation


AnyEvent = TextDelta | ToolCallEvent | ToolResultEvent
