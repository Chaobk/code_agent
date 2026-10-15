"""Agent 编排单测：使用 FakeLLM 和假工具，不访问模型或文件系统。"""

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.utils import ModelResponse, ModelResponseStream
from pydantic import BaseModel, ConfigDict, Field

from code_agent.config import Settings
from code_agent.core.agent import Agent
from code_agent.events import (
    AnyEvent,
    ErrorEvent,
    RunFinished,
    TextDelta,
    ToolCallEvent,
    ToolExecutionCancelled,
    ToolResult,
    ToolResultEvent,
    ToolStatus,
)
from code_agent.llm import client as client_module
from code_agent.tools import builtin as builtin_module
from code_agent.tools.builtin import shell as shell_module
from code_agent.tools.registry import ToolRegistry
from code_agent.tools.spec import ToolSpec


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    repeat: int = Field(default=1, ge=1, le=3)


class FakeLLM:
    """依次产出预设流，完整响应转为分片，保存请求当时的快照。"""

    def __init__(
        self,
        responses: list[ModelResponse | BaseException | list[ModelResponseStream | BaseException]],
    ) -> None:
        self._responses = iter(responses)
        self.requests: list[dict[str, Any]] = []

    async def stream_turn(
        self, messages: list[dict[str, Any]], **kwargs: Any
    ) -> AsyncIterator[ModelResponseStream]:
        self.requests.append(deepcopy({"messages": messages, **kwargs}))
        try:
            response = next(self._responses)
        except StopIteration as exc:
            raise AssertionError("Agent 发起了预期之外的模型请求") from exc
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, ModelResponse):
            choice = response.choices[0]
            message = choice.message
            delta: dict[str, Any] = {"role": "assistant", "content": message.content}
            if message.tool_calls:
                delta["tool_calls"] = [
                    {"index": index, **call.model_dump()}
                    for index, call in enumerate(message.tool_calls)
                ]
            response = [
                ModelResponseStream(
                    choices=[{"delta": delta, "finish_reason": choice.finish_reason}]
                )
            ]
        for chunk in response:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


def assistant_response(content: str | None) -> ModelResponse:
    """正常结束且没有工具调用的助手响应。"""
    return ModelResponse(
        choices=[
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ]
    )


def final_response(content: str | None = "任务完成") -> ModelResponse:
    """最终响应通过正常结束和无工具调用表达收尾，不附加文本标记。"""
    return assistant_response(content)


def tool_call(
    call_id: str = "call_1", name: str = "echo", arguments: str = '{"text":"你好"}'
) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def tool_response(
    calls: list[dict[str, Any]], *, finish_reason: str = "tool_calls"
) -> ModelResponse:
    return ModelResponse(
        choices=[
            {
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": None, "tool_calls": calls},
            }
        ]
    )


def _assert_tool_result_is_committed(agent: Agent, event: ToolResultEvent) -> None:
    """在回调现场核实结果已经写入历史，并解除该调用的 pending 状态。"""
    message = agent.history.get_messages()[-1]
    assert message["role"] == "tool"
    assert message["tool_call_id"] == event.tool_call_id
    assert ToolResult.model_validate_json(message["content"]) == event.tool_result
    assert event.tool_call_id not in agent.history.get_pending_calls()


def _assert_run_finished(events: list[AnyEvent], reason: str) -> None:
    """每次运行只结束一次，错误详情先于最后的结束事件。"""
    finished = [event for event in events if isinstance(event, RunFinished)]
    assert len(finished) == 1
    assert events[-1] is finished[0]
    assert finished[0].reason == reason
    errors = [event for event in events if isinstance(event, ErrorEvent)]
    if reason in {"error", "max_steps"}:
        assert len(errors) == 1
        assert events[-2] is errors[0]
        assert errors[0].message
    else:
        assert errors == []


@pytest.fixture(autouse=True)
def block_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("acompletion", "aresponses"):
        monkeypatch.setattr(
            client_module.litellm,
            name,
            AsyncMock(side_effect=AssertionError("Agent 单测不得访问真实模型")),
        )


@pytest.fixture
def handler() -> Mock:
    return Mock(side_effect=lambda text, repeat=1: text * repeat)


@pytest.fixture
def registry(handler: Mock) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "返回指定文本，可重复多次", EchoArgs, handler))
    return registry


async def test_run_returns_final_answer_and_sends_serializable_tools(
    registry: ToolRegistry, handler: Mock
) -> None:
    client = FakeLLM([final_response("最终答案")])
    agent = Agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("回答问题") == "最终答案"

    assert len(client.requests) == 1
    request = client.requests[0]
    assert [message["role"] for message in request["messages"]] == ["system", "user"]
    assert request["messages"][-1]["content"] == "回答问题"
    assert "echo" in request["messages"][0]["content"]
    function = request["tools"][0]["function"]
    assert request["tools"][0]["type"] == "function"
    assert function["name"] == "echo"
    assert function["description"] == "返回指定文本，可重复多次"
    assert function["parameters"]["required"] == ["text"]
    assert function["parameters"]["properties"]["repeat"]["default"] == 1
    assert "handler" not in function
    assert json.loads(json.dumps(request["tools"])) == request["tools"]
    assert agent.history.get_model_messages()[-1]["role"] == "assistant"
    assert agent.history.get_model_messages()[-1]["content"] == "最终答案"
    handler.assert_not_called()


