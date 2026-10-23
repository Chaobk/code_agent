"""工具注册、参数校验与调用分发测试。"""

import asyncio
import inspect
import json
import threading
from collections.abc import Awaitable
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from code_agent.events import ToolResult, ToolStatus
from code_agent.tools.registry import ToolRegistry
from code_agent.tools.spec import ToolHandler, ToolSpec
from tests.code_agent.helpers import allow_tool


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

    result = await registry.execute("echo", '{"text":"你好","repeat":0}', before_execute=allow_tool)

    assert result.status == ToolStatus.ERROR
    assert result.truncated is False
    assert isinstance(result.output, dict)
    assert "repeat" in result.output["error"]
    assert ToolResult.model_validate_json(result.model_dump_json()) == result
    handler.assert_not_called()


async def test_execute_returns_structured_error_for_unknown_tool() -> None:
    handler = Mock(return_value="不应执行")
    registry = _registry(handler)

    result = await registry.execute("missing", "{}", before_execute=allow_tool)

    assert isinstance(result, ToolResult)
    assert result.status == ToolStatus.ERROR
    assert "missing" in result.output["error"]
    handler.assert_not_called()


@pytest.mark.parametrize("output", ["文本结果", {"value": 3}, None])
async def test_execute_wraps_json_output_and_applies_parameter_defaults(output: JsonValue) -> None:
    handler = Mock(return_value=output)
    registry = _registry(handler)

    result = await registry.execute("echo", '{"text":"你好"}', before_execute=allow_tool)

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

    result = await _registry(handler).execute(
        "echo", '{"text":"同步","repeat":2}', before_execute=allow_tool
    )

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

    result = await _registry(handler).execute("echo", '{"text":"你好"}', before_execute=allow_tool)

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
    result = await _registry(handler).execute(
        "echo", '{"text":"异步","repeat":2}', before_execute=allow_tool
    )
    # 错误实现可能漏 await;关闭泄漏的 coroutine,避免测试留下无关警告。
    if inspect.iscoroutine(result):
        result.close()

    assert isinstance(result, ToolResult)
    assert result.output == "异步异步"
    assert calls == [("异步", 2, loop_thread)]


async def test_execute_propagates_internal_execution_error() -> None:
    error = RuntimeError("执行失败")
    handler = Mock(side_effect=error)

    with pytest.raises(RuntimeError) as caught:
        await _registry(handler).execute("echo", '{"text":"你好"}', before_execute=allow_tool)

    assert caught.value is error
    handler.assert_called_once_with(text="你好", repeat=1)


async def test_execute_propagates_handler_validation_failure() -> None:
    """只转换入参校验，工具内部的校验错误不能被降级。"""

    def handler(text: str, repeat: int) -> str:
        EchoArgs(text=text, repeat=0)
        return text

    with pytest.raises(ValidationError):
        await _registry(handler).execute("echo", '{"text":"有效输入"}', before_execute=allow_tool)


async def test_execute_propagates_cancellation() -> None:
    handler = AsyncMock(side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await _registry(handler).execute("echo", '{"text":"你好"}', before_execute=allow_tool)

    handler.assert_awaited_once()


@pytest.mark.parametrize(
    ("name", "arguments"),
    [("missing", "{}"), ("echo", "{"), ("echo", '{"text":"hello","repeat":0}')],
)
async def test_invalid_call_never_reaches_permission_hook(name: str, arguments: str) -> None:
    handler = Mock()
    before = AsyncMock(side_effect=AssertionError("无效调用不得进入审批"))

    result = await _registry(handler).execute(name, arguments, before_execute=before)

    assert result.status == ToolStatus.ERROR
    before.assert_not_awaited()
    handler.assert_not_called()


async def test_hook_sees_validated_defaults_but_cannot_mutate_execution_arguments() -> None:
    handler = Mock(return_value="done")

    async def before(name: str, args: dict[str, object]) -> None:
        assert name == "echo"
        assert args == {"text": "original", "repeat": 1}
        args["text"] = "changed"

    result = await _registry(handler).execute("echo", '{"text":"original"}', before_execute=before)

    assert result.output == "done"
    handler.assert_called_once_with(text="original", repeat=1)


async def test_hook_denial_returns_its_observation_without_invoking_handler() -> None:
    handler = Mock(side_effect=AssertionError("拒绝后不得执行"))
    denied = ToolResult(
        status=ToolStatus.ERROR,
        output={"reason": "permission_denied", "execution": "not_started", "error": "拒绝"},
    )
    before = AsyncMock(return_value=denied)

    result = await _registry(handler).execute("echo", '{"text":"hello"}', before_execute=before)

    assert result is denied
    before.assert_awaited_once_with("echo", {"text": "hello", "repeat": 1})
    handler.assert_not_called()


@pytest.mark.parametrize("error", [RuntimeError("审批内部错误"), asyncio.CancelledError()])
async def test_hook_failure_or_cancellation_propagates_without_execution(
    error: BaseException,
) -> None:
    handler = Mock(side_effect=AssertionError("审批未完成不得执行"))
    before = AsyncMock(side_effect=error)

    with pytest.raises(type(error)) as caught:
        await _registry(handler).execute("echo", '{"text":"hello"}', before_execute=before)

    assert caught.value is error
    handler.assert_not_called()
