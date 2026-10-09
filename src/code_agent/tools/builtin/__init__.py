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
            "description": "bash工具，返回工具的执行结果",
            "args_model": BashArgs,
            "handler": run_bash,
        }
    }