@pytest.mark.parametrize(
    "content",
    ["我先检查相关代码。", "示例中使用 END_TASK 标记。", "NOT_END_TASK", "", None],
    ids=["intermediate-text", "quoted-marker", "marker-substring", "empty", "null"],
)
@pytest.mark.parametrize("empty_calls", [None, []], ids=["null-calls", "empty-calls"])
async def test_run_finishes_on_normal_response_without_tool_calls(
    registry: ToolRegistry,
    handler: Mock,
    content: str | None,
    empty_calls: list[dict[str, Any]] | None,
) -> None:
    """无工具调用的正常回复立即返回，正文含义和旧标记不改变结束判断。"""
    response = assistant_response(content)
    # LiteLLM 构造器会将 [] 归一化为 None，显式保留空列表以覆盖两种表示。
    response.choices[0].message.tool_calls = empty_calls
    client = FakeLLM([response])
    agent = Agent(client, registry, max_steps=2, workspace_dir=Path.cwd())

    assert await agent.run("执行任务") == (content or "")

    assert len(client.requests) == 1
    history = agent.history.get_model_messages()
    assert [message["role"] for message in history] == ["system", "user", "assistant"]
    assert history[-1]["content"] == (content or "")
    handler.assert_not_called()


async def test_run_preserves_final_text_including_whitespace_and_old_marker(
    registry: ToolRegistry, handler: Mock
) -> None:
    """最终正文原样返回，旧标记只是文本，不删除末行或空白。"""
    content = "  已完成修改。\n示例：END_TASK\nEND_TASK\n\n"
    client = FakeLLM([assistant_response(content)])
    agent = Agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("执行任务") == content

    assert len(client.requests) == 1
    assert agent.history.get_model_messages()[-1]["content"] == content
    handler.assert_not_called()


async def test_run_returns_first_text_only_reply_without_requesting_another_turn(
    registry: ToolRegistry, handler: Mock
) -> None:
    """正常且无工具调用的回复立即结束，不需要文本标记。"""
    client = FakeLLM(
        [
            assistant_response("还在分析。"),
            assistant_response("继续分析。"),
        ]
    )
    agent = Agent(client, registry, max_steps=2, workspace_dir=Path.cwd())

    assert await agent.run("执行任务") == "还在分析。"
    assert len(client.requests) == 1
    handler.assert_not_called()


async def test_run_accepts_final_answer_on_last_allowed_request(
    registry: ToolRegistry, handler: Mock
) -> None:
    """最后一个允许的请求若正常收尾，应返回答案而不是步数上限错误。"""
    client = FakeLLM([tool_response([tool_call()]), final_response("任务完成")])
    agent = Agent(client, registry, max_steps=2, workspace_dir=Path.cwd())

    assert await agent.run("执行任务") == "任务完成"

    assert len(client.requests) == 2
    handler.assert_called_once_with(text="你好", repeat=1)


@pytest.mark.parametrize("content", ["", None])
async def test_run_returns_string_for_empty_final_content(
    registry: ToolRegistry, content: str | None
) -> None:
    client = FakeLLM([final_response(content)])

    assert await Agent(client, registry, workspace_dir=Path.cwd()).run("回答问题") == ""
    assert len(client.requests) == 1


@pytest.mark.parametrize("finish_reason", ["tool_calls", "stop"])
@pytest.mark.parametrize("content", ["我先执行工具。", "任务已经完成。\nEND_TASK"])
async def test_run_replays_tool_result_before_returning_final_answer(
    registry: ToolRegistry,
    handler: Mock,
    finish_reason: str,
    content: str,
) -> None:
    """同轮文本保存在历史中;只要有工具调用,就必须先回灌结果再收尾。"""
    call = tool_call(arguments='{"text":"工具观察"}')
    response = tool_response([call], finish_reason=finish_reason)
    response.choices[0].message.content = content
    client = FakeLLM([response, final_response("已根据工具结果确认完成")])
    agent = Agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("调用工具并确认结果") == "已根据工具结果确认完成"

    assert len(client.requests) == 2
    history = client.requests[1]["messages"]
    assert history[-2]["content"] == content
    assert history[-2]["tool_calls"] == [call]
    assert history[-1] == {
        "role": "tool",
        "tool_call_id": call["id"],
        "content": ToolResult(output="工具观察", status=ToolStatus.COMPLETED).model_dump_json(),
    }
    handler.assert_called_once_with(text="工具观察", repeat=1)


async def test_run_replays_tool_failure_despite_same_turn_completion_claim(
    registry: ToolRegistry, handler: Mock
) -> None:
    """执行前生成的完成声明不能掩盖实际工具失败。"""
    handler.side_effect = RuntimeError("执行失败")
    response = tool_response([tool_call()])
    response.choices[0].message.content = "工具执行成功。\nEND_TASK"
    client = FakeLLM([response, final_response("工具失败,需要说明原因")])
    agent = Agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("调用工具") == "工具失败,需要说明原因"

    assert len(client.requests) == 2
    result = client.requests[1]["messages"][-1]
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "call_1"
    assert "工具执行错误" in result["content"]
    assert "执行失败" in result["content"]
    handler.assert_called_once()


async def test_run_includes_resolved_workspace_in_system_prompt(
    registry: ToolRegistry,
) -> None:
    workspace = Path.cwd() / "sample-workspace"
    client = FakeLLM([final_response()])
    agent = Agent(client, registry, workspace_dir=workspace / "child" / "..")

    await agent.run("查看工作空间")

    prompt = client.requests[0]["messages"][0]["content"]
    assert agent.workspace_dir == workspace.resolve()
    assert f"工作目录：{workspace.resolve()}" in prompt
    assert "命令解释器：Bash" in prompt
    assert "cd 和 export 的状态不会跨调用保留" in prompt
    assert "END_TASK" not in prompt


