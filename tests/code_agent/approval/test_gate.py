"""审批三态、单次确认和会话授权的行为测试。"""

from unittest.mock import Mock

import pytest

from code_agent.approval.gate import ApprovalGate
from code_agent.approval.policy import ApprovalPolicy
from code_agent.safety.permission import Decision


@pytest.fixture
def policy() -> Mock:
    policy = Mock(spec=ApprovalPolicy)
    policy.evaluate.return_value = Decision.ASK
    policy.scope_key.return_value = "same-action"
    policy.describe.return_value = "待执行的具体操作"
    return policy


@pytest.mark.parametrize("decision", [Decision.ALLOW, Decision.DENY])
def test_allow_and_deny_do_not_prompt_or_consult_cache(policy: Mock, decision: Decision) -> None:
    policy.evaluate.return_value = decision
    prompt = Mock(side_effect=AssertionError("确定性决定不应询问"))

    allowed, reason = ApprovalGate(prompt).check("tool", {"target": "a"}, policy)

    assert allowed is (decision is Decision.ALLOW)
    assert bool(reason) is (decision is Decision.DENY)
    policy.evaluate.assert_called_once_with({"target": "a"})
    policy.scope_key.assert_not_called()
    prompt.assert_not_called()


@pytest.mark.parametrize("choice", ["o", "a", "r", "", "invalid"])
def test_ask_requires_an_explicit_approval(policy: Mock, choice: str) -> None:
    prompt = Mock(return_value=choice)

    allowed, reason = ApprovalGate(prompt).check("tool", {}, policy)

    assert allowed is (choice in {"o", "a"})
    assert bool(reason) is (choice not in {"o", "a"})
    prompt.assert_called_once_with("待执行的具体操作")


def test_ask_without_confirmation_capability_is_denied(policy: Mock) -> None:
    allowed, reason = ApprovalGate().check("tool", {}, policy)

    assert allowed is False
    assert "不能进行交互确认" in reason


def test_once_approval_does_not_authorize_the_next_call(policy: Mock) -> None:
    prompt = Mock(side_effect=["o", "r"])
    gate = ApprovalGate(prompt)

    assert gate.check("tool", {}, policy)[0] is True
    assert gate.check("tool", {}, policy)[0] is False
    assert prompt.call_count == 2


def test_session_approval_is_scoped_by_tool_and_scope_and_resettable(policy: Mock) -> None:
    prompt = Mock(return_value="a")
    gate = ApprovalGate(prompt)

    assert gate.check("first", {}, policy)[0] is True
    assert gate.check("first", {}, policy)[0] is True
    prompt.assert_called_once()

    assert gate.check("second", {}, policy)[0] is True
    policy.scope_key.return_value = "different-action"
    assert gate.check("first", {}, policy)[0] is True
    gate.reset()
    assert gate.check("first", {}, policy)[0] is True
    assert prompt.call_count == 4
    assert policy.evaluate.call_count == 5


def test_cached_approval_cannot_override_a_new_hard_denial(policy: Mock) -> None:
    prompt = Mock(return_value="a")
    gate = ApprovalGate(prompt)

    assert gate.check("tool", {}, policy)[0] is True
    policy.evaluate.return_value = Decision.DENY
    allowed, reason = gate.check("tool", {}, policy)

    assert allowed is False
    assert "硬拒绝" in reason
    prompt.assert_called_once()


def test_policy_failure_is_not_converted_into_a_user_denial(policy: Mock) -> None:
    error = RuntimeError("策略内部错误")
    policy.evaluate.side_effect = error
    prompt = Mock()

    with pytest.raises(RuntimeError) as caught:
        ApprovalGate(prompt).check("tool", {}, policy)

    assert caught.value is error
    prompt.assert_not_called()
