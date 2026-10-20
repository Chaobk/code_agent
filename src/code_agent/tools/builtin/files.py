"""按行读取文件内容。"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from code_agent.events import ToolResult, ToolStatus

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


def read_file(path: str, start_line: int = 1, max_lines: int = 500) -> str | ToolResult:
    start_line = max(1, start_line)
    if max_lines < 1:
        raise ValueError("max_lines should be > 0")
    max_lines = min(max_lines, MAX_LINES_PER_CALL)

    fp = Path(path).expanduser()

    try:
        with fp.open(encoding="utf-8") as f:
            lines = f.readlines()
    except (OSError, UnicodeError) as exc:
        return ToolResult(
            output={"error": f"读取文件失败：{type(exc).__name__}: {exc}"},
            status=ToolStatus.ERROR,
        )

    selected_lines = lines[start_line - 1 : start_line - 1 + max_lines]
    numbered_lines = []
    for line_number, line in enumerate(selected_lines, start=start_line):
        content = line.rstrip("\r\n")
        numbered_lines.append(f"{line_number}: {content}")
    return "\n".join(numbered_lines)