async def test_default_bash_uses_configured_environment_and_exposes_only_command_parameters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None, workspace_dir=tmp_path / "child" / "..", bash_path="configured-bash"
    )
    stdout = asyncio.StreamReader()
    stdout.feed_data(b"workspace output\n")
    stdout.feed_eof()
    stderr = asyncio.StreamReader()
    stderr.feed_eof()
    process = SimpleNamespace(
        pid=4321,
        returncode=0,
        stdout=stdout,
        stderr=stderr,
        wait=AsyncMock(return_value=0),
    )
    create_process = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(shell_module.psutil, "Process", Mock())
    client = FakeLLM(
        [tool_response([tool_call(name="bash", arguments='{"command":"pwd"}')]), final_response()]
    )
    agent = Agent(client, workspace_dir=settings.workspace_dir, bash_path=settings.bash_path)

    await agent.run("查看当前目录")

    function = client.requests[0]["tools"][0]["function"]
    assert function["name"] == "bash"
    assert set(function["parameters"]["properties"]) == {"command", "timeout_seconds"}
    assert function["parameters"]["required"] == ["command"]
    assert str(tmp_path.resolve()) in client.requests[0]["messages"][0]["content"]
    call = create_process.await_args
    assert call.args == ("configured-bash", "--noprofile", "--norc", "-c", "pwd")
    assert call.kwargs["cwd"] == agent.workspace_dir == tmp_path.resolve()
    result = json.loads(client.requests[1]["messages"][-1]["content"])
    assert result["status"] == "completed"
    assert result["output"]["stdout"] == "workspace output\n"


@pytest.mark.parametrize("extra", [{"workspace_dir": "/other"}, {"bash_path": "other-bash"}])
async def test_default_bash_rejects_model_environment_overrides(
    extra: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_process = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    arguments = json.dumps({"command": "pwd", **extra})
    client = FakeLLM(
        [tool_response([tool_call(name="bash", arguments=arguments)]), final_response()]
    )

    await Agent(client, workspace_dir=Path.cwd()).run("查看目录")

    result = ToolResult.model_validate_json(client.requests[1]["messages"][-1]["content"])
    assert result.status == ToolStatus.ERROR
    assert "工具参数错误" in result.output["error"]
    create_process.assert_not_awaited()


async def test_default_bash_bindings_are_independent_between_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations: list[tuple[Path, str]] = []

    async def handler(
        command: str, timeout_seconds: int, *, workspace_dir: Path, bash_path: str
    ) -> ToolResult:
        observations.append((workspace_dir, bash_path))
        return ToolResult(output=command, status=ToolStatus.COMPLETED)

    monkeypatch.setattr(builtin_module, "run_bash", handler)
    agents = []
    for name in ("first", "second"):
        client = FakeLLM(
            [
                tool_response([tool_call(name="bash", arguments='{"command":"pwd"}')]),
                final_response(),
            ]
        )
        agents.append(Agent(client, workspace_dir=Path(name), bash_path=f"{name}-bash"))

    for agent in agents:
        await agent.run("查看目录")

    assert observations == [
        (Path("first").resolve(), "first-bash"),
        (Path("second").resolve(), "second-bash"),
    ]
    assert builtin_module.run_bash is handler


async def test_run_executes_tool_and_replays_call_and_result(
    registry: ToolRegistry, handler: Mock
) -> None:
    call = tool_call(arguments='{"text":"你好","repeat":2}')
    client = FakeLLM([tool_response([call]), final_response("收到工具结果")])
    agent = Agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("使用工具") == "收到工具结果"

    handler.assert_called_once_with(text="你好", repeat=2)
    assert len(client.requests) == 2
    assert len(client.requests[0]["messages"]) == 2
    second_request = client.requests[1]
    assert second_request["tools"] == client.requests[0]["tools"]
    history = second_request["messages"]
    assert [message["role"] for message in history] == ["system", "user", "assistant", "tool"]
    assert history[-2]["content"] == ""
    assert history[-2]["tool_calls"] == [call]
    assert history[-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": ToolResult(output="你好你好", status=ToolStatus.COMPLETED).model_dump_json(),
    }


async def test_run_awaits_async_tool_and_serializes_result() -> None:
    event_loop_thread = threading.get_ident()

    async def handler(text: str, repeat: int) -> ToolResult:
        assert threading.get_ident() == event_loop_thread
        await asyncio.sleep(0)
        return ToolResult(output={"text": text * repeat}, status=ToolStatus.COMPLETED)

    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "异步回显", EchoArgs, handler))
    client = FakeLLM([tool_response([tool_call()]), final_response("收到结果")])

    assert await Agent(client, registry, workspace_dir=Path.cwd()).run("调用异步工具") == "收到结果"

    content = client.requests[1]["messages"][-1]["content"]
    assert isinstance(content, str)
    assert json.loads(content) == {
        "output": {"text": "你好"},
        "status": "completed",
        "truncated": False,
    }


