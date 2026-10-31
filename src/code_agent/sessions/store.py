"""会话元数据与消息的 JSONL 存储；只读读取在后续小步接入。

SessionManager 内部创建本模块，决定会话身份和业务操作；本模块负责文件读写。
每个会话使用一份 JSONL；core 负责运行、记录时机及恢复编排。
"""

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from code_agent.sessions.models import SessionMetadata


class SessionStore:
    """Manager 内部使用的文件存储；按会话 ID 定位 JSONL。"""

    def __init__(self, sessions_dir: Path) -> None:
        """绑定存储根目录；初始化不创建目录，create 时才取得文件资源。"""
        self._sessions_dir = sessions_dir.expanduser().resolve()

    def create(self, metadata: SessionMetadata) -> None:
        """新建 <session_id>.jsonl 并写入会话头，成功后返回。

        根目录不存在时创建，以 x 模式打开文件，按 session_start 格式
        保存元数据并 flush。文件在本次操作内关闭；错误传播，已有文件不得覆盖。
        """
        path = self._session_path(metadata.session_id)

        record = {
            "type": "session_start",
            "session_id": metadata.session_id,
            "title": metadata.title,
            "workspace_dir": str(metadata.workspace_dir),
            "created_at": metadata.created_at.isoformat(),
            "updated_at": metadata.updated_at.isoformat(),
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
            stream.flush()

    def append_message(self, session_id: str, message: dict[str, Any]) -> None:
        """追加一条消息并 flush；使用 a 模式，错误原样传播。"""
        self.append_record(session_id, {"type": "message", "message": message})

    def append_record(self, session_id: str, record: dict[str, Any]) -> None:
        """追加一条 JSONL 记录并 flush。"""
        path = self._session_path(session_id)
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
            stream.flush()

    def _session_path(self, session_id: str) -> Path:
        """校验身份后生成文件路径，避免将任意字符串解释为路径。"""
        parsed_id = UUID(session_id)
        if parsed_id.version != 4 or parsed_id.hex != session_id:
            raise ValueError("会话 ID 必须是 UUID4 的 32 位小写十六进制字符串")
        return self._sessions_dir / f"{session_id}.jsonl"
