"""工具规格与模型调用 schema。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, JsonValue

from code_agent.events import ToolResult

ToolOutput = ToolResult | JsonValue
ToolHandler = Callable[..., ToolOutput | Awaitable[ToolOutput]]


@dataclass(frozen=True)
class ToolSpec:
    """工具名称、说明、参数模型与执行函数。"""

    name: str
    description: str
    args_model: type[BaseModel]
    handler: ToolHandler

    def to_schema(self) -> dict[str, Any]:
        """生成 Chat Completions 工具定义，参数 schema 来自 args_model。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }
