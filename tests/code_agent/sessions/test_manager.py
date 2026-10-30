"""通过 Manager 验证当前会话与实际文件，副作用限定在临时目录。"""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import pytest

from code_agent.sessions.manager import SessionManager
from code_agent.sessions.store import SessionStore


def test_initialization_saves_header_with_identity_workspace_and_initial_timestamps(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sessions"
    workspace = tmp_path / "project" / ".." / "workspace"
    before = datetime.now(UTC)

    SessionManager(workspace, root)

    after = datetime.now(UTC)
    files = list(root.glob("*.jsonl"))
    assert len(files) == 1
    raw = files[0].read_bytes()
    assert raw.endswith(b"\n")
    assert len(raw.splitlines()) == 1
    saved = json.loads(raw)
    assert UUID(saved["session_id"]).version == 4
    assert files[0].stem == saved["session_id"]
    assert saved["type"] == "session_start"
    assert saved["title"] is None
    assert saved["workspace_dir"] == str(workspace.resolve())
    created_at = datetime.fromisoformat(saved["created_at"])
    assert created_at.utcoffset() is not None
    assert before <= created_at <= after
    assert saved["created_at"] == saved["updated_at"]
    assert not workspace.resolve().exists()


def test_directories_are_bound_at_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    manager = SessionManager(Path("workspace"), Path("sessions"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    message = {"role": "user", "content": "切换当前目录后继续"}

    manager.append_message(message)

    files = list((tmp_path / "sessions").glob("*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text("utf-8").splitlines()]
    assert rows[0]["workspace_dir"] == str(tmp_path / "workspace")
    assert rows[1]["message"] == message
    assert not (elsewhere / "sessions").exists()


def test_workspace_expands_home_directory(tmp_path: Path) -> None:
    root = tmp_path / "sessions"

    SessionManager(Path("~") / "workspace", root)

    saved = json.loads(next(root.glob("*.jsonl")).read_text("utf-8"))
    assert saved["workspace_dir"] == str((Path.home() / "workspace").resolve())


def test_managers_keep_sessions_independent_in_same_workspace(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    workspace = tmp_path / "workspace"
    first = SessionManager(workspace, root)
    first_path = next(root.glob("*.jsonl"))
    second = SessionManager(workspace, root)
    second_path = next(path for path in root.glob("*.jsonl") if path != first_path)
    first_message = {"role": "user", "content": "第一份"}
    second_message = {"role": "user", "content": "第二份"}

    second.append_message(second_message)
    first.append_message(first_message)

    first_rows = [json.loads(line) for line in first_path.read_text("utf-8").splitlines()]
    second_rows = [json.loads(line) for line in second_path.read_text("utf-8").splitlines()]
    assert len(first_rows) == len(second_rows) == 2
    assert first_rows[0]["session_id"] != second_rows[0]["session_id"]
    assert first_rows[0]["workspace_dir"] == second_rows[0]["workspace_dir"]
    assert first_rows[1] == {"type": "message", "message": first_message}
    assert second_rows[1] == {"type": "message", "message": second_message}


def test_append_preserves_message_order_and_tool_call_structure(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    manager = SessionManager(tmp_path / "workspace", root)
    messages = [
        {"role": "user", "content": "查看文件\n然后解释"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command":"pwd"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "工作目录"},
    ]

    for message in messages:
        manager.append_message(message)

    raw = next(root.glob("*.jsonl")).read_bytes()
    assert raw.endswith(b"\n")
    rows = [json.loads(line) for line in raw.splitlines()]
    assert rows[0]["type"] == "session_start"
    assert rows[1:] == [{"type": "message", "message": message} for message in messages]


def test_initialization_propagates_storage_failure_without_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "sessions"
    error = OSError("会话存储不可写")
    create = Mock(side_effect=error)
    monkeypatch.setattr(SessionStore, "create", create)

    with pytest.raises(OSError) as caught:
        SessionManager(tmp_path / "workspace", root)

    assert caught.value is error
    assert create.call_count == 1
    assert not root.exists()


def test_failed_initialization_keeps_previous_session_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "sessions"
    manager = SessionManager(tmp_path / "workspace", root)
    original = next(root.glob("*.jsonl"))
    monkeypatch.setattr(SessionStore, "create", Mock(side_effect=OSError("创建失败")))

    with pytest.raises(OSError, match="创建失败"):
        SessionManager(tmp_path / "workspace", root)
    message = {"role": "user", "content": "继续原会话"}
    manager.append_message(message)

    assert list(root.glob("*.jsonl")) == [original]
    assert json.loads(original.read_text("utf-8").splitlines()[1])["message"] == message


def test_append_propagates_storage_failure_without_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "sessions"
    manager = SessionManager(tmp_path / "workspace", root)
    header = json.loads(next(root.glob("*.jsonl")).read_text("utf-8"))
    error = OSError("追加失败")
    append = Mock(side_effect=error)
    monkeypatch.setattr(SessionStore, "append_message", append)
    message = {"role": "user", "content": "你好"}

    with pytest.raises(OSError) as caught:
        manager.append_message(message)

    assert caught.value is error
    append.assert_called_once_with(header["session_id"], message)
    assert manager.get_messages() == [message]
    assert manager.storage_error is error


def test_memory_only_manager_validates_messages_and_returns_independent_snapshots() -> None:
    manager = SessionManager(Path.cwd(), None)
    message = {"role": "user", "content": "原始消息"}
    manager.append_message(message)
    message["content"] = "调用方修改"
    snapshot = manager.get_messages()
    snapshot[0]["content"] = "快照修改"

    assert manager.get_model_messages() == [{"role": "user", "content": "原始消息"}]
    assert manager.get_pending_calls() == {}


@pytest.mark.parametrize("role", ["invalid", "tool"])
def test_invalid_message_does_not_reach_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    manager = SessionManager(tmp_path, tmp_path / "sessions")
    append = Mock()
    monkeypatch.setattr(SessionStore, "append_message", append)

    with pytest.raises(ValueError):
        manager.append_message({"role": role, "content": "无效消息"})

    append.assert_not_called()
    assert manager.get_messages() == []


def test_failed_storage_keeps_facts_allows_settlement_and_blocks_new_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = SessionManager(tmp_path, tmp_path / "sessions")
    assistant = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "first", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
            {"id": "second", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
        ],
    }
    manager.append_message(assistant)
    pending = manager.get_pending_calls()
    pending.clear()
    assert set(manager.get_pending_calls()) == {"first", "second"}
    error = OSError("磁盘不可写")
    append = Mock(side_effect=error)
    monkeypatch.setattr(SessionStore, "append_message", append)
    first = {"role": "tool", "tool_call_id": "first", "content": "真实结果"}

    with pytest.raises(OSError) as caught:
        manager.append_message(first)
    assert caught.value is error
    second = {"role": "tool", "tool_call_id": "second", "content": "未启动"}
    manager.append_message(second)

    assert manager.get_messages() == [assistant, first, second]
    assert manager.get_pending_calls() == {}
    append.assert_called_once()
    for operation in (
        lambda: manager.append_message({"role": "user", "content": "新任务"}),
        manager.get_model_messages,
    ):
        with pytest.raises(OSError) as blocked:
            operation()
        assert blocked.value is error
    assert manager.get_messages() == [assistant, first, second]
