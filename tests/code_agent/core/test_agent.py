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
from code_agent.events import ToolExecutionCancelled, ToolResult, ToolStatus
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
    assert history[-1]["content"] == content
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
    assert history[-2]["content"] is None
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

    with pytest.raises(RuntimeError) as caught:
        await agent.run("重复调用工具")

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

    with pytest.raises(RuntimeError, match=finish_reason):
        await agent.run("调用工具")

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

    with pytest.raises(RuntimeError, match="模型请求失败") as caught:
        await Agent(client, registry, workspace_dir=Path.cwd()).run("回答问题")

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
    task = asyncio.create_task(agent.run("execute batch"))

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
    assert agent.history.get_pending_calls() == {}
    history = agent.history.get_model_messages()
    results = [message for message in history if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in results] == [call["id"] for call in calls]

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

    assert await agent.run("continue") == "resumed"
    assert len(client.requests) == 2
    assert client.requests[1]["messages"] == [*history, {"role": "user", "content": "continue"}]
    assert executed == texts[: cancel_index + 1]


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

    with pytest.raises(ToolExecutionCancelled) as caught:
        await agent.run("execute batch")

    assert caught.value is cancellation
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
