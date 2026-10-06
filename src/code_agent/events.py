"""内核事件与消息模型 —— 全系统唯一的共享契约。

各能力子包之间**互不 import**,只通过这里的数据模型通信:

    模型输出 ──► Event ──► 界面渲染 / 轨迹落盘
                    ▲
    工具执行结果 ────┘
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


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
    """模型请求的一次工具调用。

    ``arguments`` 是模型给回的 JSON **字符串**(未解析)。解析与校验由工具层负责 ——
    模型层不替它做这个决定。
    """

    id: str
    name: str
    arguments: str = "{}"


class Message(BaseModel):
    """一轮对话消息(OpenAI chat 风格的超集)。"""

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None


class EventType(StrEnum):
    """事件类型。界面与轨迹层只依赖这些类型,不依赖任何内部实现。"""

    TEXT_DELTA = "text_delta"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    STEP_STARTED = "step_started"
    STEP_FINISHED = "step_finished"
    RUN_FINISHED = "run_finished"
    ERROR = "error"


class AgentEvent(BaseModel):
    """所有事件的基类。"""

    type: EventType


class TextDelta(AgentEvent):
    """流式文本增量。"""

    type: Literal[EventType.TEXT_DELTA] = EventType.TEXT_DELTA
    text: str


class AssistantMessage(AgentEvent):
    """一轮完整的助手消息(流式结束后产生)。"""

    type: Literal[EventType.ASSISTANT_MESSAGE] = EventType.ASSISTANT_MESSAGE
    message: Message


class ToolCallEvent(AgentEvent):
    """模型请求了一次工具调用。"""

    type: Literal[EventType.TOOL_CALL] = EventType.TOOL_CALL
    tool_call: ToolCall


class ToolResultEvent(AgentEvent):
    """一次工具调用的结果。"""

    type: Literal[EventType.TOOL_RESULT] = EventType.TOOL_RESULT
    tool_call_id: str
    output: str
    is_error: bool = False


class StepStarted(AgentEvent):
    """一步(一次模型往返)开始。"""

    type: Literal[EventType.STEP_STARTED] = EventType.STEP_STARTED
    index: int


class StepFinished(AgentEvent):
    """一步结束。"""

    type: Literal[EventType.STEP_FINISHED] = EventType.STEP_FINISHED
    index: int


class RunFinished(AgentEvent):
    """整次任务结束。"""

    type: Literal[EventType.RUN_FINISHED] = EventType.RUN_FINISHED
    reason: str


class ErrorEvent(AgentEvent):
    """出错。"""

    type: Literal[EventType.ERROR] = EventType.ERROR
    message: str


AnyEvent = (
    TextDelta
    | AssistantMessage
    | ToolCallEvent
    | ToolResultEvent
    | StepStarted
    | StepFinished
    | RunFinished
    | ErrorEvent
)