async def test_run_replays_all_tool_results_before_next_request(
    registry: ToolRegistry, handler: Mock
) -> None:
    calls = [
        tool_call("call_first", arguments='{"text":"第一"}'),
        tool_call("call_second", arguments='{"text":"第二","repeat":2}'),
    ]
    client = FakeLLM([tool_response(calls), final_response()])

    await Agent(client, registry, workspace_dir=Path.cwd()).run("调用两个工具")

    assert len(client.requests) == 2
    assert [call.kwargs for call in handler.call_args_list] == [
        {"text": "第一", "repeat": 1},
        {"text": "第二", "repeat": 2},
    ]
    history = client.requests[1]["messages"]
    assert history[-3]["tool_calls"] == calls
    assert history[-2:] == [
        {
            "role": "tool",
            "tool_call_id": "call_first",
            "content": ToolResult(output="第一", status=ToolStatus.COMPLETED).model_dump_json(),
        },
        {
            "role": "tool",
            "tool_call_id": "call_second",
            "content": ToolResult(output="第二第二", status=ToolStatus.COMPLETED).model_dump_json(),
        },
    ]


async def test_run_returns_unknown_tool_error_to_model(
    registry: ToolRegistry, handler: Mock
) -> None:
    client = FakeLLM([tool_response([tool_call(name="unknown")]), final_response()])

    await Agent(client, registry, workspace_dir=Path.cwd()).run("调用工具")

    result = client.requests[1]["messages"][-1]
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "call_1"
    observation = ToolResult.model_validate_json(result["content"])
    assert observation.status == ToolStatus.ERROR
    assert "未注册的工具" in observation.output["error"]
    assert "unknown" in observation.output["error"]
    handler.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    ["{invalid json", '{"repeat":2}', '{"text":"你好","repeat":0}', '{"text":"你好","extra":1}'],
    ids=["invalid_json", "missing_required", "invalid_range", "unknown_parameter"],
)
async def test_run_returns_argument_validation_error_without_executing_tool(
    registry: ToolRegistry, handler: Mock, arguments: str
) -> None:
    client = FakeLLM([tool_response([tool_call(arguments=arguments)]), final_response()])

    await Agent(client, registry, workspace_dir=Path.cwd()).run("调用工具")

    result = client.requests[1]["messages"][-1]
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "call_1"
    observation = ToolResult.model_validate_json(result["content"])
    assert observation.status == ToolStatus.ERROR
    assert "工具参数错误" in observation.output["error"]
    handler.assert_not_called()


@pytest.mark.parametrize("error", [FileNotFoundError("missing.txt"), RuntimeError("工具失败")])
async def test_run_returns_tool_execution_error_to_model(
    registry: ToolRegistry, handler: Mock, error: Exception
) -> None:
    handler.side_effect = error
    client = FakeLLM([tool_response([tool_call()]), final_response("已处理错误")])

    assert await Agent(client, registry, workspace_dir=Path.cwd()).run("调用工具") == "已处理错误"

    result = client.requests[1]["messages"][-1]
    assert result["tool_call_id"] == "call_1"
    observation = ToolResult.model_validate_json(result["content"])
    assert observation.status == ToolStatus.ERROR
    assert "工具执行错误" in observation.output["error"]
    assert type(error).__name__ in observation.output["error"]
    assert str(error) in observation.output["error"]
    handler.assert_called_once_with(text="你好", repeat=1)


async def test_run_stops_at_max_steps(registry: ToolRegistry, handler: Mock) -> None:
    client = FakeLLM(
        [tool_response([tool_call(f"call_{index}")]) for index in range(3)]
        + [final_response("不应请求这一轮")]
    )
    agent = Agent(client, registry, max_steps=3, workspace_dir=Path.cwd())
    events: list[AnyEvent] = []

    with pytest.raises(RuntimeError) as caught:
        await agent.run("重复调用工具", on_event=events.append)

    _assert_run_finished(events, "max_steps")
    assert isinstance(events[-3], ToolResultEvent)
    assert isinstance(events[-2], ErrorEvent)
    assert "达到最大请求轮数" in events[-2].message
    assert "3" in events[-2].message
    assert "3" in str(caught.value)
    assert len(client.requests) == 3
    assert handler.call_count == 3
    assert (
        len(
            [message for message in agent.history.get_model_messages() if message["role"] == "tool"]
        )
        == 3
    )


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
@pytest.mark.parametrize("has_tool_calls", [False, True], ids=["text", "tools"])
async def test_run_rejects_abnormal_response_without_executing_tool(
    registry: ToolRegistry, handler: Mock, finish_reason: str, has_tool_calls: bool
) -> None:
    response = tool_response([tool_call()] if has_tool_calls else [], finish_reason=finish_reason)
    response.choices[0].message.content = "任务完成"
    client = FakeLLM([response])
    agent = Agent(client, registry, workspace_dir=Path.cwd())
    events: list[AnyEvent] = []

    with pytest.raises(RuntimeError, match=finish_reason):
        await agent.run("调用工具", on_event=events.append)

    _assert_run_finished(events, "error")
    assert len(client.requests) == 1
    assert [message["role"] for message in agent.history.get_model_messages()] == ["system", "user"]
    handler.assert_not_called()


@pytest.mark.parametrize("content", [None, "总结\nEND_TASK"])
@pytest.mark.parametrize("empty_calls", [None, []], ids=["null-calls", "empty-calls"])
async def test_run_rejects_tool_finish_without_calls(
    registry: ToolRegistry,
    handler: Mock,
    content: str | None,
    empty_calls: list[dict[str, Any]] | None,
) -> None:
    response = tool_response([])
    response.choices[0].message.content = content
    response.choices[0].message.tool_calls = empty_calls
    client = FakeLLM([response])

    with pytest.raises(RuntimeError, match="没有返回调用信息"):
        await Agent(client, registry, workspace_dir=Path.cwd()).run("调用工具")

    assert len(client.requests) == 1
    handler.assert_not_called()


