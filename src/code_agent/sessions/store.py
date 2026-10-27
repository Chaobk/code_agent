"""会话元数据的 JSONL 存储；消息追加和加载在后续小步接入。

SessionManager 决定会话身份和业务操作，本模块负责存储位置、读写及资源释放。
每个会话使用一份 JSONL；core 负责运行、记录时机及恢复编排。
"""

import json
from pathlib import Path
from uuid import UUID

from code_agent.sessions.models import SessionMetadata


class SessionStore:
    """会话持久化入口；由核心创建并传给会话管理模块。"""

    def __init__(self, sessions_dir: Path) -> None:
        """绑定存储根目录；初始化不创建目录，create 时才取得文件资源。"""
        self._sessions_dir = sessions_dir.expanduser().resolve()

    def create(self, metadata: SessionMetadata) -> None:
        """新建 <session_id>.jsonl 并写入会话头，成功后返回。

        根目录不存在时创建，以 x 模式打开文件，按版本 1 的 session_start 格式
        保存元数据并 flush。文件在本次操作内关闭；错误传播，已有文件不得覆盖。
        """
        session_id = UUID(metadata.session_id)
        if session_id.version != 4 or session_id.hex != metadata.session_id:
            raise ValueError("会话 ID 必须是 UUID4 的 32 位小写十六进制字符串")

        record = {
            "version": 1,
            "type": "session_start",
            "session_id": metadata.session_id,
            "title": metadata.title,
            "workspace_dir": str(metadata.workspace_dir),
            "created_at": metadata.created_at.isoformat(),
            "updated_at": metadata.updated_at.isoformat(),
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        path = self._sessions_dir / f"{metadata.session_id}.jsonl"
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
            stream.flush()
