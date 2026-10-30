"""会话历史测试：通过公开方法验证消息配对和快照隔离。"""

from copy import deepcopy
from typing import Any
from unittest.mock import Mock

import pytest

from code_agent.context.history import ConversationHistory
from code_agent.events import ToolResult, ToolStatus


def tool_call(call_id: str, arguments: str = '{"command":"pwd"}') -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "bash", "arguments": arguments},
    }


def assistant_with_calls(*calls: dict[str, Any]) -> dict[str, Any]:
    return {"role": "assistant", "content": None, "tool_calls": list(calls)}


def tool_result(call_id: str, content: str = "执行结果") -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def test_model_history_rejects_unpaired_calls_but_display_history_is_available() -> None:
    history = ConversationHistory()
    message = assistant_with_calls(tool_call("call_1"))
    history.add_message(message)

    assert history.get_messages() == [message]
    assert history.get_pending_calls()["call_1"].name == "bash"
    with pytest.raises(RuntimeError, match="尚未配对结果"):
        history.get_model_messages()


def test_history_preserves_message_order_and_continues_after_all_results() -> None:
    history = ConversationHistory()
    messages = [
        {"role": "system", "content": "系统指令"},
        {"role": "user", "content": "检查项目"},
        assistant_with_calls(tool_call("first"), tool_call("second")),
        tool_result("first"),
    ]
    for message in messages:
        history.add_message(message)

    assert list(history.get_pending_calls()) == ["second"]
    with pytest.raises(RuntimeError):
        history.get_model_messages()

    remaining = [
        tool_result("second"),
        {"role": "assistant", "content": "检查完成", "tool_calls": None},
        {"role": "user", "content": "继续修复"},
    ]
    for message in remaining:
        history.add_message(message)

    assert history.get_pending_calls() == {}
    assert history.get_model_messages() == messages + remaining


@pytest.mark.parametrize("status", list(ToolStatus))
def test_any_recorded_tool_result_resolves_pending_call(status: ToolStatus) -> None:
    history = ConversationHistory()
    history.add_message(assistant_with_calls(tool_call("call_1")))
    result = ToolResult(output={"detail": "工具观察"}, status=status, truncated=True)
    message = tool_result("call_1", result.model_dump_json())

    history.add_message(message)

    assert history.get_pending_calls() == {}
    assert history.get_model_messages()[-1] == message


@pytest.mark.parametrize("role", ["system", "user", "assistant"])
def test_pending_calls_prevent_new_dialogue_without_changing_history(role: str) -> None:
    history = ConversationHistory()
    history.add_message(assistant_with_calls(tool_call("call_1")))
    messages = history.get_messages()
    pending = history.get_pending_calls()

    with pytest.raises(ValueError):
        history.add_message({"role": role, "content": "新的消息"})

    assert history.get_messages() == messages
    assert history.get_pending_calls() == pending


def test_unknown_and_duplicate_results_do_not_consume_other_pending_calls() -> None:
    history = ConversationHistory()
    history.add_message(assistant_with_calls(tool_call("first"), tool_call("second")))
    history.add_message(tool_result("first"))
    messages = history.get_messages()
    pending = history.get_pending_calls()

    for call_id in ("unknown", "first"):
        with pytest.raises(ValueError):
            history.add_message(tool_result(call_id))
        assert history.get_messages() == messages
        assert history.get_pending_calls() == pending

    history.add_message(tool_result("second"))
    assert history.get_pending_calls() == {}


@pytest.mark.parametrize(
    "invalid_call",
    [
        tool_call("valid"),
        {"id": "broken", "type": "function", "function": {"name": "bash"}},
        tool_call(" "),
        {"id": "broken", "type": "function", "function": None},
        {"id": "broken", "type": "other"},
    ],
    ids=["duplicate", "missing-arguments", "blank-id", "invalid-function", "unsupported-type"],
)
def test_invalid_second_call_does_not_partially_register_batch(
    invalid_call: dict[str, Any],
) -> None:
    history = ConversationHistory()
    original = {"role": "user", "content": "执行任务"}
    history.add_message(original)

    with pytest.raises(ValueError):
        history.add_message(assistant_with_calls(tool_call("valid"), invalid_call))

    assert history.get_pending_calls() == {}
    assert history.get_model_messages() == [original]


