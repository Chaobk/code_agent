"""Agent 编排单测：使用 FakeLLM 和假工具，不访问模型或文件系统。"""

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from copy import deepcopy
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from openai import APITimeoutError
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from pydantic import BaseModel, ConfigDict, Field

from code_agent.config import Settings
from code_agent.core import agent as agent_module
from code_agent.core.agent import Agent
from code_agent.events import (
    AnyEvent,
    RunResult,
    TextDelta,
    ToolCallEvent,
    ToolExecutionCancelled,
    ToolResult,
    ToolResultEvent,
    ToolStatus,
)
from code_agent.sessions.manager import SessionManager
from code_agent.sessions.store import SessionStore
from code_agent.tools import builtin as builtin_module
from code_agent.tools.builtin import shell as shell_module
from code_agent.tools.registry import ToolRegistry
from code_agent.tools.spec import ToolSpec
from tests.code_agent.helpers import make_agent
from tests.code_agent.llm_helpers import make_chunk, make_completion


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    repeat: int = Field(default=1, ge=1, le=3)


class FakeLLM:
    """依次产出预设流，完整响应转为分片，保存请求当时的快照。"""

    def __init__(
        self,
        responses: list[ChatCompletion | BaseException | list[ChatCompletionChunk | BaseException]],
    ) -> None:
        self._responses = iter(responses)
        self.requests: list[dict[str, Any]] = []
        self.close = AsyncMock()

    async def stream_turn(
        self, messages: list[dict[str, Any]], **kwargs: Any
    ) -> AsyncIterator[ChatCompletionChunk]:
        self.requests.append(deepcopy({"messages": messages, **kwargs}))
        try:
            response = next(self._responses)
        except StopIteration as exc:
            raise AssertionError("Agent 发起了预期之外的模型请求") from exc
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, ChatCompletion):
            choice = response.choices[0]
            message = choice.message
            delta: dict[str, Any] = {"role": "assistant", "content": message.content}
            if message.tool_calls:
                delta["tool_calls"] = [
                    {"index": index, **call.model_dump()}
                    for index, call in enumerate(message.tool_calls)
                ]
            response = [
                make_chunk(choices=[{"delta": delta, "finish_reason": choice.finish_reason}])
            ]
        for chunk in response:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


def assistant_response(content: str | None) -> ChatCompletion:
    """正常结束且没有工具调用的助手响应。"""
    return make_completion(
        choices=[
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ]
    )


def final_response(content: str | None = "任务完成") -> ChatCompletion:
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
) -> ChatCompletion:
    return make_completion(
        choices=[
            {
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": None, "tool_calls": calls},
            }
        ]
    )


def _assert_tool_result_is_committed(agent: Agent, event: ToolResultEvent) -> None:
    """在回调现场核实结果已经写入历史，并解除该调用的 pending 状态。"""
    message = agent.session_manager.get_messages()[-1]
    assert message["role"] == "tool"
    assert message["tool_call_id"] == event.tool_call_id
    assert ToolResult.model_validate_json(message["content"]) == event.tool_result
    assert event.tool_call_id not in agent.session_manager.get_pending_calls()


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
    agent = make_agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("回答问题") == RunResult(status="completed", text="最终答案")

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
    assert agent.session_manager.get_model_messages()[-1]["role"] == "assistant"
    assert agent.session_manager.get_model_messages()[-1]["content"] == "最终答案"
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
    # 显式覆盖，分别验证空列表和 None 两种无调用表示。
    response.choices[0].message.tool_calls = empty_calls
    client = FakeLLM([response])
    agent = make_agent(client, registry, max_steps=2, workspace_dir=Path.cwd())

    assert await agent.run("执行任务") == (RunResult(status="completed", text=content or ""))

    assert len(client.requests) == 1
    history = agent.session_manager.get_model_messages()
    assert [message["role"] for message in history] == ["system", "user", "assistant"]
    assert history[-1]["content"] == (content or "")
    handler.assert_not_called()


async def test_run_preserves_final_text_including_whitespace_and_old_marker(
    registry: ToolRegistry, handler: Mock
) -> None:
    """最终正文原样返回，旧标记只是文本，不删除末行或空白。"""
    content = "  已完成修改。\n示例：END_TASK\nEND_TASK\n\n"
    client = FakeLLM([assistant_response(content)])
    agent = make_agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("执行任务") == RunResult(status="completed", text=content)

    assert len(client.requests) == 1
    assert agent.session_manager.get_model_messages()[-1]["content"] == content
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
    agent = make_agent(client, registry, max_steps=2, workspace_dir=Path.cwd())

    assert await agent.run("执行任务") == RunResult(status="completed", text="还在分析。")
    assert len(client.requests) == 1
    handler.assert_not_called()


