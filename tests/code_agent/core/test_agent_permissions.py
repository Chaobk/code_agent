"""权限接线、结果配对和执行前中断；只使用假模型与假工具。"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from code_agent.approval.gate import ApprovalGate
from code_agent.approval.policy import ApprovalPolicy
from code_agent.core.agent import Agent
from code_agent.events import RunResult, ToolCallEvent, ToolResult, ToolResultEvent, ToolStatus
from code_agent.safety.permission import Decision
from code_agent.sessions.manager import SessionManager
from code_agent.tools.registry import ToolRegistry
from code_agent.tools.spec import ToolSpec
from tests.code_agent.core.test_agent import (
    EchoArgs,
    FakeLLM,
    final_response,
    tool_call,
    tool_response,
)


@pytest.mark.parametrize(
    "scenario", ["allow", "deny", "approve", "reject", "unavailable", "missing"]
)
async def test_permission_decision_controls_execution_and_returns_paired_observation(
    scenario: str, capsys: pytest.CaptureFixture[str]
) -> None:
    handler = AsyncMock(return_value=ToolResult(output="executed", status=ToolStatus.COMPLETED))
    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "返回文本", EchoArgs, handler))
    policy = Mock(spec=ApprovalPolicy)
    policy.evaluate.return_value = {
        "allow": Decision.ALLOW,
        "deny": Decision.DENY,
    }.get(scenario, Decision.ASK)
    policy.scope_key.return_value = "hello"
    policy.describe.return_value = "echo hello"
    prompt = Mock(return_value="o" if scenario == "approve" else "r")
    client = FakeLLM(
        [tool_response([tool_call(arguments='{"text":"hello"}')]), final_response("done")]
    )
    agent = Agent(
        client,
        registry,
        workspace_dir=Path.cwd(),
        session_manager=SessionManager(Path.cwd(), None),
        approval_gate=ApprovalGate(None if scenario == "unavailable" else prompt),
        approval_policies={} if scenario == "missing" else {"echo": policy},
    )
    events = []

    def observe(event: object) -> None:
        if isinstance(event, ToolResultEvent):
            assert agent.session_manager.get_pending_calls() == {}
            assert agent.session_manager.get_messages()[-1]["tool_call_id"] == event.tool_call_id
        events.append(event)

    assert await agent.run("call echo", on_event=observe) == RunResult(
        status="completed", text="done"
    )

    results = [event for event in events if isinstance(event, ToolResultEvent)]
    assert len(results) == 1
    assert results[0].tool_call_id == "call_1"
    observation = results[0].tool_result
    assert (
        ToolResult.model_validate_json(client.requests[1]["messages"][-1]["content"]) == observation
    )
    assert [
        type(event) for event in events if isinstance(event, (ToolCallEvent, ToolResultEvent))
    ] == [
        ToolCallEvent,
        ToolResultEvent,
    ]
    if scenario in {"allow", "approve"}:
        handler.assert_awaited_once_with(text="hello", repeat=1)
        assert observation.output == "executed"
    else:
        handler.assert_not_awaited()
        assert observation.status == ToolStatus.ERROR
        assert observation.output["execution"] == "not_started"
        assert observation.output["error"]
        assert observation.output["reason"] == (
            "approval_unavailable" if scenario == "missing" else "permission_denied"
        )
    if scenario in {"approve", "reject"}:
        prompt.assert_called_once_with("echo hello")
    else:
        prompt.assert_not_called()
    if scenario == "missing":
        policy.evaluate.assert_not_called()
    else:
        policy.evaluate.assert_called_once_with({"text": "hello", "repeat": 1})
    assert agent.session_manager.get_pending_calls() == {}
    assert capsys.readouterr().out == ""


async def test_gate_session_approval_survives_multiple_runs() -> None:
    handler = AsyncMock(return_value="done")
    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "返回文本", EchoArgs, handler))
    policy = Mock(spec=ApprovalPolicy)
    policy.evaluate.return_value = Decision.ASK
    policy.scope_key.return_value = "same-action"
    policy.describe.return_value = "echo hello"
    prompt = Mock(return_value="a")
    client = FakeLLM(
        [
            tool_response([tool_call("first")]),
            final_response(),
            tool_response([tool_call("second")]),
            final_response(),
        ]
    )
    agent = Agent(
        client,
        registry,
        workspace_dir=Path.cwd(),
        session_manager=SessionManager(Path.cwd(), None),
        approval_gate=ApprovalGate(prompt),
        approval_policies={"echo": policy},
    )

    await agent.run("first")
    await agent.run("second")

    assert handler.await_count == 2
    assert policy.evaluate.call_count == 2
    prompt.assert_called_once()
    assert agent.session_manager.get_pending_calls() == {}


@pytest.mark.parametrize("cancel", [True, False], ids=["cancelled", "internal-error"])
async def test_pre_execution_interruption_preserves_known_results_and_settles_unstarted_calls(
    cancel: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = AsyncMock(return_value="known result")
    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "返回文本", EchoArgs, handler))
    policy = Mock(spec=ApprovalPolicy)
    policy.evaluate.return_value = Decision.ALLOW
    client = FakeLLM(
        [
            tool_response(
                [
                    tool_call(name="echo", call_id=name, arguments=f'{{"text":"{name}"}}')
                    for name in ("first", "second", "third")
                ]
            ),
            final_response("recovered"),
        ]
    )
    agent = Agent(
        client,
        registry,
        workspace_dir=Path.cwd(),
        session_manager=SessionManager(Path.cwd(), None),
        approval_gate=ApprovalGate(),
        approval_policies={"echo": policy},
    )
    original_check = agent._before_tool_execute
    waiting = asyncio.Event()
    error = RuntimeError("permission hook failed")

    async def before(name: str, args: dict) -> ToolResult | None:
        if args["text"] == "second":
            if not cancel:
                raise error
            waiting.set()
            await asyncio.Future()
        return await original_check(name, args)

    monkeypatch.setattr(agent, "_before_tool_execute", before)
    if cancel:
        task = asyncio.create_task(agent.run("batch"))
        try:
            await asyncio.wait_for(waiting.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    else:
        with pytest.raises(RuntimeError) as caught:
            await agent.run("batch")
        assert caught.value is error

    handler.assert_awaited_once_with(text="first", repeat=1)
    messages = [
        message
        for message in agent.session_manager.get_model_messages()
        if message["role"] == "tool"
    ]
    assert [message["tool_call_id"] for message in messages] == ["first", "second", "third"]
    observations = [ToolResult.model_validate_json(message["content"]) for message in messages]
    assert observations[0].output == "known result"
    assert [result.output["execution"] for result in observations[1:]] == [
        "not_started",
        "not_started",
    ]
    assert agent.session_manager.get_pending_calls() == {}
    assert len(client.requests) == 1
    assert await agent.run("continue") == RunResult(status="completed", text="recovered")
    assert handler.await_count == 1
