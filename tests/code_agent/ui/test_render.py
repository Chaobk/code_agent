"""消息历史显示测试:不调用模型或执行工具。"""

import copy
from io import StringIO

import pytest
from rich.console import Console

from code_agent.events import (
    AnyEvent,
    ErrorEvent,
    RunFinished,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolResult,
    ToolResultEvent,
    ToolStatus,
)
from code_agent.ui.render import EventRenderer, print_history


@pytest.mark.parametrize("reason", ["error", "max_steps"])
def test_render_failure_separates_partial_text_and_shows_error_once(reason: str) -> None:
    output = StringIO()
    console = Console(file=output, width=100, color_system=None)
    renderer = EventRenderer(console=console)
    message = "RuntimeError: 失败[bold]\n下一行"

    renderer.render_event(TextDelta(text="部分正文"))
    renderer.render_event(ErrorEvent(message=message))
    renderer.render_event(RunFinished(reason=reason))

    text = output.getvalue()
    assert text == f"助手： 部分正文\n{message}\n"
    assert text.count(message) == 1
    assert "运行已取消" not in text


def test_render_cancelled_separates_partial_text_and_shows_cancellation_once() -> None:
    output = StringIO()
    console = Console(file=output, width=100, color_system=None)
    renderer = EventRenderer(console=console)

    renderer.render_event(TextDelta(text="部分正文[bold]"))
    renderer.render_event(RunFinished(reason="cancelled"))

    assert output.getvalue() == "助手： 部分正文[bold]\n运行已取消\n"


@pytest.mark.parametrize("answer", ["最终答案[bold]", ""], ids=["text", "empty"])
def test_render_completed_only_terminates_final_text_line(answer: str) -> None:
    output = StringIO()
    console = Console(file=output, width=100, color_system=None)
    renderer = EventRenderer(console=console)

    renderer.render_event(TextDelta(text=answer))
    renderer.render_event(RunFinished(reason="completed"))

    assert output.getvalue() == (f"助手： {answer}\n" if answer else "")


def test_render_text_preserves_whitespace_and_ignores_empty_chunks() -> None:
    output = StringIO()
    renderer = EventRenderer(console=Console(file=output, width=100, color_system=None))

    renderer.render_event(TextDelta(text=""))
    assert output.getvalue() == ""

    renderer.render_event(TextDelta(text="  第一行\n"))
    renderer.render_event(TextDelta(text=""))
    renderer.render_event(TextDelta(text="[bold]第二行  "))

    assert output.getvalue() == "助手：   第一行\n[bold]第二行  "


@pytest.mark.parametrize(
    "boundary",
    [
        ToolCallEvent(tool_call=ToolCall(id="call_1", name="bash", arguments="{}")),
        ToolResultEvent(
            tool_call_id="call_1",
            tool_result=ToolResult(output="观察", status=ToolStatus.COMPLETED),
        ),
        ErrorEvent(message="失败"),
        RunFinished(reason="completed"),
        RunFinished(reason="cancelled"),
        RunFinished(reason="error"),
        RunFinished(reason="max_steps"),
    ],
    ids=["call", "result", "error", "completed", "cancelled", "failed", "max-steps"],
)
def test_render_boundary_closes_text_and_next_text_gets_new_label(boundary: AnyEvent) -> None:
    output = StringIO()
    renderer = EventRenderer(console=Console(file=output, width=100, color_system=None))

    renderer.render_event(TextDelta(text="第一段"))
    renderer.render_event(boundary)
    renderer.render_event(TextDelta(text="第二段"))
    renderer.render_event(TextDelta(text="后续"))
    renderer.render_event(RunFinished(reason="completed"))

    text = output.getvalue()
    assert text.startswith("助手： 第一段\n")
    assert text.endswith("助手： 第二段后续\n")
    assert text.count("助手： ") == 2


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


