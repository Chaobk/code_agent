"""会话业务操作，具体存储通过传入的 SessionStore 完成。"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from code_agent.sessions.models import SessionMetadata
from code_agent.sessions.store import SessionStore


class SessionManager:
    """管理会话身份和元数据；不创建 Agent 或执行工具。"""

    def __init__(self, store: SessionStore) -> None:
        self._store = store

    def create(self, workspace_dir: Path) -> SessionMetadata:
        """创建尚无标题的会话，保存成功后返回；存储失败原样传播。"""
        workspace_dir = workspace_dir.expanduser().resolve()
        now = datetime.now(UTC)
        metadata = SessionMetadata(
            session_id=uuid4().hex,
            title=None,
            workspace_dir=workspace_dir,
            created_at=now,
            updated_at=now,
        )
        self._store.create(metadata)
        return metadata
