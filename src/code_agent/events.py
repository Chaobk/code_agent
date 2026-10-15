"""内核事件与消息模型 —— 全系统唯一的共享契约。

共享消息、工具结果与事件契约;业务编排集中在核心,能力通过契约协作:

    模型输出 ──► Event ──► 界面渲染 / 轨迹落盘
                    ▲
    工具执行结果 ────┘
"""

from __future__ import annotations

import asyncio
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
    """模型请求的一次工具调用。

    ``arguments`` 是模型给回的 JSON **字符串**(未解析)。解析与校验由工具层负责 ——
    模型层不替它做这个决定。
    """

    id: str
    name: str
    arguments: str = "{}"


class ToolStatus(StrEnum):
    """工具调用的结束状态,不表示命令零退出或用户任务已经完成。

    取消不转换为这些状态:清理执行进程后继续传播 CancelledError。
    """

    # 工具完成调用并取得有效观察;Shell 非零退出(如测试失败)也属于此状态。
    # 原始进程退出码保留在 ToolResult.output 中,由模型判断命令的具体结果。
    COMPLETED = "completed"

    # 工具自身无法正常完成调用,如 Bash 无法启动、执行目录无效或无权限。
    # 失败原因放在 ToolResult.output 中;Shell 没有退出码时使用 None。
    ERROR = "error"

    # 超过工具执行时限;清理进程后返回超时原因和已捕获的部分输出。
    # Shell 的原始退出码按约定使用 None,不将超时归为普通 ERROR。
    TIMED_OUT = "timed_out"


class ToolResult(BaseModel):
    """
    工具输出的统一格式
    """

    output: JsonValue
    # 工具调用的结束状态;具体退出码、失败原因等观察信息放在 output 中。
    status: ToolStatus
    # 工具输出是否被截断
    truncated: bool = False


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
    type: Literal[EventType.TOOL_RESULT] = EventType.TOOL_RESULT
    tool_result: ToolResult
    tool_call_id: str


class StepStarted(AgentEvent):
    """一步(一次模型往返)开始。"""

    type: Literal[EventType.STEP_STARTED] = EventType.STEP_STARTED
    index: int


class StepFinished(AgentEvent):
    """一步结束。"""

    type: Literal[EventType.STEP_FINISHED] = EventType.STEP_FINISHED
    index: int


class RunFinished(AgentEvent):
    """一次运行已终止，具体原因由 reason 表示，不代表任务成功。"""

    type: Literal[EventType.RUN_FINISHED] = EventType.RUN_FINISHED
    reason: str


class ErrorEvent(AgentEvent):
    """出错。"""

    type: Literal[EventType.ERROR] = EventType.ERROR
    message: str


class ToolExecutionCancelled(asyncio.CancelledError):
    """取消执行，同时携带已取得的观察"""

    def __init__(self, observation: ToolResult) -> None:
        super().__init__("工具执行被取消")
        self.observation = observation


AnyEvent = (
    TextDelta
    | AssistantMessage
    | ToolCallEvent
    | StepStarted
    | StepFinished
    | RunFinished
    | ErrorEvent
    | ToolResultEvent
)