def test_input_and_returned_snapshots_cannot_modify_history_or_pending_calls() -> None:
    history = ConversationHistory()
    message = assistant_with_calls(tool_call("call_1"))
    history.add_message(message)

    message["tool_calls"][0]["function"]["name"] = "改动输入"
    displayed = history.get_messages()
    displayed[0]["tool_calls"][0]["function"]["name"] = "改动快照"
    pending = history.get_pending_calls()
    pending["call_1"].name = "改动 pending 快照"
    pending.clear()

    assert history.get_messages()[0]["tool_calls"][0]["function"]["name"] == "bash"
    assert history.get_pending_calls()["call_1"].name == "bash"
    history.add_message(tool_result("call_1"))
    model_messages = history.get_model_messages()
    model_messages[0]["tool_calls"].clear()
    assert len(history.get_model_messages()[0]["tool_calls"]) == 1


def test_history_keeps_invalid_argument_json_for_registry_to_handle() -> None:
    history = ConversationHistory()
    message = assistant_with_calls(tool_call("call_1", "{invalid json"))

    history.add_message(message)

    assert history.get_pending_calls()["call_1"].arguments == "{invalid json"
    assert history.get_messages() == [message]


@pytest.mark.parametrize("field", ["id", "name", "arguments"])
def test_invalid_call_field_type_does_not_change_history_or_pending(field: str) -> None:
    history = ConversationHistory()
    history.add_message({"role": "user", "content": "执行任务"})
    messages = history.get_messages()
    pending = history.get_pending_calls()
    invalid_call = tool_call("invalid")
    if field == "id":
        invalid_call[field] = b"invalid"
    else:
        invalid_call["function"][field] = b"invalid"

    with pytest.raises(ValueError, match="字段类型错误"):
        history.add_message(assistant_with_calls(tool_call("valid"), invalid_call))

    assert history.get_model_messages() == messages
    assert history.get_pending_calls() == pending


@pytest.mark.parametrize(
    "message",
    [
        {"role": "unknown", "content": "内容"},
        {"role": "user", "content": None},
        {"role": "assistant", "content": 1},
        {"role": "assistant", "content": None, "tool_calls": "invalid"},
        {"role": "user", "content": "内容", "tool_calls": [tool_call("call_1")]},
        tool_result("unknown"),
    ],
)
def test_invalid_message_leaves_empty_history_unchanged(message: dict[str, Any]) -> None:
    history = ConversationHistory()

    with pytest.raises(ValueError):
        history.add_message(message)

    assert history.get_model_messages() == []
    assert history.get_pending_calls() == {}


def test_invalid_result_content_does_not_remove_pending_call() -> None:
    history = ConversationHistory()
    history.add_message(assistant_with_calls(tool_call("call_1")))
    messages = history.get_messages()

    with pytest.raises(ValueError):
        history.add_message({"role": "tool", "tool_call_id": "call_1", "content": None})

    assert history.get_messages() == messages
    assert list(history.get_pending_calls()) == ["call_1"]


def test_save_callback_receives_each_committed_message_once_in_order() -> None:
    saved: list[dict[str, Any]] = []
    history = ConversationHistory(save_message=lambda message: saved.append(deepcopy(message)))
    messages = [
        {"role": "user", "content": "检查项目"},
        assistant_with_calls(tool_call("call_1")),
        tool_result("call_1"),
    ]

    for message in messages:
        history.add_message(message)
    assert history.get_messages() == messages
    assert history.get_model_messages() == messages
    assert history.get_pending_calls() == {}
    assert saved == messages
    messages[0]["content"] = "调用方修改"
    assert saved[0]["content"] == "检查项目"


@pytest.mark.parametrize(
    "message",
    [
        {"role": "invalid", "content": "错误角色"},
        assistant_with_calls(tool_call("duplicate"), tool_call("duplicate")),
        tool_result("unknown"),
    ],
)
def test_invalid_message_does_not_reach_save_callback(message: dict[str, Any]) -> None:
    save = Mock()
    history = ConversationHistory(save_message=save)

    with pytest.raises(ValueError):
        history.add_message(message)

    save.assert_not_called()
    assert history.get_messages() == []
    assert history.get_pending_calls() == {}


@pytest.mark.parametrize(
    "message",
    [
        {"role": "user", "content": "新消息"},
        assistant_with_calls(tool_call("call_1")),
        tool_result("call_1"),
    ],
)
def test_save_failure_leaves_history_and_pending_calls_unchanged(
    message: dict[str, Any],
) -> None:
    save = Mock()
    history = ConversationHistory(save_message=save)
    if message["role"] == "tool":
        history.add_message(assistant_with_calls(tool_call("call_1")))
    previous = history.get_messages()
    pending = history.get_pending_calls()
    save.reset_mock()
    error = OSError("保存失败")
    save.side_effect = error

    with pytest.raises(OSError) as caught:
        history.add_message(message)

    assert caught.value is error
    save.assert_called_once_with(message)
    assert history.get_messages() == previous
    assert history.get_pending_calls() == pending
