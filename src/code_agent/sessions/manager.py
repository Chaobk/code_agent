"""管理当前会话，内部创建 Store 处理持久化。"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from code_agent.sessions.models import SessionMetadata
from code_agent.sessions.store import SessionStore


class SessionManager:
    """管理会话身份和元数据；不创建 Agent 或执行工具。"""

    def __init__(self, workspace_dir: Path, sessions_dir: Path) -> None:
        """创建并保存一份新会话；存储失败时初始化直接抛出异常。"""
        workspace_dir = workspace_dir.expanduser().resolve()
        self._store = SessionStore(sessions_dir)
        now = datetime.now(UTC)
        self._metadata = SessionMetadata(
            session_id=uuid4().hex,
            title=None,
            workspace_dir=workspace_dir,
            created_at=now,
            updated_at=now,
        )
        self._store.create(self._metadata)

    def append_message(self, message: dict[str, Any]) -> None:
        """向初始化时创建的会话追加消息。"""
        self._store.append_message(self._metadata.session_id, message)