async def test_run_accepts_final_answer_on_last_allowed_request(
    registry: ToolRegistry, handler: Mock
) -> None:
    """最后一个允许的请求若正常收尾，应返回答案而不是步数上限错误。"""
    client = FakeLLM([tool_response([tool_call()]), final_response("任务完成")])
    agent = make_agent(client, registry, max_steps=2, workspace_dir=Path.cwd())

    assert await agent.run("执行任务") == RunResult(status="completed", text="任务完成")

    assert len(client.requests) == 2
    handler.assert_called_once_with(text="你好", repeat=1)


@pytest.mark.parametrize("content", ["", None])
async def test_run_returns_completed_for_empty_final_content(
    registry: ToolRegistry, content: str | None
) -> None:
    client = FakeLLM([final_response(content)])

    assert await make_agent(client, registry, workspace_dir=Path.cwd()).run(
        "回答问题"
    ) == RunResult(status="completed", text="")
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
    agent = make_agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("调用工具并确认结果") == RunResult(
        status="completed", text="已根据工具结果确认完成"
    )

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
    handler.side_effect = None
    handler.return_value = ToolResult(
        output={"error": "工具执行错误：执行失败"}, status=ToolStatus.ERROR
    )
    response = tool_response([tool_call()])
    response.choices[0].message.content = "工具执行成功。\nEND_TASK"
    client = FakeLLM([response, final_response("工具失败,需要说明原因")])
    agent = make_agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("调用工具") == RunResult(
        status="completed", text="工具失败,需要说明原因"
    )

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
    agent = make_agent(client, registry, workspace_dir=workspace / "child" / "..")

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
    agent = make_agent(client, workspace_dir=settings.workspace_dir, bash_path=settings.bash_path)

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

    await make_agent(client, workspace_dir=Path.cwd()).run("查看目录")

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
        agents.append(make_agent(client, workspace_dir=Path(name), bash_path=f"{name}-bash"))

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
    agent = make_agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("使用工具") == RunResult(status="completed", text="收到工具结果")

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

    assert await make_agent(client, registry, workspace_dir=Path.cwd()).run(
        "调用异步工具"
    ) == RunResult(status="completed", text="收到结果")

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

    await make_agent(client, registry, workspace_dir=Path.cwd()).run("调用两个工具")

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

    await make_agent(client, registry, workspace_dir=Path.cwd()).run("调用工具")

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

    await make_agent(client, registry, workspace_dir=Path.cwd()).run("调用工具")

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
    handler.side_effect = None
    handler.return_value = ToolResult(
        output={"error": f"工具执行错误：{type(error).__name__}: {error}"}, status=ToolStatus.ERROR
    )
    client = FakeLLM([tool_response([tool_call()]), final_response("已处理错误")])

    assert await make_agent(client, registry, workspace_dir=Path.cwd()).run(
        "调用工具"
    ) == RunResult(status="completed", text="已处理错误")

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
    agent = make_agent(client, registry, max_steps=3, workspace_dir=Path.cwd())
    events: list[AnyEvent] = []

    result = await agent.run("重复调用工具", on_event=events.append)
    assert result == RunResult(status="max_steps", error="达到最大请求轮数：3")
    assert isinstance(events[-1], ToolResultEvent)
    assert len(client.requests) == 3
    assert handler.call_count == 3
    assert (
        len(
            [
                message
                for message in agent.session_manager.get_model_messages()
                if message["role"] == "tool"
            ]
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
    agent = make_agent(client, registry, workspace_dir=Path.cwd())
    events: list[AnyEvent] = []

    result = await agent.run("调用工具", on_event=events.append)
    assert result.status == "failed"
    assert finish_reason in result.error

    assert len(client.requests) == 1
    assert [message["role"] for message in agent.session_manager.get_model_messages()] == [
        "system",
        "user",
    ]
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

    result = await make_agent(client, registry, workspace_dir=Path.cwd()).run("调用工具")
    assert result.status == "failed"
    assert "没有返回调用信息" in result.error

    assert len(client.requests) == 1
    handler.assert_not_called()


async def test_run_propagates_model_request_error(registry: ToolRegistry, handler: Mock) -> None:
    error = RuntimeError("模型请求失败")
    client = FakeLLM([error])
    events: list[AnyEvent] = []

    with pytest.raises(RuntimeError, match="模型请求失败") as caught:
        await make_agent(client, registry, workspace_dir=Path.cwd()).run(
            "回答问题", on_event=events.append
        )

    assert events == []
    assert caught.value is error
    assert len(client.requests) == 1
    handler.assert_not_called()


async def test_run_propagates_tool_cancellation(registry: ToolRegistry, handler: Mock) -> None:
    handler.side_effect = asyncio.CancelledError()
    client = FakeLLM([tool_response([tool_call()]), final_response("不应继续请求")])
    agent = make_agent(client, registry, workspace_dir=Path.cwd())

    with pytest.raises(asyncio.CancelledError):
        await agent.run("调用工具")

    assert len(client.requests) == 1
    handler.assert_called_once()
    assert agent.session_manager.get_pending_calls() == {}

    messages = agent.session_manager.get_model_messages()
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "tool"]
    assert messages[-1]["tool_call_id"] == "call_1"

    observation = ToolResult.model_validate_json(messages[-1]["content"])
    assert observation.status == ToolStatus.ERROR
    assert isinstance(observation.output, dict)
    assert observation.output["reason"] == "interrupted"
    assert observation.output["execution"] == "unknown"


@pytest.mark.parametrize("error", [RuntimeError("BROKEN_TOOL"), TypeError("BROKEN_TOOL")])
async def test_internal_tool_failure_settles_batch_without_more_actions(error: Exception) -> None:
    executed: list[str] = []

    async def execute(text: str, repeat: int = 1) -> str:
        executed.append(text)
        if text == "current":
            raise error
        return text

    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "回显", EchoArgs, execute))
    client = FakeLLM(
        [
            tool_response(
                [
                    tool_call(text, arguments=json.dumps({"text": text}))
                    for text in ("done", "current", "remaining")
                ]
            ),
            final_response("不得请求"),
        ]
    )
    agent = make_agent(client, registry, workspace_dir=Path.cwd())
    events: list[AnyEvent] = []

    with pytest.raises(type(error)) as caught:
        await agent.run("执行批次", on_event=events.append)

    assert caught.value is error
    assert executed == ["done", "current"]
    assert len(client.requests) == 1
    assert agent.session_manager.get_pending_calls() == {}
    results = [
        ToolResult.model_validate_json(m["content"])
        for m in agent.session_manager.get_model_messages()
        if m["role"] == "tool"
    ]
    assert results[0] == ToolResult(output="done", status=ToolStatus.COMPLETED)
    assert results[1].output["execution"] == "unknown"
    assert results[2].output["execution"] == "not_started"
    assert len([event for event in events if isinstance(event, ToolResultEvent)]) == 1


