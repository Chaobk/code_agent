"""会话创建契约：使用内存存储验证业务行为，不访问真实会话目录。"""

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from code_agent.sessions.manager import SessionManager
from code_agent.sessions.models import SessionMetadata
from code_agent.sessions.store import SessionStore


class RecordingStore(SessionStore):
    def __init__(self, error: Exception | None = None) -> None:
        self.created: list[SessionMetadata] = []
        self.error = error

    def create(self, metadata: SessionMetadata) -> None:
        self.created.append(deepcopy(metadata))
        if self.error is not None:
            raise self.error


def test_create_saves_metadata_with_identity_workspace_and_initial_timestamps() -> None:
    store = RecordingStore()
    manager = SessionManager(store)
    workspace = Path("project") / ".." / "workspace"
    before = datetime.now(UTC)

    metadata = manager.create(workspace)

    after = datetime.now(UTC)
    assert isinstance(metadata, SessionMetadata)
    assert UUID(metadata.session_id).version == 4
    assert metadata.title is None
    assert metadata.workspace_dir == workspace.resolve()
    assert metadata.created_at.utcoffset() is not None
    assert before <= metadata.created_at <= after
    assert metadata.updated_at == metadata.created_at
    assert store.created == [metadata]


def test_create_expands_home_directory() -> None:
    store = RecordingStore()

    metadata = SessionManager(store).create(Path("~") / "workspace")

    assert metadata.workspace_dir == (Path.home() / "workspace").resolve()
    assert store.created == [metadata]


def test_create_keeps_sessions_independent_in_the_same_workspace() -> None:
    store = RecordingStore()
    manager = SessionManager(store)

    first = manager.create(Path.cwd())
    second = manager.create(Path.cwd())

    assert first.session_id != second.session_id
    assert first.workspace_dir == second.workspace_dir
    assert store.created == [first, second]
    assert first.title is None
    assert second.title is None


def test_create_propagates_storage_failure_without_retrying() -> None:
    error = OSError("会话存储不可写")
    store = RecordingStore(error)

    with pytest.raises(OSError) as caught:
        SessionManager(store).create(Path.cwd())

    assert caught.value is error
    assert len(store.created) == 1


def test_create_persists_untitled_session_through_real_store(tmp_path: Path) -> None:
    manager = SessionManager(SessionStore(tmp_path / "sessions"))

    metadata = manager.create(tmp_path / "workspace")

    path = tmp_path / "sessions" / f"{metadata.session_id}.jsonl"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["title"] is None
    assert saved["session_id"] == metadata.session_id
    assert saved["workspace_dir"] == str(metadata.workspace_dir)
    assert saved["created_at"] == saved["updated_at"]
