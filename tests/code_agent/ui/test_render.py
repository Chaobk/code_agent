"""消息历史显示测试:不调用模型或执行工具。"""

import copy
from io import StringIO

from rich.console import Console

from code_agent.events import ToolResult, ToolStatus
from code_agent.ui.render import print_history


def test_print_history_shows_tool_pair_and_preserves_literal_output() -> None:
    messages = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "读取文件"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "bash", "arguments": '{"command": "cat 示例.md"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": ToolResult(
                output={"stdout": "第一行\n[bold]第二行[/bold]", "stderr": "", "exit_code": 1},
                status=ToolStatus.COMPLETED,
                truncated=True,
            ).model_dump_json(),
        },
        {"role": "assistant", "content": "读取完成"},
    ]
    original = copy.deepcopy(messages)
    output = StringIO()

    print_history(messages, console=Console(file=output, width=100, color_system=None))

    text = output.getvalue()
    assert "工具调用: bash" in text
    assert "工具结果 · bash" in text
    assert text.count("call_1") == 2
    assert "cat 示例.md" in text
    assert "状态: completed  |  截断: 是" in text
    assert "第一行\n[bold]第二行[/bold]" in text
    assert "stderr:\n（空）" in text
    assert "exit_code:\n1" in text
    assert text.index("读取文件") < text.index("工具调用") < text.index("读取完成")
    assert messages == original


def test_print_history_handles_plain_error_and_invalid_tool_arguments() -> None:
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "call_1", "function": {"name": "bash", "arguments": '{"command":'}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "工具参数错误：[bold]"},
    ]
    output = StringIO()

    print_history(messages, console=Console(file=output, width=100, color_system=None))

    text = output.getvalue()
    assert '{"command":' in text
    assert "工具参数错误：[bold]" in text


def test_print_history_can_hide_system_and_handles_empty_history() -> None:
    output = StringIO()
    console = Console(file=output, width=100, color_system=None)

    print_history(
        [{"role": "system", "content": "系统提示"}, {"role": "user", "content": "你好"}],
        show_system=False,
        console=console,
    )
    print_history([], console=console)

    text = output.getvalue()
    assert "系统提示" not in text
    assert "02 · 用户" in text
    assert "你好" in text
    assert "暂无可显示的历史消息。" in text