@pytest.mark.parametrize("boundary", ["text", "call", "result"])
async def test_renderer_failure_is_fatal_and_not_called_again(
    registry: ToolRegistry, handler: Mock, boundary: str
) -> None:
    """即使显示器抛 SDK 同类型异常，也不能误判成受控模型失败。"""
    response = tool_response([tool_call("first"), tool_call("remaining")])
    response.choices[0].message.content = "部分正文"
    client = FakeLLM([response, final_response("不得请求")])
    agent = make_agent(client, registry, workspace_dir=Path.cwd())
    failure = APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))
    event_class = {"text": TextDelta, "call": ToolCallEvent, "result": ToolResultEvent}[boundary]
    failed = False

    def render(event: AnyEvent) -> None:
        nonlocal failed
        assert not failed, "不能重试已失败的显示回调"
        if isinstance(event, event_class):
            failed = True
            raise failure

    with pytest.raises(APITimeoutError) as caught:
        await agent.run("执行工具", on_event=render)

    assert caught.value is failure
    assert agent.session_manager.get_pending_calls() == {}
    assert len(client.requests) == 1
    history = agent.session_manager.get_model_messages()
    if boundary == "text":
        assert [m["role"] for m in history] == ["system", "user"]
        handler.assert_not_called()
    else:
        results = [
            ToolResult.model_validate_json(m["content"]) for m in history if m["role"] == "tool"
        ]
        if boundary == "call":
            handler.assert_not_called()
            assert all(result.output["execution"] == "not_started" for result in results)
        else:
            handler.assert_called_once()
            assert results[0].status == ToolStatus.COMPLETED
            assert results[1].output["execution"] == "not_started"


