"""Agent 运行循环，协调模型请求、工具执行和会话历史。"""

import asyncio
from collections.abc import Callable
from pathlib import Path

from code_agent.config import Settings
from code_agent.context.history import ConversationHistory
from code_agent.events import (
    AnyEvent,
    ErrorEvent,
    Role,
    RunFinished,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolExecutionCancelled,
    ToolResult,
    ToolResultEvent,
    ToolStatus,
)
from code_agent.llm.client import LLMClient
from code_agent.llm.streaming import TurnAssembler
from code_agent.safety.policy import resolve_execution_environment
from code_agent.tools.builtin import create_builtin_registry
from code_agent.tools.registry import ToolRegistry

SYSTEM_PROMPT = """
    你是一个编码助手，要尽力完成用户的任务

    当前执行环境：
    - 工作目录：{workspace_dir}
    - 命令解释器：Bash
    - 工具调用默认从上述目录来执行，优先使用相对路径
    - 每次调用启动独立进程，cd 和 export 的状态不会跨调用保留
    - 需要了解目录结构或文件内容时，使用工具检查

    可用工具:
    {tools}
"""


class Agent:
    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry | None = None,
        max_steps: int = 10,
        *,
        workspace_dir: Path,
        bash_path: str = "bash",
    ):
        self.workspace_dir = workspace_dir.expanduser().resolve()
        self.client = client
        self.max_steps = max_steps
        registry = (
            registry
            if registry is not None
            else create_builtin_registry(workspace_dir=self.workspace_dir, bash_path=bash_path)
        )
        self.registry = registry
        self.tools = registry.model_tools()
        tool_description = "\n".join(
            f"- {tool['function']['name']}: {tool['function']['description']}"
            for tool in self.tools
        )

        system_prompt = SYSTEM_PROMPT.format(
            workspace_dir=self.workspace_dir, tools=tool_description
        )
        self.history = ConversationHistory()
        self.history.add_message({"role": "system", "content": system_prompt})

    async def run(self, prompt: str, *, on_event: Callable[[AnyEvent], None] | None = None) -> str:
        def emit(event: AnyEvent) -> None:
            if on_event is not None:
                on_event(event)

        self.history.add_message({"role": "user", "content": prompt})
        for _ in range(self.max_steps):
            assembler = TurnAssembler()

            try:
                async for chunk in self.client.stream_turn(
                    self.history.get_model_messages(), tools=self.tools
                ):
                    text = assembler.push(chunk)
                    if text is not None:
                        emit(TextDelta(text=text))

                assembled_turn = assembler.finish()

                self.history.add_message(assembled_turn.message)
            except asyncio.CancelledError:
                emit(RunFinished(reason="cancelled"))
                raise
            except Exception as exc:
                emit(ErrorEvent(message=f"{type(exc).__name__}: {exc}"))
                emit(RunFinished(reason="error"))
                raise

            tool_calls = assembled_turn.message.get("tool_calls") or []
            for tool_call in tool_calls:
                try:
                    call = ToolCall(
                        id=tool_call["id"],
                        name=tool_call["function"]["name"],
                        arguments=tool_call["function"]["arguments"],
                    )
                    emit(ToolCallEvent(tool_call=call))

                    tool_result = await self.registry.execute(
                        tool_call["function"]["name"],
                        tool_call["function"]["arguments"],
                    )
                except asyncio.CancelledError as exc:
                    for pending_id in self.history.get_pending_calls():
                        is_current = pending_id == tool_call["id"]

                        if is_current and isinstance(exc, ToolExecutionCancelled):
                            observation = exc.observation
                        else:
                            observation = ToolResult(
                                status=ToolStatus.ERROR,
                                output={
                                    "reason": "interrupted",
                                    "execution": ("unknown" if is_current else "not_started"),
                                    "error": (
                                        "调用被中断，未取得可确认的执行结果"
                                        if is_current
                                        else "本轮已中断，此调用尚未启动"
                                    ),
                                },
                            )
                        self._append_tool_result(pending_id, observation)
                        emit(
                            ToolResultEvent(
                                tool_result=observation,
                                tool_call_id=pending_id,
                            )
                        )
                    emit(RunFinished(reason="cancelled"))
                    raise
                self._append_tool_result(tool_call["id"], tool_result)
                emit(ToolResultEvent(tool_result=tool_result, tool_call_id=tool_call["id"]))

            if tool_calls:
                continue

            if assembled_turn.finish_reason == "tool_calls":
                raise RuntimeError("模型工具调用，但没有返回调用信息")

            if assembled_turn.finish_reason == "stop" and tool_calls == []:
                emit(RunFinished(reason="completed"))
                return assembled_turn.message["content"] or ""

        emit(ErrorEvent(message=f"达到最大请求轮数：{self.max_steps}"))
        emit(RunFinished(reason="max_steps"))
        raise RuntimeError(f"达到最大请求轮数: {self.max_steps}")

    def _append_tool_result(self, call_id: str, result: ToolResult) -> None:
        self.history.add_message(
            {"role": Role.TOOL.value, "tool_call_id": call_id, "content": result.model_dump_json()}
        )


def create_agent(settings: Settings) -> Agent:
    """校验执行环境，并按应用配置创建 Agent。"""
    env = resolve_execution_environment(
        workspace_dir=settings.workspace_dir, bash_path=settings.bash_path
    )

    return Agent(
        LLMClient(settings),
        max_steps=settings.max_steps,
        workspace_dir=env.workspace_dir,
        bash_path=env.bash_path,
    )
