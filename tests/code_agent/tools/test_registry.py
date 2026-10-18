"""工具注册、参数校验与调用分发测试。"""

import asyncio
import inspect
import json
import threading
from collections.abc import Awaitable
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from code_agent.events import ToolResult, ToolStatus
from code_agent.tools.registry import ToolRegistry
from code_agent.tools.spec import ToolHandler, ToolSpec


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    repeat: int = Field(default=1, ge=1)


def _registry(handler: ToolHandler) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="echo", description="返回指定文本", args_model=EchoArgs, handler=handler)
    )
    return registry


def test_model_tools_returns_empty_list_before_registration() -> None:
    assert ToolRegistry().model_tools() == []


def test_registered_tools_expose_model_schemas_in_registration_order() -> None:
    registry = ToolRegistry()
    handlers = [Mock(return_value="不应执行"), Mock(return_value="不应执行")]
    for name, handler in zip(("z_first", "a_second"), handlers, strict=True):
        registry.register(
            ToolSpec(name=name, description=f"工具 {name}", args_model=EchoArgs, handler=handler)
        )

    tools = registry.model_tools()

    assert len(tools) == 2
    assert [tool["function"]["name"] for tool in tools] == ["z_first", "a_second"]
    assert all(tool["type"] == "function" for tool in tools)
    assert all(tool["function"]["parameters"]["required"] == ["text"] for tool in tools)
    assert all("handler" not in tool["function"] for tool in tools)
    assert json.loads(json.dumps(tools)) == tools
    for handler in handlers:
        handler.assert_not_called()


def test_register_rejects_duplicate_name_and_preserves_original_spec() -> None:
    registry = ToolRegistry()
    original_handler = Mock(return_value="原工具")
    replacement_handler = Mock(return_value="替换工具")
    registry.register(
        ToolSpec(name="echo", description="原描述", args_model=EchoArgs, handler=original_handler)
    )

    with pytest.raises(ValueError):
        registry.register(
            ToolSpec(
                name="echo",
                description="替换描述",
                args_model=EchoArgs,
                handler=replacement_handler,
            )
        )

    tools = registry.model_tools()
    assert len(tools) == 1
    assert tools[0]["function"]["description"] == "原描述"
    original_handler.assert_not_called()
    replacement_handler.assert_not_called()


async def test_execute_returns_structured_validation_error_without_running_handler() -> None:
    """参数无效时返回错误结果，不调用 handler。"""
    handler = AsyncMock(return_value=ToolResult(output="不应执行", status=ToolStatus.COMPLETED))
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="echo", description="返回指定文本", args_model=EchoArgs, handler=handler)
    )

    result = await registry.execute("echo", '{"text":"你好","repeat":0}')

    assert result.status == ToolStatus.ERROR
    assert result.truncated is False
    assert isinstance(result.output, dict)
    assert "repeat" in result.output["error"]
    assert ToolResult.model_validate_json(result.model_dump_json()) == result
    handler.assert_not_called()


async def test_execute_returns_structured_error_for_unknown_tool() -> None:
    handler = Mock(return_value="不应执行")
    registry = _registry(handler)

    result = await registry.execute("missing", "{}")

    assert isinstance(result, ToolResult)
    assert result.status == ToolStatus.ERROR
    assert "missing" in result.output["error"]
    handler.assert_not_called()


@pytest.mark.parametrize("output", ["文本结果", {"value": 3}, None])
async def test_execute_wraps_json_output_and_applies_parameter_defaults(output: JsonValue) -> None:
    handler = Mock(return_value=output)
    registry = _registry(handler)

    result = await registry.execute("echo", '{"text":"你好"}')

    assert isinstance(result, ToolResult)
    assert result.output == output
    assert result.status == ToolStatus.COMPLETED
    assert result.truncated is False
    assert ToolResult.model_validate_json(result.model_dump_json()) == result
    handler.assert_called_once_with(text="你好", repeat=1)


async def test_execute_runs_sync_handler_outside_event_loop_thread() -> None:
    loop_thread = threading.get_ident()
    execution_threads: list[int] = []

    def handler(text: str, repeat: int) -> str:
        execution_threads.append(threading.get_ident())
        return text * repeat

    result = await _registry(handler).execute("echo", '{"text":"同步","repeat":2}')

    assert isinstance(result, ToolResult)
    assert result.output == "同步同步"
    assert len(execution_threads) == 1
    assert execution_threads[0] != loop_thread


async def test_execute_preserves_existing_tool_result_status_and_truncation() -> None:
    observation = ToolResult(
        output={"stdout": "部分输出", "stderr": "", "exit_code": None},
        status=ToolStatus.TIMED_OUT,
        truncated=True,
    )
    handler = AsyncMock(return_value=observation)

    result = await _registry(handler).execute("echo", '{"text":"你好"}')

    assert isinstance(result, ToolResult)
    assert result == observation
    handler.assert_awaited_once_with(text="你好", repeat=1)


@pytest.mark.parametrize("kind", ["async-callable", "sync-wrapper"])
async def test_execute_awaits_handler_result_before_returning(kind: str) -> None:
    calls: list[tuple[str, int, int]] = []
    loop_thread = threading.get_ident()

    async def work(text: str, repeat: int) -> ToolResult:
        calls.append((text, repeat, threading.get_ident()))
        return ToolResult(output=text * repeat, status=ToolStatus.COMPLETED)

    def work_wrapper(text: str, repeat: int) -> Awaitable[ToolResult]:
        return work(text, repeat)

    class AsyncEcho:
        async def __call__(self, text: str, repeat: int) -> ToolResult:
            return await work(text, repeat)

    handler = AsyncEcho() if kind == "async-callable" else work_wrapper
    result = await _registry(handler).execute("echo", '{"text":"异步","repeat":2}')
    # 错误实现可能漏 await;关闭泄漏的 coroutine,避免测试留下无关警告。
    if inspect.iscoroutine(result):
        result.close()

    assert isinstance(result, ToolResult)
    assert result.output == "异步异步"
    assert calls == [("异步", 2, loop_thread)]


async def test_execute_returns_structured_execution_error() -> None:
    handler = Mock(side_effect=RuntimeError("执行失败"))

    result = await _registry(handler).execute("echo", '{"text":"你好"}')

    assert isinstance(result, ToolResult)
    assert result.status == ToolStatus.ERROR
    assert "RuntimeError" in result.output["error"]
    assert "执行失败" in result.output["error"]
    handler.assert_called_once_with(text="你好", repeat=1)


async def test_execute_classifies_handler_validation_failure_as_execution_error() -> None:
    """已通过输入校验后,工具内部的校验异常属于执行错误。"""

    def handler(text: str, repeat: int) -> str:
        EchoArgs(text=text, repeat=0)
        return text

    result = await _registry(handler).execute("echo", '{"text":"有效输入"}')

    assert result.status == ToolStatus.ERROR
    assert "工具执行错误" in result.output["error"]
    assert "ValidationError" in result.output["error"]


async def test_execute_propagates_cancellation() -> None:
    handler = AsyncMock(side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await _registry(handler).execute("echo", '{"text":"你好"}')

    handler.assert_awaited_once()