async def test_renderer_failure_closes_model_stream(registry: ToolRegistry) -> None:
    closed = False

    class StreamingLLM:
        async def stream_turn(
            self, *args: Any, **kwargs: Any
        ) -> AsyncIterator[ChatCompletionChunk]:
            nonlocal closed
            try:
                yield make_chunk(choices=[{"delta": {"content": "partial"}}])
                raise AssertionError("不能在显示失败后继续读取")
            finally:
                closed = True

    agent = make_agent(StreamingLLM(), registry, workspace_dir=Path.cwd())
    with pytest.raises(RuntimeError, match="RENDER_FAILED"):
        await agent.run("输出", on_event=Mock(side_effect=RuntimeError("RENDER_FAILED")))
    assert closed
    assert agent.session_manager.get_pending_calls() == {}


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
    agent = make_agent(client, registry, workspace_dir=Path.cwd())

    def on_event(event: AnyEvent) -> None:
        if isinstance(event, ToolCallEvent):
            assert event.tool_call.id in agent.session_manager.get_pending_calls()
            assert event.tool_call.model_dump() == {
                "id": calls[len(executed)]["id"],
                **calls[len(executed)]["function"],
            }
            events.append(event)
        elif isinstance(event, ToolResultEvent):
            _assert_tool_result_is_committed(agent, event)
            assert len(client.requests) == 1
            events.append(event)

    assert await agent.run("执行两个工具", on_event=on_event) == RunResult(
        status="completed", text="已处理观察"
    )

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
async def test_run_returns_completed_after_final_text(
    registry: ToolRegistry, handler: Mock, content: str | None
) -> None:
    client = FakeLLM([final_response(content)])
    agent = make_agent(client, registry, workspace_dir=Path.cwd())
    events: list[AnyEvent] = []

    assert await agent.run("回答问题", on_event=events.append) == (
        RunResult(status="completed", text=content or "")
    )

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
    handler.side_effect = None
    handler.return_value = ToolResult(
        output={"error": "工具执行错误：执行失败[bold]"}, status=ToolStatus.ERROR
    )
    client = FakeLLM(
        [tool_response([tool_call("failed", name, arguments)]), final_response("已根据错误收尾")]
    )
    agent = make_agent(client, registry, workspace_dir=Path.cwd())
    results: list[ToolResultEvent] = []
    events: list[AnyEvent] = []

    def on_event(event: AnyEvent) -> None:
        events.append(event)
        if isinstance(event, ToolResultEvent):
            _assert_tool_result_is_committed(agent, event)
            results.append(event)

    assert await agent.run("调用工具", on_event=on_event) == RunResult(
        status="completed", text="已根据错误收尾"
    )

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
    agent = make_agent(client, registry, workspace_dir=Path.cwd())
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
    assert len(client.requests) == 1
    assert agent.session_manager.get_pending_calls() == {}
    history = agent.session_manager.get_model_messages()
    results = [message for message in history if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in results] == [call["id"] for call in calls]
    assert [
        type(event) for event in events if isinstance(event, (ToolCallEvent, ToolResultEvent))
    ] == ([ToolCallEvent, ToolResultEvent] * cancel_index + [ToolCallEvent])
    assert [event.tool_call.id for event in events if isinstance(event, ToolCallEvent)] == [
        call["id"] for call in calls[: cancel_index + 1]
    ]
    result_events = [event for event in events if isinstance(event, ToolResultEvent)]
    assert [event.tool_call_id for event in result_events] == [
        call["id"] for call in calls[:cancel_index]
    ]
    assert [event.tool_result for event in result_events] == [
        ToolResult.model_validate_json(message["content"]) for message in results[:cancel_index]
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

    assert await agent.run("continue", on_event=on_event) == RunResult(
        status="completed", text="resumed"
    )
    assert len(client.requests) == 2
    assert client.requests[1]["messages"] == [*history, {"role": "user", "content": "continue"}]
    assert executed == texts[: cancel_index + 1]
    assert len([event for event in events if isinstance(event, ToolResultEvent)]) == cancel_index


@pytest.mark.parametrize("cleanup_fails", [False, True], ids=["cleanup-ok", "cleanup-error"])
async def test_run_stops_after_bash_cancellation_even_when_cleanup_fails(
    cleanup_fails: bool,
    registry: ToolRegistry,
    handler: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """使用真实 Shell/Registry/Core，取消后不得继续工具批次或请求模型。"""
    started = asyncio.Event()
    output_closed = asyncio.Event()
    readers: list[asyncio.Task[Any]] = []
    process = SimpleNamespace(pid=4321, returncode=None, send_signal=Mock())
    tracked = Mock()

    async def collect(process: Any) -> tuple[str, str, bool]:
        reader = asyncio.current_task()
        assert reader is not None
        readers.append(reader)
        started.set()
        await output_closed.wait()
        return "partial", "", False

    async def stop(*args: object) -> None:
        if cleanup_fails:
            raise RuntimeError("CLEANUP_FAILED")
        output_closed.set()

    monkeypatch.setattr(shell_module, "PROCESS_CLEANUP_TIMEOUT_SECONDS", 0.05)
    cleanup = AsyncMock(side_effect=stop)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr(shell_module.psutil, "Process", Mock(return_value=tracked))
    monkeypatch.setattr(shell_module, "collect_output", collect)
    monkeypatch.setattr(shell_module, "stop_process_tree", cleanup)
    registry.register(
        ToolSpec(
            "bash",
            "执行 Bash 命令",
            shell_module.BashArgs,
            partial(shell_module.run_bash, workspace_dir=Path.cwd()),
        )
    )
    client = FakeLLM(
        [
            tool_response(
                [
                    tool_call("done", arguments='{"text":"before"}'),
                    tool_call("current", "bash", '{"command":"long-command"}'),
                    tool_call("remaining", arguments='{"text":"after"}'),
                ]
            ),
            final_response("不应在取消后继续请求"),
        ]
    )
    agent = make_agent(client, registry, workspace_dir=Path.cwd())
    events: list[AnyEvent] = []
    task = asyncio.create_task(agent.run("execute batch", on_event=events.append))

    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        # 收集实际结果，先检查是否产生取消后的额外动作，给出明确失败证据。
        outcomes = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=2)
        outcome = outcomes[0]
    finally:
        output_closed.set()
        task.cancel()
        await asyncio.gather(task, *readers, return_exceptions=True)

    cleanup.assert_awaited_once_with(process, tracked)
    assert (
        [call.kwargs["text"] for call in handler.call_args_list],
        len(client.requests),
    ) == (["before"], 1)
    if cleanup_fails:
        assert isinstance(outcome, RuntimeError)
        assert "CLEANUP_FAILED" in str(outcome)
        assert isinstance(outcome.__context__, asyncio.CancelledError)
    else:
        assert isinstance(outcome, asyncio.CancelledError)
    assert all(reader.done() for reader in readers)
    assert agent.session_manager.get_pending_calls() == {}
    results = [
        message
        for message in agent.session_manager.get_model_messages()
        if message["role"] == "tool"
    ]
    assert [message["tool_call_id"] for message in results] == ["done", "current", "remaining"]
    observations = [ToolResult.model_validate_json(message["content"]) for message in results]
    assert observations[0] == ToolResult(output="before", status=ToolStatus.COMPLETED)
    assert observations[1].status == ToolStatus.ERROR
    assert observations[1].output["execution"] == "unknown"
    assert observations[2].output["execution"] == "not_started"
    assert [event.tool_call.id for event in events if isinstance(event, ToolCallEvent)] == [
        "done",
        "current",
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
    agent = make_agent(client, registry, workspace_dir=Path.cwd())
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
    handler.assert_awaited_once_with(text="first", repeat=1)
    assert len(client.requests) == 1
    assert agent.session_manager.get_pending_calls() == {}
    results = [
        message
        for message in agent.session_manager.get_model_messages()
        if message["role"] == "tool"
    ]
    assert [message["tool_call_id"] for message in results] == ["current", "remaining"]
    assert ToolResult.model_validate_json(results[0]["content"]) == observation
    remaining = ToolResult.model_validate_json(results[1]["content"])
    assert remaining.status == ToolStatus.ERROR
    assert isinstance(remaining.output, dict)
    assert remaining.output["reason"] == "interrupted"
    assert remaining.output["execution"] == "not_started"
    assert result_events == []


async def test_run_preserves_history_between_user_turns(
    registry: ToolRegistry, handler: Mock
) -> None:
    client = FakeLLM([final_response("第一轮答案"), final_response("第二轮答案")])
    agent = make_agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("第一轮问题") == RunResult(status="completed", text="第一轮答案")
    assert await agent.run("第二轮问题") == RunResult(status="completed", text="第二轮答案")

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

    await make_agent(client, registry, workspace_dir=Path.cwd()).run("调用工具")

    assert len(executed_threads) == 1
    assert executed_threads[0] != event_loop_thread


def stream_chunk(
    content: str | None = None,
    *,
    calls: list[dict[str, Any]] | None = None,
    finish_reason: str | None = None,
) -> ChatCompletionChunk:
    """构造原始分片；参数是否完整由组装器和注册表分别判断。"""
    return make_chunk(
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
                make_chunk(choices=[{"delta": {"role": "assistant"}}]),
                stream_chunk("  分片"),
                stream_chunk("正文\n", finish_reason="stop"),
                make_chunk(
                    choices=[],
                    usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                ),
            ]
        ]
    )
    agent = make_agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("回答问题") == RunResult(status="completed", text="  分片正文\n")

    assert len(client.requests) == 1
    assert agent.session_manager.get_model_messages()[-1] == {
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
    agent = make_agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("执行两个工具") == RunResult(status="completed", text="观察已齐全")

    assert [call.kwargs["text"] for call in handler.call_args_list] == ["第一", "第二"]
    assert len(client.requests) == 2
    messages = client.requests[1]["messages"]
    assert messages[-3]["content"] == "准备执行"
    assert [call["id"] for call in messages[-3]["tool_calls"]] == ["first", "second"]
    assert [message["tool_call_id"] for message in messages[-2:]] == ["first", "second"]
    assert agent.session_manager.get_pending_calls() == {}


async def test_run_waits_for_stream_exhaustion_before_committing_or_executing(
    registry: ToolRegistry, handler: Mock
) -> None:
    at_tail = asyncio.Event()
    release = asyncio.Event()

    class TailLLM(FakeLLM):
        async def stream_turn(
            self, messages: list[dict[str, Any]], **kwargs: Any
        ) -> AsyncIterator[ChatCompletionChunk]:
            if not self.requests:
                self.requests.append(deepcopy({"messages": messages, **kwargs}))
                yield stream_chunk(
                    "尚未耗尽",
                    calls=[{"index": 0, **tool_call()}],
                    finish_reason="tool_calls",
                )
                at_tail.set()
                await release.wait()
                yield make_chunk(choices=[])
            else:
                async for chunk in super().stream_turn(messages, **kwargs):
                    yield chunk

    client = TailLLM([final_response("完成")])
    agent = make_agent(client, registry, workspace_dir=Path.cwd())
    task = asyncio.create_task(agent.run("调用工具"))
    try:
        await asyncio.wait_for(at_tail.wait(), timeout=5)
        assert [item["role"] for item in agent.session_manager.get_model_messages()] == [
            "system",
            "user",
        ]
        handler.assert_not_called()
        assert not task.done()
        release.set()
        assert await asyncio.wait_for(task, timeout=5) == RunResult(status="completed", text="完成")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    handler.assert_called_once_with(text="你好", repeat=1)
    assert len(client.requests) == 2
    assert agent.session_manager.get_pending_calls() == {}


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
    agent = make_agent(client, registry, workspace_dir=Path.cwd())
    events: list[AnyEvent] = []

    with pytest.raises(error_type) as caught:
        await agent.run("执行任务", on_event=events.append)

    assert caught.value is error
    history = agent.session_manager.get_model_messages()
    assert [item["role"] for item in history] == ["system", "user", "assistant", "tool"]
    assert history[-1]["tool_call_id"] == "committed"
    assert ToolResult.model_validate_json(history[-1]["content"]).output == "已取得的观察"
    assert agent.session_manager.get_pending_calls() == {}
    handler.assert_called_once_with(text="已取得的观察", repeat=1)
    assert [event.tool_call.id for event in events if isinstance(event, ToolCallEvent)] == [
        "committed"
    ]
    assert [event.tool_call_id for event in events if isinstance(event, ToolResultEvent)] == [
        "committed"
    ]

    assert await agent.run("继续") == RunResult(status="completed", text="继续成功")
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
    chunks: list[ChatCompletionChunk],
    error_match: str,
) -> None:
    client = FakeLLM([chunks, final_response("恢复成功")])
    agent = make_agent(client, registry, workspace_dir=Path.cwd())
    events: list[AnyEvent] = []

    result = await agent.run("执行任务", on_event=events.append)
    assert result.status == "failed"
    assert error_match in result.error
    history = agent.session_manager.get_model_messages()
    assert [item["role"] for item in history] == ["system", "user"]
    assert agent.session_manager.get_pending_calls() == {}
    assert not any(isinstance(event, (ToolCallEvent, ToolResultEvent)) for event in events)
    handler.assert_not_called()
    assert await agent.run("继续") == RunResult(status="completed", text="恢复成功")
    assert client.requests[1]["messages"] == [*history, {"role": "user", "content": "继续"}]


