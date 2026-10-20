"""执行目录与 Bash 可执行文件的校验、路径解析。"""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExecutionEnvironment:
    workspace_dir: Path
    bash_path: str


def resolve_execution_environment(*, workspace_dir: Path, bash_path: str) -> ExecutionEnvironment:
    try:
        resolved_workspace = workspace_dir.expanduser().resolve()

        if not resolved_workspace.is_dir():
            raise ValueError(f"workspace_dir 必须是已存在的目录：{resolved_workspace}")
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"workspace_dir 路径处理失败: {exc}") from exc

    if not bash_path.strip():
        raise ValueError("bash_path 不能为空")

    try:
        candidate = os.path.expanduser(bash_path)
        found = shutil.which(candidate)

        if found is None:
            raise ValueError(f"bash_path 无法找到可执行文件：{candidate}")

        resolved_bash = Path(found).resolve()

        if not resolved_bash.is_file():
            raise ValueError(f"bash_path 必须指向文件：{resolved_bash}")
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"bash_path 路径处理失败：{exc}") from exc

    return ExecutionEnvironment(workspace_dir=resolved_workspace, bash_path=str(resolved_bash))
