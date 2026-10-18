"""内核:Agent 编排器。

M1 实现。这里是**唯一**把各能力串起来的地方:

    用户输入 ─► modes(决定下一轮请求)─► llm(调用模型)
                    ▲                        │
                    │                        ▼
              context(历史) ◄── tools(执行工具) ◄── events(推给界面)

跨能力业务连接集中在核心,UI 调用核心公开入口;
能力通过共享契约与传入接口协作,不引入循环依赖。
M2a 将当前演示入口的终端渲染依赖移出核心业务模块。
"""

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
        # 每个 Agent 保留自己的绑定,避免共享注册表被另一会话覆盖。
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
                # 由于取消导致的结束
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
                # 正常结束，补充信号
                emit(RunFinished(reason="completed"))
                return assembled_turn.message["content"] or ""

        # 轮数耗尽，先发信号
        emit(ErrorEvent(message=f"达到最大请求轮数：{self.max_steps}"))
        emit(RunFinished(reason="max_steps"))
        raise RuntimeError(f"达到最大请求轮数: {self.max_steps}")

    def _append_tool_result(self, call_id: str, result: ToolResult) -> None:
        self.history.add_message(
            {"role": Role.TOOL.value, "tool_call_id": call_id, "content": result.model_dump_json()}
        )


def create_agent(settings: Settings) -> Agent:
    """根据应用配置创建单次任务使用的 Agent(M1)。

    在核心中构造 LLMClient,将 max_steps、workspace_dir、bash_path 绑定到 Agent。
    CLI 调用此入口后 await agent.run(prompt),由事件回调实时显示运行过程。
    """
    # 先校验一下路径配置
    env = resolve_execution_environment(
        workspace_dir=settings.workspace_dir, bash_path=settings.bash_path
    )

    return Agent(
        LLMClient(settings),
        max_steps=settings.max_steps,
        workspace_dir=env.workspace_dir,
        bash_path=env.bash_path,
    )