@pytest.mark.parametrize("phase", ["request", "midstream", "after-finish"])
async def test_run_returns_sdk_failure_without_committing_or_executing(
    registry: ToolRegistry, handler: Mock, phase: str
) -> None:
    error = APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))
    interrupted_stream = [
        stream_chunk(
            "临时正文",
            calls=[{"index": 0, **tool_call("uncommitted")}],
            finish_reason="tool_calls" if phase == "after-finish" else None,
        ),
        error,
    ]
    client = FakeLLM([error if phase == "request" else interrupted_stream])
    agent = make_agent(client, registry, workspace_dir=Path.cwd())
    events: list[AnyEvent] = []

    result = await agent.run("执行任务", on_event=events.append)
    assert result == RunResult(status="failed", error=f"APITimeoutError: {error}")
    assert [message["role"] for message in agent.session_manager.get_model_messages()] == [
        "system",
        "user",
    ]
    assert not any(isinstance(event, (ToolCallEvent, ToolResultEvent)) for event in events)
    assert len(client.requests) == 1
    handler.assert_not_called()


async def test_run_cancels_inflight_stream_without_committing_or_executing(
    registry: ToolRegistry, handler: Mock
) -> None:
    waiting = asyncio.Event()
    blocked = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingLLM(FakeLLM):
        async def stream_turn(
            self, messages: list[dict[str, Any]], **kwargs: Any
        ) -> AsyncIterator[ChatCompletionChunk]:
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
    agent = make_agent(client, registry, workspace_dir=Path.cwd())
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
    assert not any(isinstance(event, (ToolCallEvent, ToolResultEvent)) for event in events)
    history = agent.session_manager.get_model_messages()
    assert [item["role"] for item in history] == ["system", "user"]
    assert agent.session_manager.get_pending_calls() == {}
    handler.assert_not_called()
    assert await agent.run("继续") == RunResult(status="completed", text="取消后继续")
    assert client.requests[1]["messages"] == [*history, {"role": "user", "content": "继续"}]