async def test_run_propagates_model_request_error(registry: ToolRegistry, handler: Mock) -> None:
    error = RuntimeError("模型请求失败")
    client = FakeLLM([error])
    events: list[AnyEvent] = []

    with pytest.raises(RuntimeError, match="模型请求失败") as caught:
        await Agent(client, registry, workspace_dir=Path.cwd()).run(
            "回答问题", on_event=events.append
        )

    _assert_run_finished(events, "error")
    assert isinstance(events[-2], ErrorEvent)
    assert events[-2].message == "RuntimeError: 模型请求失败"
    assert caught.value is error
    assert len(client.requests) == 1
    handler.assert_not_called()


async def test_run_propagates_tool_cancellation(registry: ToolRegistry, handler: Mock) -> None:
    handler.side_effect = asyncio.CancelledError()
    client = FakeLLM([tool_response([tool_call()]), final_response("不应继续请求")])
    agent = Agent(client, registry, workspace_dir=Path.cwd())

    with pytest.raises(asyncio.CancelledError):
        await agent.run("调用工具")

    assert len(client.requests) == 1
    handler.assert_called_once()
    assert agent.history.get_pending_calls() == {}

    messages = agent.history.get_model_messages()
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "tool"]
    assert messages[-1]["tool_call_id"] == "call_1"

    observation = ToolResult.model_validate_json(messages[-1]["content"])
    assert observation.status == ToolStatus.ERROR
    assert isinstance(observation.output, dict)
    assert observation.output["reason"] == "interrupted"
    assert observation.output["execution"] == "unknown"


async def test_run_emits_tool_results_after_commit_in_execution_order() -> None:
    observations = [
        ToolResult(
            output={"stdout": "测试失败[bold]\n", "stderr": "", "exit_code": 1},
            status=ToolStatus.COMPLETED,
            truncated=True,
        ),
        ToolResult(
            output={"stdout": "部分输出", "stderr": "", "exit_code": None},
            status=ToolStatus.TIMED_OUT,
        ),
    ]
    events: list[ToolCallEvent | ToolResultEvent] = []
    executed: list[str] = []

    async def execute(text: str, repeat: int = 1) -> ToolResult:
        # 调用事件必须先于 handler；上一个结果必须在下一个调用启动前送达。
        index = len(executed)
        assert len(events) == index * 2 + 1
        assert isinstance(events[-1], ToolCallEvent)
        assert events[-1].tool_call.id == f"call_{index}"
        executed.append(text)
        return observations[index]

    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "Return text", EchoArgs, execute))
    calls = [
        tool_call(f"call_{index}", arguments=json.dumps({"text": text}))
        for index, text in enumerate(["first", "second"])
    ]
    client = FakeLLM([tool_response(calls), final_response("已处理观察")])
    agent = Agent(client, registry, workspace_dir=Path.cwd())

    def on_event(event: AnyEvent) -> None:
        if isinstance(event, ToolCallEvent):
            assert event.tool_call.id in agent.history.get_pending_calls()
            assert event.tool_call.model_dump() == {
                "id": calls[len(executed)]["id"],
                **calls[len(executed)]["function"],
            }
            events.append(event)
        elif isinstance(event, ToolResultEvent):
            _assert_tool_result_is_committed(agent, event)
            assert len(client.requests) == 1
            events.append(event)

    assert await agent.run("执行两个工具", on_event=on_event) == "已处理观察"

    assert executed == ["first", "second"]
    assert [type(event) for event in events] == [
        ToolCallEvent,
        ToolResultEvent,
        ToolCallEvent,
        ToolResultEvent,
    ]
    results = [event for event in events if isinstance(event, ToolResultEvent)]
    assert [event.tool_call_id for event in results] == ["call_0", "call_1"]
    assert [event.tool_result for event in results] == observations
    assert [
        ToolResult.model_validate_json(message["content"])
        for message in client.requests[1]["messages"]
        if message["role"] == "tool"
    ] == observations


@pytest.mark.parametrize("content", ["最终答案[bold]\n", "", None], ids=["text", "empty", "null"])
async def test_run_emits_completed_after_final_text(
    registry: ToolRegistry, handler: Mock, content: str | None
) -> None:
    client = FakeLLM([final_response(content)])
    agent = Agent(client, registry, workspace_dir=Path.cwd())
    events: list[AnyEvent] = []

    assert await agent.run("回答问题", on_event=events.append) == (content or "")

    _assert_run_finished(events, "completed")
    assert "".join(event.text for event in events if isinstance(event, TextDelta)) == (
        content or ""
    )
    assert not any(isinstance(event, (ToolCallEvent, ToolResultEvent)) for event in events)
    handler.assert_not_called()


@pytest.mark.parametrize(
    ("name", "arguments", "handler_fails", "error_match"),
    [
        ("echo", '{"text":', False, "工具参数错误"),
        ("echo", '{"text":"你好","repeat":0}', False, "工具参数错误"),
        ("missing", '{"text":"你好"}', False, "未注册的工具"),
        ("echo", '{"text":"你好"}', True, "工具执行错误"),
    ],
    ids=["invalid-json", "invalid-arguments", "unknown-tool", "handler-error"],
)
async def test_run_emits_paired_error_observation_and_continues(
    registry: ToolRegistry,
    handler: Mock,
    name: str,
    arguments: str,
    handler_fails: bool,
    error_match: str,
) -> None:
    handler.side_effect = RuntimeError("执行失败[bold]")
    client = FakeLLM(
        [tool_response([tool_call("failed", name, arguments)]), final_response("已根据错误收尾")]
    )
    agent = Agent(client, registry, workspace_dir=Path.cwd())
    results: list[ToolResultEvent] = []
    events: list[AnyEvent] = []

    def on_event(event: AnyEvent) -> None:
        events.append(event)
        if isinstance(event, ToolResultEvent):
            _assert_tool_result_is_committed(agent, event)
            results.append(event)

    assert await agent.run("调用工具", on_event=on_event) == "已根据错误收尾"

    _assert_run_finished(events, "completed")
    assert len(results) == 1
    assert results[0].tool_call_id == "failed"
    observation = results[0].tool_result
    assert observation.status == ToolStatus.ERROR
    assert isinstance(observation.output, dict)
    assert error_match in observation.output["error"]
    assert ToolResult.model_validate_json(client.requests[1]["messages"][-1]["content"]) == (
        observation
    )
    if handler_fails:
        handler.assert_called_once_with(text="你好", repeat=1)
    else:
        handler.assert_not_called()


