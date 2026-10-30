"""会话身份和可持久化的元数据。"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class SessionMetadata:
    """标题在会话创建时未知，使用 None 表示尚未命名。"""

    session_id: str
    title: str | None
    workspace_dir: Path
    created_at: datetime
    updated_at: datetime
