"""命令行入口，支持版本查询和单次任务执行。"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from code_agent import __version__
from code_agent.config import Settings, load_settings
from code_agent.core.agent import create_agent
from code_agent.events import ErrorEvent
from code_agent.ui.render import render_event

app = typer.Typer(add_completion=False, help="code_agent —— 学习型编程 Agent")
console = Console()


def _echo_version(value: bool) -> None:
    """输出版本号，在其他参数校验前执行。"""
    if value:
        console.print(f"code-agent {__version__}")
        raise typer.Exit()


@app.command()
def ask(
    prompt: str | None = typer.Option(None, "-p", "--prompt", help="一句话任务(命令行单次模式)"),
    version: bool = typer.Option(
        False, "--version", callback=_echo_version, is_eager=True, help="显示版本号后退出"
    ),
) -> None:
    """执行一次 Agent 任务并显示命令、工具结果与最终答案。"""
    if not prompt:
        console.print('[yellow]用法:[/yellow] code-agent -p "你的任务"')
        raise typer.Exit(code=1)
    settings = load_settings()
    raise typer.Exit(code=asyncio.run(_run_once(prompt, settings)))


async def _run_once(prompt: str, settings: Settings) -> int:
    """执行一次 Agent 任务并显示运行事件,返回进程退出码。"""
    try:
        agent = create_agent(settings)
    except ValueError as exc:
        render_event(
            ErrorEvent(message=f"执行环境配置错误：{exc}"),
            console=console,
        )
        return 1

    try:
        await agent.run(prompt, on_event=lambda event: render_event(event, console=console))
    except Exception:
        return 1

    return 0


def main() -> None:
    """项目脚本入口。"""
    app()


if __name__ == "__main__":
    main()
