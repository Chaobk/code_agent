"""文件读取工具测试。"""

from pathlib import Path

from code_agent.tools.builtin.files import read_file


def test_read_file_returns_selected_lines_with_original_line_numbers(tmp_path: Path) -> None:
    """只返回指定范围，使用原文件行号，并保留内容中的空格。"""
    file_path = tmp_path / "example.txt"
    file_path.write_text("第一行\n第二行\n  第三行  \n第四行\n", encoding="utf-8")

    result = read_file(str(file_path), start_line=2, max_lines=2)

    assert result == "2: 第二行\n3:   第三行  "
