"""工具注册、参数校验与调用分发。"""

import asyncio
import inspect
from typing import Any

from pydantic import ValidationError

from code_agent.events import ToolResult, ToolStatus
from code_agent.tools.spec import ToolSpec


class ToolRegistry:
    """注册工具、生成模型工具定义，并分发经过参数校验的调用。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        """注册工具，名称重复时抛出 ValueError。"""
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
        """校验 JSON 参数并执行工具，返回统一的 ToolResult。

        未知工具、参数错误和执行异常返回 ERROR；取消异常继续传播。
        同步 handler 在线程中调用，异步调用和返回的 awaitable 会被等待。
        已有 ToolResult 原样返回，其他结果包装为 COMPLETED。
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
