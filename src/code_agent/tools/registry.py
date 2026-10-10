"""工具系统(D):M2a 注册、schema 与统一执行结果接口。"""

import asyncio
import inspect
from typing import Any

from pydantic import ValidationError

from code_agent.events import ToolResult, ToolStatus
from code_agent.tools.spec import ToolSpec


class ToolRegistry:
    """管理工具,不调用模型、不追加历史、不决定任务结束。

    工具规格通过 register 注册,调用结果统一为 ToolResult。
    Agent 通过本接口生成工具定义并执行调用。
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        """注册工具;名称重复时抛出 ValueError,不静默替换。"""
        if spec.name in self._tools:
            raise ValueError("工具名称已存在")
        self._tools[spec.name] = spec

    def model_tools(self) -> list[dict[str, Any]]:
        """按注册顺序生成模型工具定义。"""
        res = list()
        for tool in self._tools.values():
            res.append(tool.to_schema())
        return res

    async def execute(self, name: str, arguments: str) -> ToolResult:
        """校验并执行一次调用,始终返回 ToolResult。

        未知工具、参数校验失败、执行失败返回 ERROR,原因放在 output 的 error 字段。
        参数校验和执行异常分开处理;校验失败不执行 handler。
        异步 handler/awaitable 正常 await,同步 handler 使用 asyncio.to_thread。
        既有 ToolResult 原样保留;旧字符串或其他 JSON 输出包装为 COMPLETED 结果。
        CancelledError 继续传播,不转换为普通错误。消息配对与 JSON 序列化留在核心。
        """
        if name not in self._tools:
            return ToolResult(output={"error": f"未注册的工具：{name}"}, status=ToolStatus.ERROR)

        spec = self._tools[name]
        try:
            args = spec.args_model.model_validate_json(arguments)
        except ValidationError as exc:
            return ToolResult(output={"error": f"工具参数错误：{exc}"}, status=ToolStatus.ERROR)

        try:
            handler = spec.handler
            kwargs = args.model_dump()

            if inspect.iscoroutinefunction(handler):
                result = await handler(**kwargs)
            else:
                result = await asyncio.to_thread(handler, **kwargs)

            if inspect.isawaitable(result):
                result = await result

            if isinstance(result, ToolResult):
                return result
            return ToolResult(output=result, status=ToolStatus.COMPLETED)
        except Exception as exc:
            return ToolResult(
                output={"error": f"工具执行错误：{type(exc).__name__}: {exc}"},
                status=ToolStatus.ERROR,
            )
