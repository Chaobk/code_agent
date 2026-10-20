"""文件读取工具测试。"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from code_agent.events import ToolResult, ToolStatus
from code_agent.tools.builtin.files import read_file


def test_read_file_returns_selected_lines_with_original_line_numbers(tmp_path: Path) -> None:
    """只返回指定范围，使用原文件行号，并保留内容中的空格。"""
    file_path = tmp_path / "example.txt"
    file_path.write_text("第一行\n第二行\n  第三行  \n第四行\n", encoding="utf-8")

    result = read_file(str(file_path), start_line=2, max_lines=2)

    assert result == "2: 第二行\n3:   第三行  "


@pytest.mark.parametrize("kind", ["missing", "directory", "invalid-utf8", "permission"])
def test_read_file_returns_expected_operation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path = tmp_path / "file.txt"
    if kind == "directory":
        path.mkdir()
    elif kind == "invalid-utf8":
        path.write_bytes(b"\xff")
    elif kind == "permission":
        monkeypatch.setattr(Path, "open", Mock(side_effect=PermissionError("DENIED")))

    result = read_file(str(path))

    assert isinstance(result, ToolResult)
    assert result.status == ToolStatus.ERROR
    assert "读取文件失败" in result.output["error"]
