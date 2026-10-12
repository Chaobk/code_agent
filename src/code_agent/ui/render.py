"""终端事件输出(H)。

M1 输出工具命令、结果与最终答案;M2 消费 events 定义的流式文本、工具调用、
工具结果与错误。
仅负责呈现,不调用模型、不执行工具、不参与 agent 决策。
提供消息历史查看函数,支持以核心返回的总结替换最后一条助手消息的显示。
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError
from rich.console import Console
from rich.syntax import Syntax
from rich.text import Text

from code_agent.events import ToolResult

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
    """按原始消息顺序显示正文、工具调用和结果,不修改历史。

    show_system=False 可隐藏系统提示词;console 可传入自定义输出目标。
    final_answer 非 None 时仅替换末条无工具调用助手消息的显示正文,不修改历史。
    核心负责结束判断并返回最终回复,渲染层不判断任务是否结束。
    正文按原始文本呈现,避免把代码中的方括号当作 Rich 标记。
    """
    console = console if console is not None else Console()
    tool_names: dict[str, str] = {}
    displayed = False
    for index, message in enumerate(messages, start=1):
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
            and index == len(messages)
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
