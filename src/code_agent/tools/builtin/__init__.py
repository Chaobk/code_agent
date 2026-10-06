"""工具系统(D):内置工具实现。"""

from .files import ReadFileArgs, read_file


def create_builtin_registry() -> dict:
    return {
        "read_file": {
            "description": "读取文本文件指定范围，返回带行号的内容",
            "args_model": ReadFileArgs,
            "handler": read_file,
        }
    }
