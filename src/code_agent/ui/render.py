"""运行事件与消息历史的终端显示。"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError
from rich.console import Console
from rich.syntax import Syntax
from rich.text import Text

from code_agent.events import (
    AnyEvent,
    ErrorEvent,
    RunFinished,
    TextDelta,
    ToolCallEvent,
    ToolResult,
    ToolResultEvent,
)

ROLE_LABELS = {
    "system": "系统",
    "user": "用户",
    "assistant": "助手",
    "tool": "工具结果",
}


def print_history(
    messages: Sequence[Mapping[str, Any]],
    *,
    show_system: bool = True,
    final_answer: str | None = None,
    console: Console | None = None,
) -> None:
    """按消息顺序显示正文、工具调用和结果。

    show_system 控制系统消息是否显示。final_answer 可替换末条无工具调用的
    助手消息正文，仅影响显示。正文按原样输出，不解析 Rich 标记。
    """
    console = console if console is not None else Console()
    tool_names: dict[str, str] = {}
    displayed = False
    for index, message in enumerate(messages, start=0):
        role = message.get("role", "unknown")
        if role == "system" and not show_system:
            continue
        displayed = True
        title = f"{index:02d} · {ROLE_LABELS.get(role, role)}"
        call_id = message.get("tool_call_id")
        if role == "tool" and call_id:
            title += f" · {tool_names.get(call_id, call_id)}"
        console.rule(Text(title))

        content = message.get("content")
        if (
            final_answer is not None
            and index == len(messages) - 1
            and role == "assistant"
            and not message.get("tool_calls")
        ):
            content = final_answer
        if role == "tool":
            if call_id:
                console.print(Text(f"调用 ID: {call_id}", style="dim"))
            _print_tool_result(content or "", console)
        elif content:
            console.print(Text(content))

        for call in message.get("tool_calls") or []:
            function = call["function"]
            name = function["name"]
            tool_names[call["id"]] = name
            console.print(Text(f"工具调用: {name}  |  ID: {call['id']}", style="bold cyan"))
            arguments = function.get("arguments") or "{}"
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                console.print(Text(arguments))
            else:
                _print_value(parsed, console)
        console.print()
    if not displayed:
        console.print(Text("暂无可显示的历史消息。"))


def _print_tool_result(content: str, console: Console) -> None:
    """将统一工具结果展开,旧的文本结果或错误消息直接显示。"""
    try:
        result = ToolResult.model_validate_json(content)
    except ValidationError:
        console.print(Text(content))
        return
    console.print(
        Text(f"状态: {result.status.value}  |  截断: {'是' if result.truncated else '否'}")
    )
    _print_value(result.output, console)


def _print_value(value: Any, console: Console) -> None:
    """字典字段分段展示,字符串保留实际换行,其余值格式化为 JSON。"""
    if isinstance(value, dict):
        for key, item in value.items():
            console.print(Text(f"{key}:", style="bold"))
            _print_value(item, console)
    elif isinstance(value, str):
        console.print(Text(value if value else "（空）"))
    else:
        console.print(Syntax(json.dumps(value, ensure_ascii=False, indent=2), "json"))


def render_event(event: AnyEvent, *, console: Console | None = None) -> None:
    """将运行事件输出到终端。"""
    console = console if console is not None else Console()

    if isinstance(event, TextDelta):
        console.print(
            Text(event.text),
            end="",
            soft_wrap=True,
        )
    elif isinstance(event, ToolCallEvent):
        console.print()
        call = event.tool_call
        console.print(Text(f"工具调用：{call.name} | ID: {call.id}"))
        console.print(Text(call.arguments))
    elif isinstance(event, ToolResultEvent):
        console.print()
        res = event.tool_result
        console.print(Text(f"工具执行结果 | Id: {event.tool_call_id}"))
        console.print(Text(f"状态： {res.status.value} | 截断： {'是' if res.truncated else '否'}"))
        _print_value(res.output, console)
    elif isinstance(event, RunFinished):
        console.print()
        if event.reason == "cancelled":
            console.print(Text("运行已取消", style="yellow"))
    elif isinstance(event, ErrorEvent):
        console.print()
        console.print(Text(event.message), style="red")

    pass
