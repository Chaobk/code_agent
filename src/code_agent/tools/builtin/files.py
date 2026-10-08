"""内置工具(D):文件相关。

已有 ``read_file``(带行号);Bash 优先路线下保留实现与测试。
``list_dir``、``glob``、``search``、``write_file``、``edit_file`` 按需增加,
不作为 M1/M2 的验收前置条件。
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

MAX_LINES_PER_CALL = 2000  # 单次最多返回行数


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="要读取的文件绝对路径")
    start_line: int = Field(default=1, ge=1, description="起始行号，从 1 开始")
    max_lines: int = Field(
        default=500,
        ge=1,
        le=MAX_LINES_PER_CALL,
        description="最多读取多少行",
    )


def read_file(path: str, start_line: int = 1, max_lines: int = 500) -> str:
    start_line = max(1, start_line)
    if max_lines < 1:
        raise ValueError("max_lines should be > 0")
    max_lines = min(max_lines, MAX_LINES_PER_CALL)

    fp = Path(path).expanduser()

    if not fp.exists():
        raise FileNotFoundError(fp)

    if not fp.is_file():
        raise ValueError(f"{path} 路径下并不是一个可读文件")

    with open(fp, encoding="utf-8") as f:
        lines = f.readlines()

    # 行号从 1 开始，列表下标从 0 开始，因此起始位置减 1。
    selected_lines = lines[start_line - 1 : start_line - 1 + max_lines]
    numbered_lines = []
    for line_number, line in enumerate(selected_lines, start=start_line):
        content = line.rstrip("\r\n")
        numbered_lines.append(f"{line_number}: {content}")
    return "\n".join(numbered_lines)
