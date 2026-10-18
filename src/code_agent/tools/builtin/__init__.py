"""内置工具及其执行环境绑定。"""

from functools import partial
from pathlib import Path

from .files import ReadFileArgs, read_file
from .shell import BashArgs, run_bash

__all__ = ["BashArgs", "ReadFileArgs", "create_builtin_registry", "read_file", "run_bash"]

from ..registry import ToolRegistry
from ..spec import ToolSpec


def create_builtin_registry(*, workspace_dir: Path, bash_path: str = "bash") -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="bash",
            description="在指定工作目录执行 Bash 命令",
            args_model=BashArgs,
            handler=partial(
                run_bash,
                workspace_dir=workspace_dir,
                bash_path=bash_path,
            ),
        )
    )
    return registry
