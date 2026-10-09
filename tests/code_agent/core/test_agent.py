"""Agent 编排单测：使用 FakeLLM 和假工具，不访问模型或文件系统。"""

import asyncio
import json
import threading
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.utils import ModelResponse
from pydantic import BaseModel, ConfigDict, Field

from code_agent.config import Settings
from code_agent.core.agent import Agent
from code_agent.events import ToolResult, ToolStatus
from code_agent.llm import client as client_module
from code_agent.tools.builtin import create_builtin_registry
from code_agent.tools.builtin import shell as shell_module


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    repeat: int = Field(default=1, ge=1, le=3)


class FakeLLM:
    """依次返回预设响应，并保存请求当时的快照。"""

    def __init__(self, responses: list[ModelResponse | BaseException]) -> None:
        self._responses = iter(responses)
        self.requests: list[dict[str, Any]] = []

    async def complete_turn(self, messages: list[dict[str, Any]], **kwargs: Any) -> ModelResponse:
        self.requests.append(deepcopy({"messages": messages, **kwargs}))
        try:
            response = next(self._responses)
        except StopIteration as exc:
            raise AssertionError("Agent 发起了预期之外的模型请求") from exc
        if isinstance(response, BaseException):
            raise response
        return response


def assistant_response(content: str | None) -> ModelResponse:
    """普通助手文本,不自动添加结束标记,可用于中间轮次。"""
    return ModelResponse(
        choices=[
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ]
    )


def final_response(content: str | None = "任务完成") -> ModelResponse:
    """明确结束的响应:总结之后以独立末行输出临时结束标记。"""
    return assistant_response(f"{content or ''}\nEND_TASK")


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
def registry(handler: Mock) -> dict[str, dict[str, Any]]:
    return {
        "echo": {
            "description": "返回指定文本，可重复多次",
            "args_model": EchoArgs,
            "handler": handler,
        }
    }