@pytest.mark.parametrize("status", list(ToolStatus))
@pytest.mark.parametrize("truncated", [False, True], ids=["complete-output", "truncated-output"])
def test_render_tool_result_shows_identity_status_and_literal_output(
    status: ToolStatus, truncated: bool
) -> None:
    event = ToolResultEvent(
        tool_call_id="call_result[bold]",
        tool_result=ToolResult(
            output={
                "stdout": "第一行\n[bold]第二行[/bold]",
                "stderr": "错误[red]",
                "exit_code": 1 if status == ToolStatus.COMPLETED else None,
                "details": {"error": "原因[italic]"},
            },
            status=status,
            truncated=truncated,
        ),
    )
    original = event.model_dump()
    output = StringIO()

    renderer = EventRenderer(console=Console(file=output, width=100, color_system=None))
    renderer.render_event(event)

    text = output.getvalue()
    assert text.count("call_result[bold]") == 1
    assert status.value in text
    assert f"截断： {'是' if truncated else '否'}" in text
    assert "stdout:\n第一行\n[bold]第二行[/bold]" in text
    assert "stderr:\n错误[red]" in text
    assert f"exit_code:\n{'1' if status == ToolStatus.COMPLETED else 'null'}" in text
    assert "details:\nerror:\n原因[italic]" in text
    assert event.model_dump() == original


def test_render_events_keep_text_call_result_and_answer_in_order() -> None:
    output = StringIO()
    console = Console(file=output, width=100, color_system=None)
    renderer = EventRenderer(console=console)
    renderer.render_event(TextDelta(text="准备"))
    assert output.getvalue() == "助手： 准备"
    renderer.render_event(TextDelta(text="执行[bold]"))
    assert output.getvalue() == "助手： 准备执行[bold]"
    renderer.render_event(
        ToolCallEvent(tool_call=ToolCall(id="call_1", name="bash", arguments='{"command":"pwd"}')),
    )
    renderer.render_event(
        ToolResultEvent(
            tool_call_id="call_1",
            tool_result=ToolResult(output="观察[red]", status=ToolStatus.COMPLETED),
        ),
    )
    renderer.render_event(TextDelta(text="最终答案"))

    text = output.getvalue()
    assert "准备执行[bold]\n工具调用" in text
    assert "bash" in text
    assert '{"command":"pwd"}' in text
    assert text.count("call_1") == 2
    assert text.count("观察[red]") == 1
    assert text.count("最终答案") == 1
    assert text.count("助手： ") == 2
    assert text.index("pwd") < text.index("观察[red]") < text.index("最终答案")
    assert text.endswith("助手： 最终答案")


@pytest.mark.parametrize("value", ["", "错误[bold]\n下一行", [1, None, True]])
def test_render_tool_result_accepts_non_mapping_output(value: str | list[object]) -> None:
    output = StringIO()
    event = ToolResultEvent(
        tool_call_id="plain_result",
        tool_result=ToolResult(output=value, status=ToolStatus.ERROR),
    )

    renderer = EventRenderer(console=Console(file=output, width=100, color_system=None))
    renderer.render_event(event)

    text = output.getvalue()
    assert "plain_result" in text
    assert "error" in text
    if isinstance(value, str):
        assert (value or "（空）") in text
    else:
        assert "1" in text
        assert "null" in text
        assert "true" in text


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
    assert "01 · 用户" in text
    assert "你好" in text
    assert "暂无可显示的历史消息。" in text


@pytest.mark.parametrize("final_answer", ["已完成[bold]", ""])
def test_print_history_displays_final_summary_and_preserves_original_messages(
    final_answer: str,
) -> None:
    messages = [
        {"role": "user", "content": "用户输入\nEND_TASK"},
        {"role": "tool", "tool_call_id": "call_1", "content": "工具输出\nEND_TASK"},
        {"role": "assistant", "content": "原始总结\nEND_TASK"},
    ]
    original = copy.deepcopy(messages)
    output = StringIO()

    print_history(
        messages,
        final_answer=final_answer,
        console=Console(file=output, width=100, color_system=None),
    )

    text = output.getvalue()
    assert "原始总结" not in text
    assert "用户输入\nEND_TASK" in text
    assert "工具输出\nEND_TASK" in text
    assert text.count("END_TASK") == 2
    if final_answer:
        assert text.count(final_answer) == 1
    assert messages == original
