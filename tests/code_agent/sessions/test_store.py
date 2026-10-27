"""单会话 JSONL 创建契约，文件副作用限定在临时目录。"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from code_agent.sessions.models import SessionMetadata
from code_agent.sessions.store import SessionStore


@pytest.fixture
def metadata(tmp_path: Path) -> SessionMetadata:
    now = datetime(2026, 10, 27, 8, 0, tzinfo=UTC)
    return SessionMetadata(
        session_id=uuid4().hex,
        title=None,
        workspace_dir=tmp_path / "工作空间",
        created_at=now,
        updated_at=now,
    )


def test_create_writes_one_utf8_header_and_creates_parent_directory(
    tmp_path: Path, metadata: SessionMetadata
) -> None:
    root = tmp_path / "sessions" / "nested"
    store = SessionStore(root)
    assert not root.exists()

    store.create(metadata)

    path = root / f"{metadata.session_id}.jsonl"
    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    assert len(raw.splitlines()) == 1
    assert json.loads(raw.decode("utf-8")) == {
        "version": 1,
        "type": "session_start",
        "session_id": metadata.session_id,
        "title": metadata.title,
        "workspace_dir": str(metadata.workspace_dir),
        "created_at": metadata.created_at.isoformat(),
        "updated_at": metadata.updated_at.isoformat(),
    }
    assert "工作空间" in raw.decode("utf-8")


def test_create_existing_session_does_not_overwrite_original_record(
    tmp_path: Path, metadata: SessionMetadata
) -> None:
    store = SessionStore(tmp_path)
    store.create(metadata)
    path = tmp_path / f"{metadata.session_id}.jsonl"
    original = path.read_bytes()

    with pytest.raises(FileExistsError):
        store.create(replace(metadata, title="不应覆盖原会话"))

    assert path.read_bytes() == original


def test_create_keeps_two_sessions_in_separate_files(
    tmp_path: Path, metadata: SessionMetadata
) -> None:
    store = SessionStore(tmp_path)
    second = replace(metadata, session_id=uuid4().hex)

    store.create(metadata)
    store.create(second)

    assert {path.name for path in tmp_path.iterdir()} == {
        f"{metadata.session_id}.jsonl",
        f"{second.session_id}.jsonl",
    }
    for expected in (metadata, second):
        saved = json.loads((tmp_path / f"{expected.session_id}.jsonl").read_text("utf-8"))
        assert saved["session_id"] == expected.session_id
        assert saved["title"] == expected.title


def test_create_propagates_open_failure_without_retrying(
    tmp_path: Path, metadata: SessionMetadata, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SessionStore(tmp_path)
    error = OSError("会话存储不可写")
    open_file = Mock(side_effect=error)
    monkeypatch.setattr(Path, "open", open_file)

    with pytest.raises(OSError) as caught:
        store.create(metadata)

    assert caught.value is error
    assert open_file.call_count == 1


@pytest.mark.parametrize("operation", ["write", "flush"])
def test_create_closes_file_and_propagates_recording_failure(
    tmp_path: Path, metadata: SessionMetadata, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    error = OSError(f"{operation} 失败")

    class FailingStream(StringIO):
        def write(self, text: str) -> int:
            if operation == "write":
                raise error
            return super().write(text)

        def flush(self) -> None:
            if operation == "flush":
                raise error
            super().flush()

    stream = FailingStream()
    open_file = Mock(return_value=stream)
    monkeypatch.setattr(Path, "open", open_file)

    with pytest.raises(OSError) as caught:
        SessionStore(tmp_path).create(metadata)

    assert caught.value is error
    assert stream.closed
    assert open_file.call_count == 1


@pytest.mark.parametrize("session_id", ["../outside", "invalid", "0" * 32])
def test_create_rejects_invalid_identity_before_creating_storage(
    tmp_path: Path, metadata: SessionMetadata, session_id: str
) -> None:
    root = tmp_path / "sessions"

    with pytest.raises(ValueError):
        SessionStore(root).create(replace(metadata, session_id=session_id))

    assert not root.exists()
