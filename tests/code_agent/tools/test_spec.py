"""工具规格测试:模型定义必须可序列化,不包含本地执行对象。"""

import json
from unittest.mock import Mock

from pydantic import BaseModel, ConfigDict, Field

from code_agent.tools.spec import ToolSpec


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    repeat: int = Field(default=1, ge=1)


def test_to_schema_exposes_model_parameters_without_handler() -> None:
    handler = Mock(return_value="不应执行")
    spec = ToolSpec(name="echo", description="返回指定文本", args_model=EchoArgs, handler=handler)

    schema = spec.to_schema()

    assert schema["type"] == "function"
    function = schema["function"]
    assert function["name"] == "echo"
    assert function["description"] == "返回指定文本"
    parameters = function["parameters"]
    assert parameters["type"] == "object"
    assert parameters["required"] == ["text"]
    assert parameters["additionalProperties"] is False
    assert parameters["properties"]["repeat"]["default"] == 1
    assert parameters["properties"]["repeat"]["minimum"] == 1
    assert "handler" not in function
    assert "args_model" not in function
    assert json.loads(json.dumps(schema, ensure_ascii=False)) == schema
    handler.assert_not_called()
