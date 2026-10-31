"""Agent 运行循环，协调模型请求、工具执行和会话历史。"""

from collections.abc import Callable, Mapping
from contextlib import aclosing
from pathlib import Path
from typing import Any

from openai import APIError

from code_agent.approval.gate import ApprovalGate
from code_agent.approval.policy import ApprovalPolicy
from code_agent.config import Settings
from code_agent.core.run_recording import record_run
from code_agent.events import (
    AnyEvent,
    Role,
    RunResult,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolExecutionCancelled,
    ToolResult,
    ToolResultEvent,
    ToolStatus,
)
from code_agent.llm.client import LLMClient
from code_agent.llm.streaming import AssembledTurn, ModelProtocolError, TurnAssembler
from code_agent.safety.policy import resolve_execution_environment
from code_agent.sessions.manager import SessionManager
from code_agent.tools.builtin import create_builtin_registry
from code_agent.tools.builtin.shell import BashApprovalPolicy
from code_agent.tools.registry import ToolRegistry

MODEL_ERRORS = (APIError, ModelProtocolError)

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
        approval_gate: ApprovalGate,
        approval_policies: Mapping[str, ApprovalPolicy],
        session_manager: SessionManager,
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
        self.session_manager = session_manager
        self.session_manager.append_message({"role": "system", "content": system_prompt})

        self.approval_gate = approval_gate
        self.approval_policies = dict(approval_policies)

    async def close(self) -> None:
        """会话或单次任务退出时释放模型客户端。"""
        await self.client.close()

    async def _before_tool_execute(
        self,
        name: str,
        args: dict[str, Any],
    ) -> ToolResult | None:
        """检查本次工具调用，返回结果标识阻止执行"""
        policy = self.approval_policies.get(name)

        if policy is None:
            return ToolResult(
                status=ToolStatus.ERROR,
                output={
                    "reason": "approval_unavailable",
                    "execution": "not_started",
                    "error": f"工具尚未配置审批策略：{name}",
                },
            )

        allowed, reason = self.approval_gate.check(
            tool_name=name,
            args=args,
            policy=policy,
        )

        if not allowed:
            return ToolResult(
                status=ToolStatus.ERROR,
                output={
                    "reason": "permission_denied",
                    "execution": "not_started",
                    "error": reason,
                },
            )

        return None

    @record_run
    async def run(
        self, prompt: str, *, on_event: Callable[[AnyEvent], None] | None = None
    ) -> RunResult:
        """提交用户消息并推进模型循环，工具批次自行执行和收尾。"""

        def emit(event: AnyEvent) -> None:
            if on_event is not None:
                on_event(event)

        self.session_manager.append_message({"role": "user", "content": prompt})
        for _ in range(self.max_steps):
            turn = await self._request_turn(emit)
            if isinstance(turn, RunResult):
                return turn

            tool_calls = turn.message.get("tool_calls") or []
            await self._execute_calls(turn.message, emit)

            if not tool_calls:
                return RunResult(status="completed", text=turn.message["content"] or "")

        return RunResult(status="max_steps", error=f"达到最大请求轮数：{self.max_steps}")

    async def _request_turn(self, emit: Callable[[AnyEvent], None]) -> AssembledTurn | RunResult:
        """请求失败只在模型边界转换；显示回调和历史错误不在捕获范围内。"""
        assembler = TurnAssembler()
        messages = self.session_manager.get_model_messages()
        async with aclosing(self.client.stream_turn(messages, tools=self.tools)) as stream:
            while True:
                try:
                    chunk = await anext(stream)
                    text = assembler.push(chunk)
                except StopAsyncIteration:
                    break
                except MODEL_ERRORS as exc:
                    return RunResult(status="failed", error=f"{type(exc).__name__}: {exc}")
                if text is not None:
                    emit(TextDelta(text=text))

        try:
            return assembler.finish()
        except ModelProtocolError as exc:
            return RunResult(status="failed", error=f"{type(exc).__name__}: {exc}")

    async def _execute_calls(
        self, message: dict[str, Any], emit: Callable[[AnyEvent], None]
    ) -> None:
        """提交助手消息并顺序执行调用，异常或取消时统一结清本批观察。"""
        current_call_id: str | None = None
        cancelled_observation: ToolResult | None = None
        try:
            self.session_manager.append_message(message)
            tool_calls = message.get("tool_calls") or []
            for tool_call in tool_calls:
                call = ToolCall(
                    id=tool_call["id"],
                    name=tool_call["function"]["name"],
                    arguments=tool_call["function"]["arguments"],
                )

                async def before_execute(
                    name: str, args: dict[str, Any], *, call_id: str = call.id
                ) -> ToolResult | None:
                    nonlocal current_call_id

                    blocked = await self._before_tool_execute(name, args)
                    if blocked is None:
                        current_call_id = call_id

                    return blocked

                emit(ToolCallEvent(tool_call=call))
                tool_result = await self.registry.execute(
                    call.name, call.arguments, before_execute=before_execute
                )
                self._append_tool_result(call.id, tool_result)
                current_call_id = None
                emit(ToolResultEvent(tool_result=tool_result, tool_call_id=call.id))
        except ToolExecutionCancelled as exc:
            cancelled_observation = exc.observation
            raise
        finally:
            self._settle_pending(current_call_id, cancelled_observation)

    def _settle_pending(
        self, current_call_id: str | None, observation: ToolResult | None = None
    ) -> None:
        """保留确定观察，补齐未知与未启动调用；收尾不再调用显示回调。"""
        for pending_id in self.session_manager.get_pending_calls():
            is_current = pending_id == current_call_id
            result = observation if is_current else None
            if result is None:
                result = ToolResult(
                    status=ToolStatus.ERROR,
                    output={
                        "reason": "interrupted",
                        "execution": "unknown" if is_current else "not_started",
                        "error": (
                            "运行已停止，未取得可确认的执行结果"
                            if is_current
                            else "运行已停止，此调用尚未启动"
                        ),
                    },
                )
            try:
                self._append_tool_result(pending_id, result)
            except Exception as exc:
                # Manager 已保留本条观察并停止落盘，继续结算其他调用。
                # 仅识别保存失败，保留批次正在传播的原始异常或取消。
                if exc is not self.session_manager.storage_error:
                    raise

    def _append_tool_result(self, call_id: str, result: ToolResult) -> None:
        self.session_manager.append_message(
            {"role": Role.TOOL.value, "tool_call_id": call_id, "content": result.model_dump_json()}
        )


def create_agent(
    settings: Settings,
    *,
    prompt_fn: Callable[[str], str] | None = None,
) -> Agent:
    """校验执行环境，并按应用配置创建 Agent。"""
    env = resolve_execution_environment(
        workspace_dir=settings.workspace_dir, bash_path=settings.bash_path
    )

    manager = SessionManager(env.workspace_dir, settings.sessions_dir)

    return Agent(
        LLMClient(settings),
        max_steps=settings.max_steps,
        workspace_dir=env.workspace_dir,
        bash_path=env.bash_path,
        approval_gate=ApprovalGate(prompt_fn=prompt_fn),
        approval_policies={
            "bash": BashApprovalPolicy(),
        },
        session_manager=manager,
    )