@pytest.mark.parametrize("cancel_index", [0, 1, 2], ids=["first", "middle", "last"])
async def test_run_pairs_cancelled_batch_and_can_resume(cancel_index: int) -> None:
    started = asyncio.Event()
    blocked = asyncio.Event()
    executed: list[str] = []
    texts = ["first", "second", "third"]

    async def execute(text: str, repeat: int = 1) -> str:
        executed.append(text)
        if text == texts[cancel_index]:
            started.set()
            await blocked.wait()
        return f"done:{text}"

    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "Return text", EchoArgs, execute))
    calls = [
        tool_call(f"call_{index}", arguments=json.dumps({"text": text}))
        for index, text in enumerate(texts)
    ]
    client = FakeLLM([tool_response(calls), final_response("resumed")])
    agent = Agent(client, registry, workspace_dir=Path.cwd())
    events: list[AnyEvent] = []

    def on_event(event: AnyEvent) -> None:
        if isinstance(event, ToolResultEvent):
            _assert_tool_result_is_committed(agent, event)
        events.append(event)

    task = asyncio.create_task(agent.run("execute batch", on_event=on_event))

    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert executed == texts[: cancel_index + 1]
    _assert_run_finished(events, "cancelled")
    assert len(client.requests) == 1
    assert agent.history.get_pending_calls() == {}
    history = agent.history.get_model_messages()
    results = [message for message in history if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in results] == [call["id"] for call in calls]
    assert [
        type(event) for event in events if isinstance(event, (ToolCallEvent, ToolResultEvent))
    ] == (
        [ToolCallEvent, ToolResultEvent] * cancel_index
        + [ToolCallEvent]
        + [ToolResultEvent] * (len(calls) - cancel_index)
    )
    assert [event.tool_call.id for event in events if isinstance(event, ToolCallEvent)] == [
        call["id"] for call in calls[: cancel_index + 1]
    ]
    result_events = [event for event in events if isinstance(event, ToolResultEvent)]
    assert [event.tool_call_id for event in result_events] == [call["id"] for call in calls]
    assert [event.tool_result for event in result_events] == [
        ToolResult.model_validate_json(message["content"]) for message in results
    ]

    for index, message in enumerate(results):
        observation = ToolResult.model_validate_json(message["content"])
        if index < cancel_index:
            assert observation.status == ToolStatus.COMPLETED
            assert observation.output == f"done:{texts[index]}"
        else:
            assert observation.status == ToolStatus.ERROR
            assert isinstance(observation.output, dict)
            assert observation.output["reason"] == "interrupted"
            expected = "unknown" if index == cancel_index else "not_started"
            assert observation.output["execution"] == expected

    assert await agent.run("continue", on_event=on_event) == "resumed"
    assert len(client.requests) == 2
    assert client.requests[1]["messages"] == [*history, {"role": "user", "content": "continue"}]
    assert executed == texts[: cancel_index + 1]
    assert len([event for event in events if isinstance(event, ToolResultEvent)]) == len(calls)
    assert [event.reason for event in events if isinstance(event, RunFinished)] == [
        "cancelled",
        "completed",
    ]


async def test_run_preserves_observation_from_cancelled_tool() -> None:
    observation = ToolResult(
        output={
            "reason": "interrupted",
            "stdout": "partial output",
            "stderr": "",
            "exit_code": None,
        },
        status=ToolStatus.ERROR,
        truncated=True,
    )
    cancellation = ToolExecutionCancelled(observation)
    handler = AsyncMock(side_effect=cancellation)
    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "Return text", EchoArgs, handler))
    client = FakeLLM(
        [
            tool_response(
                [
                    tool_call("current", arguments=json.dumps({"text": "first"})),
                    tool_call("remaining", arguments=json.dumps({"text": "second"})),
                ]
            )
        ]
    )
    agent = Agent(client, registry, workspace_dir=Path.cwd())
    result_events: list[ToolResultEvent] = []
    events: list[AnyEvent] = []

    def on_event(event: AnyEvent) -> None:
        events.append(event)
        if isinstance(event, ToolResultEvent):
            _assert_tool_result_is_committed(agent, event)
            result_events.append(event)

    with pytest.raises(ToolExecutionCancelled) as caught:
        await agent.run("execute batch", on_event=on_event)

    assert caught.value is cancellation
    _assert_run_finished(events, "cancelled")
    handler.assert_awaited_once_with(text="first", repeat=1)
    assert len(client.requests) == 1
    assert agent.history.get_pending_calls() == {}
    results = [
        message for message in agent.history.get_model_messages() if message["role"] == "tool"
    ]
    assert [message["tool_call_id"] for message in results] == ["current", "remaining"]
    assert ToolResult.model_validate_json(results[0]["content"]) == observation
    remaining = ToolResult.model_validate_json(results[1]["content"])
    assert remaining.status == ToolStatus.ERROR
    assert isinstance(remaining.output, dict)
    assert remaining.output["reason"] == "interrupted"
    assert remaining.output["execution"] == "not_started"
    assert [event.tool_call_id for event in result_events] == ["current", "remaining"]
    assert [event.tool_result for event in result_events] == [observation, remaining]