async def test_run_returns_final_answer_and_sends_serializable_tools(
    registry: dict[str, dict[str, Any]], handler: Mock
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
    assert function["description"] == registry["echo"]["description"]
    assert function["parameters"]["required"] == ["text"]
    assert function["parameters"]["properties"]["repeat"]["default"] == 1
    assert "handler" not in function
    assert json.loads(json.dumps(request["tools"])) == request["tools"]
    assert agent.messages[-1]["role"] == "assistant"
    assert agent.messages[-1]["content"] == "最终答案\nEND_TASK"
    handler.assert_not_called()


@pytest.mark.parametrize(
    "content",
    ["我先检查相关代码。", "示例中使用 END_TASK 标记。", "NOT_END_TASK", "", None],
    ids=["intermediate-text", "quoted-marker", "marker-substring", "empty", "null"],
)
async def test_run_continues_text_only_turns_without_final_end_marker(
    registry: dict[str, dict[str, Any]], handler: Mock, content: str | None
) -> None:
    """中间文本或正文中的标记不能结束任务,空正文也不得导致类型错误。"""
    client = FakeLLM([assistant_response(content), final_response("任务完成")])
    agent = Agent(client, registry, max_steps=2, workspace_dir=Path.cwd())

    assert await agent.run("执行任务") == "任务完成"

    assert len(client.requests) == 2
    history = client.requests[1]["messages"]
    assert [message["role"] for message in history] == ["system", "user", "assistant"]
    assert history[-1]["content"] == content
    handler.assert_not_called()


async def test_run_removes_final_end_marker_from_returned_summary(
    registry: dict[str, dict[str, Any]], handler: Mock
) -> None:
    """允许末尾空行,最后一行结束标记不属于用户看到的任务总结。"""
    client = FakeLLM([assistant_response("已完成修改。\n测试全部通过。\nEND_TASK\n\n")])
    agent = Agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("执行任务") == "已完成修改。\n测试全部通过。"

    assert len(client.requests) == 1
    assert agent.messages[-1]["content"] == "已完成修改。\n测试全部通过。\nEND_TASK\n\n"
    handler.assert_not_called()


async def test_run_limits_text_only_turns_without_end_marker(
    registry: dict[str, dict[str, Any]], handler: Mock
) -> None:
    """未明确结束的纯文本轮次也受最大请求轮数限制。"""
    client = FakeLLM([assistant_response("还在分析。"), assistant_response("继续分析。")])
    agent = Agent(client, registry, max_steps=2, workspace_dir=Path.cwd())

    with pytest.raises(RuntimeError, match="达到最大请求轮数：2"):
        await agent.run("执行任务")

    assert len(client.requests) == 2
    handler.assert_not_called()


@pytest.mark.parametrize("content", ["", None])
async def test_run_returns_string_for_empty_final_content(
    registry: dict[str, dict[str, Any]], content: str | None
) -> None:
    client = FakeLLM([final_response(content)])

    assert await Agent(client, registry, workspace_dir=Path.cwd()).run("回答问题") == ""
    assert len(client.requests) == 1


@pytest.mark.parametrize("finish_reason", ["tool_calls", "stop"])
@pytest.mark.parametrize("content", ["我先执行工具。", "任务已经完成。\nEND_TASK"])
async def test_run_replays_tool_result_before_accepting_end_marker(
    registry: dict[str, dict[str, Any]],
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
        "content": "工具观察",
    }
    handler.assert_called_once_with(text="工具观察", repeat=1)


async def test_run_replays_tool_failure_despite_same_turn_end_marker(
    registry: dict[str, dict[str, Any]], handler: Mock
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
    registry: dict[str, dict[str, Any]],
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

    assert client.requests[1]["messages"][-1]["content"].startswith("工具参数错误")
    create_process.assert_not_awaited()


async def test_bash_bindings_are_independent_for_agents_sharing_registry() -> None:
    observations: list[tuple[Path, str]] = []

    async def handler(
        command: str, timeout_seconds: int, *, workspace_dir: Path, bash_path: str
    ) -> ToolResult:
        observations.append((workspace_dir, bash_path))
        return ToolResult(output=command, status=ToolStatus.COMPLETED)

    shared_registry = create_builtin_registry()
    shared_registry["bash"]["handler"] = handler
    agents = []
    for name in ("first", "second"):
        client = FakeLLM(
            [
                tool_response([tool_call(name="bash", arguments='{"command":"pwd"}')]),
                final_response(),
            ]
        )
        agents.append(
            Agent(client, shared_registry, workspace_dir=Path(name), bash_path=f"{name}-bash")
        )

    for agent in agents:
        await agent.run("查看目录")

    assert observations == [
        (Path("first").resolve(), "first-bash"),
        (Path("second").resolve(), "second-bash"),
    ]
    assert shared_registry["bash"]["handler"] is handler


async def test_run_executes_tool_and_replays_call_and_result(
    registry: dict[str, dict[str, Any]], handler: Mock
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
    assert history[-2]["content"] is None
    assert history[-2]["tool_calls"] == [call]
    assert history[-1] == {"role": "tool", "tool_call_id": "call_1", "content": "你好你好"}


async def test_run_awaits_async_tool_and_serializes_result() -> None:
    event_loop_thread = threading.get_ident()

    async def handler(text: str, repeat: int) -> ToolResult:
        assert threading.get_ident() == event_loop_thread
        await asyncio.sleep(0)
        return ToolResult(output={"text": text * repeat}, status=ToolStatus.COMPLETED)

    registry = {"echo": {"description": "异步回显", "args_model": EchoArgs, "handler": handler}}
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
    registry: dict[str, dict[str, Any]], handler: Mock
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
        {"role": "tool", "tool_call_id": "call_first", "content": "第一"},
        {"role": "tool", "tool_call_id": "call_second", "content": "第二第二"},
    ]


async def test_run_returns_unknown_tool_error_to_model(
    registry: dict[str, dict[str, Any]], handler: Mock
) -> None:
    client = FakeLLM([tool_response([tool_call(name="unknown")]), final_response()])

    await Agent(client, registry, workspace_dir=Path.cwd()).run("调用工具")

    result = client.requests[1]["messages"][-1]
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "call_1"
    assert result["content"].startswith("工具错误")
    assert "unknown" in result["content"]
    handler.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    ["{invalid json", '{"repeat":2}', '{"text":"你好","repeat":0}', '{"text":"你好","extra":1}'],
    ids=["invalid_json", "missing_required", "invalid_range", "unknown_parameter"],
)
async def test_run_returns_argument_validation_error_without_executing_tool(
    registry: dict[str, dict[str, Any]], handler: Mock, arguments: str
) -> None:
    client = FakeLLM([tool_response([tool_call(arguments=arguments)]), final_response()])

    await Agent(client, registry, workspace_dir=Path.cwd()).run("调用工具")

    result = client.requests[1]["messages"][-1]
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "call_1"
    assert result["content"].startswith("工具参数错误")
    handler.assert_not_called()


@pytest.mark.parametrize("error", [FileNotFoundError("missing.txt"), RuntimeError("工具失败")])
async def test_run_returns_tool_execution_error_to_model(
    registry: dict[str, dict[str, Any]], handler: Mock, error: Exception
) -> None:
    handler.side_effect = error
    client = FakeLLM([tool_response([tool_call()]), final_response("已处理错误")])

    assert await Agent(client, registry, workspace_dir=Path.cwd()).run("调用工具") == "已处理错误"

    result = client.requests[1]["messages"][-1]
    assert result["tool_call_id"] == "call_1"
    assert result["content"].startswith("工具执行错误")
    assert type(error).__name__ in result["content"]
    assert str(error) in result["content"]
    handler.assert_called_once_with(text="你好", repeat=1)


async def test_run_stops_at_max_steps(registry: dict[str, dict[str, Any]], handler: Mock) -> None:
    client = FakeLLM(
        [tool_response([tool_call(f"call_{index}")]) for index in range(3)]
        + [final_response("不应请求这一轮")]
    )
    agent = Agent(client, registry, max_steps=3, workspace_dir=Path.cwd())

    with pytest.raises(RuntimeError) as caught:
        await agent.run("重复调用工具")

    assert "3" in str(caught.value)
    assert len(client.requests) == 3
    assert handler.call_count == 3
    assert len([message for message in agent.messages if message["role"] == "tool"]) == 3


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
async def test_run_rejects_abnormal_response_without_executing_tool(
    registry: dict[str, dict[str, Any]], handler: Mock, finish_reason: str
) -> None:
    client = FakeLLM([tool_response([tool_call()], finish_reason=finish_reason)])
    agent = Agent(client, registry, workspace_dir=Path.cwd())

    with pytest.raises(RuntimeError, match=finish_reason):
        await agent.run("调用工具")

    assert len(client.requests) == 1
    assert [message["role"] for message in agent.messages] == ["system", "user"]
    handler.assert_not_called()


@pytest.mark.parametrize("content", [None, "总结\nEND_TASK"])
async def test_run_rejects_tool_finish_without_calls(
    registry: dict[str, dict[str, Any]], handler: Mock, content: str | None
) -> None:
    response = tool_response([])
    response.choices[0].message.content = content
    client = FakeLLM([response])

    with pytest.raises(RuntimeError, match="没有返回调用信息"):
        await Agent(client, registry, workspace_dir=Path.cwd()).run("调用工具")

    assert len(client.requests) == 1
    handler.assert_not_called()


async def test_run_propagates_model_request_error(
    registry: dict[str, dict[str, Any]], handler: Mock
) -> None:
    error = RuntimeError("模型请求失败")
    client = FakeLLM([error])

    with pytest.raises(RuntimeError, match="模型请求失败") as caught:
        await Agent(client, registry, workspace_dir=Path.cwd()).run("回答问题")

    assert caught.value is error
    assert len(client.requests) == 1
    handler.assert_not_called()


async def test_run_propagates_tool_cancellation(
    registry: dict[str, dict[str, Any]], handler: Mock
) -> None:
    handler.side_effect = asyncio.CancelledError()
    client = FakeLLM([tool_response([tool_call()]), final_response("不应继续请求")])
    agent = Agent(client, registry, workspace_dir=Path.cwd())

    with pytest.raises(asyncio.CancelledError):
        await agent.run("调用工具")

    assert len(client.requests) == 1
    assert not any(message["role"] == "tool" for message in agent.messages)


async def test_run_preserves_history_between_user_turns(
    registry: dict[str, dict[str, Any]], handler: Mock
) -> None:
    client = FakeLLM([final_response("第一轮答案"), final_response("第二轮答案")])
    agent = Agent(client, registry, workspace_dir=Path.cwd())

    assert await agent.run("第一轮问题") == "第一轮答案"
    assert await agent.run("第二轮问题") == "第二轮答案"

    history = client.requests[1]["messages"]
    assert [message["role"] for message in history] == ["system", "user", "assistant", "user"]
    assert [message["content"] for message in history[1:]] == [
        "第一轮问题",
        "第一轮答案\nEND_TASK",
        "第二轮问题",
    ]
    handler.assert_not_called()


async def test_run_executes_sync_tool_outside_event_loop_thread(
    registry: dict[str, dict[str, Any]], handler: Mock
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