@pytest.mark.parametrize("invalid_field", ["workspace_dir", "bash_path"])
def test_create_agent_validates_environment_before_constructing_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_field: str
) -> None:
    workspace = tmp_path / "missing" if invalid_field == "workspace_dir" else tmp_path
    settings = Settings(
        _env_file=None,
        workspace_dir=workspace,
        bash_path=str(tmp_path / "missing-bash.exe"),
    )
    constructor = Mock(side_effect=AssertionError("无效环境不得创建客户端"))
    monkeypatch.setattr(agent_module, "LLMClient", constructor)

    with pytest.raises(ValueError, match=invalid_field):
        agent_module.create_agent(settings)

    constructor.assert_not_called()


async def test_create_agent_binds_resolved_paths_to_builtin_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executable = tmp_path / "bin" / "creation-review-bash.exe"
    executable.parent.mkdir()
    executable.touch()
    executable.chmod(0o700)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(executable.parent))
    monkeypatch.setenv("PATHEXT", ".EXE")
    monkeypatch.setenv("NoDefaultCurrentDirectoryInExePath", "1")
    settings = Settings(
        _env_file=None,
        workspace_dir=Path("workspace") / "child" / "..",
        bash_path=executable.name,
        sessions_dir=tmp_path / "sessions",
    )
    client = FakeLLM(
        [tool_response([tool_call(name="bash", arguments='{"command":"pwd"}')]), final_response()]
    )
    monkeypatch.setattr(agent_module, "LLMClient", Mock(return_value=client))
    handler = AsyncMock(return_value=ToolResult(output="workspace", status=ToolStatus.COMPLETED))
    monkeypatch.setattr(builtin_module, "run_bash", handler)

    agent = agent_module.create_agent(settings)
    monkeypatch.setenv("PATH", "")
    result = await agent.run("查看工作目录")

    assert agent.workspace_dir == workspace.resolve()
    session_files = list(settings.sessions_dir.glob("*.jsonl"))
    assert len(session_files) == 1
    records = [
        json.loads(line) for line in session_files[0].read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["workspace_dir"] == str(workspace.resolve())
    assert [record for record in records[1:] if record["type"] == "message"] == [
        {"type": "message", "message": message} for message in agent.session_manager.get_messages()
    ]
    assert [record["type"] for record in records if record["type"].startswith("run_")] == [
        "run_start", "run_end"
    ]
    assert [record["type"] for record in records] == [
        "session_start", "message", "run_start", "message", "message", "message", "message",
        "run_end",
    ]
    assert records[2]["run_id"] == records[-1]["run_id"]
    assert records[-2]["message"]["role"] == "assistant"
    assert records[-2]["message"]["content"] == result.text
    assert records[-1] == {
        "type": "run_end", "run_id": records[2]["run_id"], "status": "completed", "error": None
    }
    assert not list(workspace.glob("*.jsonl"))
    assert str(workspace.resolve()) in client.requests[0]["messages"][0]["content"]
    handler.assert_awaited_once_with(
        command="pwd",
        timeout_seconds=30,
        workspace_dir=workspace.resolve(),
        bash_path=str(executable.resolve()),
    )
    assert settings.workspace_dir == Path("workspace") / "child" / ".."
    assert settings.bash_path == executable.name


@pytest.mark.parametrize("failure_role", ["assistant", "tool"])
async def test_storage_failure_stops_batch_preserves_facts_and_prevents_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_role: str
) -> None:
    manager = SessionManager(tmp_path, tmp_path / "sessions")
    handler = AsyncMock(return_value="真实结果")
    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "回显", EchoArgs, handler))
    client = FakeLLM(
        [tool_response([tool_call("first"), tool_call("remaining")]), final_response()]
    )
    agent = make_agent(client, registry, workspace_dir=tmp_path, session_manager=manager)
    original_append = SessionStore.append_message
    error = OSError("模拟保存失败")
    failed_attempts = 0

    def append(store: SessionStore, session_id: str, message: dict[str, Any]) -> None:
        nonlocal failed_attempts
        if message["role"] == failure_role:
            failed_attempts += 1
            raise error
        original_append(store, session_id, message)

    monkeypatch.setattr(SessionStore, "append_message", append)

    with pytest.raises(OSError) as caught:
        await agent.run("执行批次")

    assert caught.value is error
    assert failed_attempts == 1
    assert handler.await_count == (0 if failure_role == "assistant" else 1)
    assert len(client.requests) == 1
    assert manager.get_pending_calls() == {}
    results = [
        ToolResult.model_validate_json(message["content"])
        for message in manager.get_messages()
        if message["role"] == "tool"
    ]
    if failure_role == "assistant":
        assert all(result.output["execution"] == "not_started" for result in results)
    else:
        assert results[0] == ToolResult(output="真实结果", status=ToolStatus.COMPLETED)
        assert results[1].output["execution"] == "not_started"
    previous = manager.get_messages()
    with pytest.raises(OSError) as blocked:
        await agent.run("不得继续")
    assert blocked.value is error
    assert manager.get_messages() == previous
    assert failed_attempts == 1
    assert len(client.requests) == 1