async def test_run_preserves_history_between_user_turns(
    registry: ToolRegistry, handler: Mock
) -> None:
    client = FakeLLM([final_response("第一轮答案"), final_response("第二轮答案")])
    agent = Agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("第一轮问题") == "第一轮答案"
    assert await agent.run("第二轮问题") == "第二轮答案"

    history = client.requests[1]["messages"]
    assert [message["role"] for message in history] == ["system", "user", "assistant", "user"]
    assert [message["content"] for message in history[1:]] == [
        "第一轮问题",
        "第一轮答案",
        "第二轮问题",
    ]
    handler.assert_not_called()


async def test_run_executes_sync_tool_outside_event_loop_thread(
    registry: ToolRegistry, handler: Mock
) -> None:
    event_loop_thread = threading.get_ident()
    executed_threads: list[int] = []

    def record_thread(text: str, repeat: int) -> str:
        executed_threads.append(threading.get_ident())
        return text * repeat

    handler.side_effect = record_thread
    client = FakeLLM([tool_response([tool_call()]), final_response()])

    await Agent(client, registry, workspace_dir=Path.cwd()).run("调用工具")

    assert len(executed_threads) == 1
    assert executed_threads[0] != event_loop_thread


def stream_chunk(
    content: str | None = None,
    *,
    calls: list[dict[str, Any]] | None = None,
    finish_reason: str | None = None,
) -> ModelResponseStream:
    """构造原始分片；参数是否完整由组装器和注册表分别判断。"""
    return ModelResponseStream(
        choices=[
            {
                "delta": {"content": content, "tool_calls": calls},
                "finish_reason": finish_reason,
            }
        ]
    )


async def test_run_assembles_text_and_consumes_trailing_usage(
    registry: ToolRegistry, handler: Mock
) -> None:
    client = FakeLLM(
        [
            [
                ModelResponseStream(choices=[{"delta": {"role": "assistant"}}]),
                stream_chunk("  分片"),
                stream_chunk("正文\n", finish_reason="stop"),
                ModelResponseStream(
                    choices=[],
                    usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                ),
            ]
        ]
    )
    agent = Agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("回答问题") == "  分片正文\n"

    assert len(client.requests) == 1
    assert agent.history.get_model_messages()[-1] == {
        "role": "assistant",
        "content": "  分片正文\n",
    }
    handler.assert_not_called()


async def test_run_executes_interleaved_calls_once_in_index_order(
    registry: ToolRegistry, handler: Mock
) -> None:
    client = FakeLLM(
        [
            [
                stream_chunk(
                    "准备执行",
                    calls=[
                        {"index": 1, **tool_call("second", arguments='{"text":')},
                        {"index": 0, **tool_call("first", arguments='{"text":')},
                    ],
                ),
                stream_chunk(calls=[{"index": 1, "function": {"arguments": '"第二"}'}}]),
                stream_chunk(
                    calls=[{"index": 0, "function": {"arguments": '"第一"}'}}],
                    finish_reason="tool_calls",
                ),
            ],
            final_response("观察已齐全"),
        ]
    )
    agent = Agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("执行两个工具") == "观察已齐全"

    assert [call.kwargs["text"] for call in handler.call_args_list] == ["第一", "第二"]
    assert len(client.requests) == 2
    messages = client.requests[1]["messages"]
    assert messages[-3]["content"] == "准备执行"
    assert [call["id"] for call in messages[-3]["tool_calls"]] == ["first", "second"]
    assert [message["tool_call_id"] for message in messages[-2:]] == ["first", "second"]
    assert agent.history.get_pending_calls() == {}


async def test_run_waits_for_stream_exhaustion_before_committing_or_executing(
    registry: ToolRegistry, handler: Mock
) -> None:
    at_tail = asyncio.Event()
    release = asyncio.Event()

    class TailLLM(FakeLLM):
        async def stream_turn(
            self, messages: list[dict[str, Any]], **kwargs: Any
        ) -> AsyncIterator[ModelResponseStream]:
            if not self.requests:
                self.requests.append(deepcopy({"messages": messages, **kwargs}))
                yield stream_chunk(
                    "尚未耗尽",
                    calls=[{"index": 0, **tool_call()}],
                    finish_reason="tool_calls",
                )
                at_tail.set()
                await release.wait()
                yield ModelResponseStream(choices=[])
            else:
                async for chunk in super().stream_turn(messages, **kwargs):
                    yield chunk

    client = TailLLM([final_response("完成")])
    agent = Agent(client, registry, workspace_dir=Path.cwd())
    task = asyncio.create_task(agent.run("调用工具"))
    try:
        await asyncio.wait_for(at_tail.wait(), timeout=5)
        assert [item["role"] for item in agent.history.get_model_messages()] == ["system", "user"]
        handler.assert_not_called()
        assert not task.done()
        release.set()
        assert await asyncio.wait_for(task, timeout=5) == "完成"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    handler.assert_called_once_with(text="你好", repeat=1)
    assert len(client.requests) == 2
    assert agent.history.get_pending_calls() == {}


@pytest.mark.parametrize("after_finish", [False, True], ids=["partial", "after-finish"])
@pytest.mark.parametrize("error_type", [RuntimeError, TimeoutError, asyncio.CancelledError])
async def test_run_discards_failed_stream_and_preserves_prior_observations(
    registry: ToolRegistry,
    handler: Mock,
    after_finish: bool,
    error_type: type[BaseException],
) -> None:
    error = error_type("流已中断")
    client = FakeLLM(
        [
            tool_response([tool_call("committed", arguments='{"text":"已取得的观察"}')]),
            [
                stream_chunk(
                    "临时正文不得进入历史",
                    calls=[{"index": 0, **tool_call("uncommitted")}],
                    finish_reason="tool_calls" if after_finish else None,
                ),
                error,
            ],
            final_response("继续成功"),
        ]
    )
    agent = Agent(client, registry, workspace_dir=Path.cwd())
    events: list[AnyEvent] = []

    with pytest.raises(error_type) as caught:
        await agent.run("执行任务", on_event=events.append)

    assert caught.value is error
    _assert_run_finished(events, "cancelled" if error_type is asyncio.CancelledError else "error")
    if error_type is not asyncio.CancelledError:
        assert isinstance(events[-2], ErrorEvent)
        assert events[-2].message == f"{error_type.__name__}: 流已中断"
    history = agent.history.get_model_messages()
    assert [item["role"] for item in history] == ["system", "user", "assistant", "tool"]
    assert history[-1]["tool_call_id"] == "committed"
    assert ToolResult.model_validate_json(history[-1]["content"]).output == "已取得的观察"
    assert agent.history.get_pending_calls() == {}
    handler.assert_called_once_with(text="已取得的观察", repeat=1)
    assert [event.tool_call.id for event in events if isinstance(event, ToolCallEvent)] == [
        "committed"
    ]
    assert [event.tool_call_id for event in events if isinstance(event, ToolResultEvent)] == [
        "committed"
    ]

    assert await agent.run("继续") == "继续成功"
    assert client.requests[2]["messages"] == [*history, {"role": "user", "content": "继续"}]
    handler.assert_called_once()


@pytest.mark.parametrize(
    ("chunks", "error_match"),
    [
        ([stream_chunk("缺少结束片")], "缺少结束原因"),
        ([stream_chunk("没有调用", finish_reason="tool_calls")], "没有返回调用信息"),
        (
            [
                stream_chunk(
                    calls=[
                        {
                            "index": 0,
                            "type": "function",
                            "function": {"name": "echo", "arguments": "{}"},
                        }
                    ],
                    finish_reason="tool_calls",
                )
            ],
            "ID",
        ),
        (
            [
                stream_chunk(
                    calls=[
                        {
                            "index": 0,
                            "id": "missing-name",
                            "type": "function",
                            "function": {"arguments": "{}"},
                        }
                    ],
                    finish_reason="tool_calls",
                )
            ],
            "名称",
        ),
    ],
    ids=["missing-finish", "missing-calls", "missing-id", "missing-name"],
)
async def test_run_rejects_protocol_failure_without_commit_or_execution(
    registry: ToolRegistry,
    handler: Mock,
    chunks: list[ModelResponseStream],
    error_match: str,
) -> None:
    client = FakeLLM([chunks, final_response("恢复成功")])
    agent = Agent(client, registry, workspace_dir=Path.cwd())
    events: list[AnyEvent] = []

    with pytest.raises(RuntimeError, match=error_match):
        await agent.run("执行任务", on_event=events.append)

    _assert_run_finished(events, "error")
    assert isinstance(events[-2], ErrorEvent)
    assert error_match in events[-2].message
    history = agent.history.get_model_messages()
    assert [item["role"] for item in history] == ["system", "user"]
    assert agent.history.get_pending_calls() == {}
    assert not any(isinstance(event, (ToolCallEvent, ToolResultEvent)) for event in events)
    handler.assert_not_called()
    assert await agent.run("继续") == "恢复成功"
    assert client.requests[1]["messages"] == [*history, {"role": "user", "content": "继续"}]


async def test_run_cancels_inflight_stream_without_committing_or_executing(
    registry: ToolRegistry, handler: Mock
) -> None:
    waiting = asyncio.Event()
    blocked = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingLLM(FakeLLM):
        async def stream_turn(
            self, messages: list[dict[str, Any]], **kwargs: Any
        ) -> AsyncIterator[ModelResponseStream]:
            if not self.requests:
                self.requests.append(deepcopy({"messages": messages, **kwargs}))
                try:
                    yield stream_chunk("临时文字", calls=[{"index": 0, **tool_call()}])
                    waiting.set()
                    await blocked.wait()
                finally:
                    cancelled.set()
            else:
                async for chunk in super().stream_turn(messages, **kwargs):
                    yield chunk

    client = BlockingLLM([final_response("取消后继续")])
    agent = Agent(client, registry, workspace_dir=Path.cwd())
    events: list[AnyEvent] = []
    task = asyncio.create_task(agent.run("调用工具", on_event=events.append))
    try:
        await asyncio.wait_for(waiting.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert cancelled.is_set()
    _assert_run_finished(events, "cancelled")
    assert not any(isinstance(event, (ToolCallEvent, ToolResultEvent)) for event in events)
    history = agent.history.get_model_messages()
    assert [item["role"] for item in history] == ["system", "user"]
    assert agent.history.get_pending_calls() == {}
    handler.assert_not_called()
    assert await agent.run("继续") == "取消后继续"
    assert client.requests[1]["messages"] == [*history, {"role": "user", "content": "继续"}]
