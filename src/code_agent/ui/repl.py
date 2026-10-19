"""终端多轮会话。"""

from collections.abc import Callable

from rich.console import Console

from code_agent.core.agent import Agent
from code_agent.ui.render import EventRenderer


async def run_session(
    agent: Agent,
    *,
    read_input: Callable[[], str] | None = None,
    console: Console | None = None,
) -> int:
    """复用传入的 Agent 接受多次输入，正常退出返回 0。

    read_input 不接收参数，每次返回一行输入，EOF 时抛出 EOFError；
    为 None 时由实现提供终端输入。console 为 None 时创建默认 Console。
    空白输入跳过，去除首尾空白后等于 /exit 时退出，其余输入原样交给
    Agent.run，并用 EventRenderer 显示事件，不重复打印返回的正文。
    """
    console = console if console is not None else Console()
    renderer = EventRenderer(console=console)

    def terminal_input() -> str:
        return input("你 > ")

    reader = read_input if read_input is not None else terminal_input

    while True:
        try:
            prompt = reader()
        except EOFError:
            console.print()
            return 0

        stripped = prompt.strip()

        if not stripped:
            continue

        if stripped == "/exit":
            return 0

        await agent.run(
            prompt,
            on_event=renderer.render_event,
        )

    return 0