@pytest.mark.parametrize("cancel", [True, False], ids=["cancelled", "tool-error"])
async def test_batch_preserves_original_error_when_settlement_storage_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    observation = ToolResult(output="取消前取得的结果", status=ToolStatus.ERROR, truncated=True)
    failure = ToolExecutionCancelled(observation) if cancel else RuntimeError("工具内部故障")
    handler = AsyncMock(side_effect=failure)
    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "回显", EchoArgs, handler))
    manager = SessionManager(tmp_path, tmp_path / "sessions")
    client = FakeLLM([tool_response([tool_call("current"), tool_call("remaining")])])
    agent = make_agent(client, registry, workspace_dir=tmp_path, session_manager=manager)
    original_append = SessionStore.append_message
    append_tool = Mock(side_effect=OSError("取消收尾保存失败"))

    def append(store: SessionStore, session_id: str, message: dict[str, Any]) -> None:
        if message["role"] == "tool":
            append_tool(message)
        else:
            original_append(store, session_id, message)

    monkeypatch.setattr(SessionStore, "append_message", append)

    with pytest.raises(type(failure)) as caught:
        await agent.run("执行批次")

    assert caught.value is failure
    append_tool.assert_called_once()
    assert manager.get_pending_calls() == {}
    results = [
        ToolResult.model_validate_json(message["content"])
        for message in manager.get_messages()
        if message["role"] == "tool"
    ]
    if cancel:
        assert results[0] == observation
    else:
        assert results[0].output["execution"] == "unknown"
    assert results[1].output["execution"] == "not_started"
    assert handler.await_count == 1
    assert len(client.requests) == 1
