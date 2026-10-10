"""工具系统(D):M2a 工具规格与模型 schema 接口。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, JsonValue

from code_agent.events import ToolResult

ToolOutput = ToolResult | JsonValue
ToolHandler = Callable[..., ToolOutput | Awaitable[ToolOutput]]


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的数据定义;执行环境由核心绑定到 handler,不暴露为模型参数。"""

    name: str
    description: str
    args_model: type[BaseModel]
    handler: ToolHandler

    def to_schema(self) -> dict[str, Any]:
        """返回 Chat Completions 的 function 工具定义(M2a 用户实现)。

        parameters 来自 args_model.model_json_schema();不包含 handler 等执行细节。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }
