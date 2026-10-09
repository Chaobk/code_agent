"""工具系统(D):内置工具实现。"""

from .files import ReadFileArgs, read_file
from .shell import BashArgs, run_bash

__all__ = ["BashArgs", "ReadFileArgs", "create_builtin_registry", "read_file", "run_bash"]


def create_builtin_registry() -> dict:
    return {
        # "read_file": {
        #     "description": "读取文本文件指定范围，返回带行号的内容",
        #     "args_model": ReadFileArgs,
        #     "handler": read_file,
        # },
        "bash": {
            "description": (
                "在应用指定的工作目录执行 Bash 命令，返回 stdout、stderr 和退出码。"
                "每次调用是独立进程，cd 和 export 不跨调用保留。"
            ),
            "args_model": BashArgs,
            "handler": run_bash,
        }
    }
