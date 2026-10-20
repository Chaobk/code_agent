"""命令行入口，支持版本查询、单次任务和终端会话。"""

from __future__ import annotations

import asyncio
import sys

import typer
from rich.console import Console

from code_agent import __version__
from code_agent.config import Settings, load_settings
from code_agent.core.agent import create_agent
from code_agent.ui import repl
from code_agent.ui.render import EventRenderer

app = typer.Typer(add_completion=False, help="code_agent —— 学习型编程 Agent")
console = Console()


def _echo_version(value: bool) -> None:
    """输出版本号，在其他参数校验前执行。"""
    if value:
        console.print(f"codeagent {__version__}")
        raise typer.Exit()


@app.command()
def ask(
    prompt: str | None = typer.Option(None, "-p", "--prompt", help="一句话任务(命令行单次模式)"),
    version: bool = typer.Option(
        False, "--version", callback=_echo_version, is_eager=True, help="显示版本号后退出"
    ),
) -> None:
    """选择单次任务或终端会话，并显示运行事件。"""
    if prompt is None:
        if not _stdin_is_terminal():
            console.print('请使用codeagent -p "你的任务"')
            raise typer.Exit(code=1)

    elif not prompt.strip():
        console.print("任务内容不能为空")
        raise typer.Exit(code=1)

    settings = load_settings()
    try:
        if prompt is None:
            code = asyncio.run(_run_interactive(settings))
        else:
            code = asyncio.run(_run_once(prompt, settings))
    except KeyboardInterrupt:
        console.print("运行已取消")
        code = 130
    raise typer.Exit(code=code)


async def _run_once(prompt: str, settings: Settings) -> int:
    """执行一次 Agent 任务并显示运行事件,返回进程退出码。"""
    renderer = EventRenderer(console=console)
    try:
        agent = create_agent(settings)
    except ValueError as exc:
        renderer.render_error(f"执行环境配置错误：{exc}")
        return 1

    try:
        result = await agent.run(prompt, on_event=renderer.render_event)
        renderer.render_result(result)
    except Exception as exc:
        renderer.render_error(f"{type(exc).__name__}: {exc}")
        return 1

    return 0 if result.status == "completed" else 1


def _stdin_is_terminal() -> bool:
    """检查输入来源是否为终端，供命令行入口选择运行方式。"""
    return sys.stdin.isatty()


async def _run_interactive(settings: Settings) -> int:
    """创建一次 Agent 并交给会话入口；环境校验失败时显示原因并返回 1。"""
    renderer = EventRenderer(console=console)

    try:
        agent = create_agent(settings)
    except ValueError as exc:
        renderer.render_error(f"执行环境配置错误：{exc}")
        return 1

    return await repl.run_session(agent, console=console)


def main() -> None:
    """项目脚本入口。"""
    app()


if __name__ == "__main__":
    main()
