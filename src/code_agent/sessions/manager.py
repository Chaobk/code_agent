"""管理当前会话，内部创建 Store 处理持久化。"""

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from code_agent.context.history import ConversationHistory
from code_agent.events import ToolCall
from code_agent.sessions.models import SessionMetadata
from code_agent.sessions.store import SessionStore


class SessionManager:
    """统一管理会话身份、内存历史与保存状态；不执行工具。"""

    def __init__(self, workspace_dir: Path, sessions_dir: Path | None) -> None:
        """创建新会话与 History；目录为 None 时只使用内存，否则立即保存会话头。"""
        workspace_dir = workspace_dir.expanduser().resolve()
        self._history = ConversationHistory()
        self._store = SessionStore(sessions_dir) if sessions_dir is not None else None
        self._storage_error: Exception | None = None
        self._active_run_id: str | None = None
        now = datetime.now(UTC)
        self._metadata = SessionMetadata(
            session_id=uuid4().hex,
            title=None,
            workspace_dir=workspace_dir,
            created_at=now,
            updated_at=now,
        )
        if self._store is not None:
            self._store.create(self._metadata)

    def start_run(self) -> str:
        """保存运行开始；同一会话不允许并发运行。"""
        if self._storage_error is not None:
            raise self._storage_error
        if self._active_run_id is not None:
            raise RuntimeError("会话已有运行进行中")
        run_id = uuid4().hex
        self._append_record({"type": "run_start", "run_id": run_id})
        self._active_run_id = run_id
        return run_id

    def end_run(self, run_id: str, *, status: str, error: str | None) -> None:
        """保存与当前运行匹配的终态。"""
        if self._storage_error is not None:
            raise self._storage_error
        if run_id != self._active_run_id or self._active_run_id is None:
            raise ValueError("运行 ID 与当前运行不匹配")
        self._append_record({"type": "run_end", "run_id": run_id, "status": status, "error": error})
        self._active_run_id = None

    def _append_record(self, record: dict[str, Any]) -> None:
        if self._store is not None:
            try:
                self._store.append_record(self._metadata.session_id, record)
            except Exception as exc:
                self._storage_error = exc
                raise

    def append_message(self, message: dict[str, Any]) -> None:
        """先校验并保留内存事实，再保存；文件失败后只允许内存工具收尾。"""
        saved = deepcopy(message)
        if self._storage_error is not None:
            if saved.get("role") != "tool":
                raise self._storage_error
            self._history.add_message(saved)
            return
        self._history.add_message(saved)
        if self._store is not None:
            try:
                self._store.append_message(self._metadata.session_id, saved)
            except Exception as exc:
                self._storage_error = exc
                raise

    @property
    def storage_error(self) -> Exception | None:
        """首次保存失败，供运行收尾识别；不会自动重试已失败的文件。"""
        return self._storage_error

    def get_messages(self) -> list[dict[str, Any]]:
        """返回完整内存消息的独立快照，保存失败后仍可检查。"""
        return self._history.get_messages()

    def get_pending_calls(self) -> dict[str, ToolCall]:
        """返回待结算调用的独立快照。"""
        return self._history.get_pending_calls()

    def get_model_messages(self) -> list[dict[str, Any]]:
        """返回已配对模型历史；保存失败的会话不能继续请求模型。"""
        if self._storage_error is not None:
            raise self._storage_error
        return self._history.get_model_messages()
