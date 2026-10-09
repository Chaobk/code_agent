"""交互层(H):命令行入口。

当前支持两个开关:

- ``--version``;
- ``-p/--prompt``:命令行单次模式,执行 Agent 任务并显示运行历史。

``-p`` 模式用于自动化调试与验收。M4 再接入终端多轮会话;当前无参数运行
仍提示用法并退出。参数入口不承载会话、权限判断或 agent 决策。
"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from code_agent import __version__
from code_agent.config import Settings, load_settings
from code_agent.core.agent import create_agent
from code_agent.ui.render import print_history

app = typer.Typer(add_completion=False, help="code_agent —— 学习型编程 Agent")
console = Console()


def _echo_version(value: bool) -> None:
    """``--version`` 的回调(eager:在其它参数校验之前就执行)。"""
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
    """执行一次 Agent 任务并显示运行历史,返回进程退出码。"""
    agent = create_agent(settings)

    try:
        answer = await agent.run(prompt)
    except Exception as exc:
        print_history(agent.messages, show_system=False, console=console)
        console.print(
            f"调用失败: {type(exc).__name__}: {exc}",
            style="bold red",
            markup=False,
            highlight=False,
        )
        return 1

    print_history(
        agent.messages,
        show_system=False,
        final_answer=answer,
        console=console,
    )
    return 0


def main() -> None:
    """``pyproject.toml`` 中 ``[project.scripts]`` 指向的入口。"""
    app()


if __name__ == "__main__":
    main()
