"""终端多轮会话。"""

import asyncio
import signal
from collections.abc import Callable
from types import FrameType

from rich.console import Console

from code_agent.core.agent import Agent
from code_agent.events import RunResult
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

    loop = asyncio.get_running_loop()
    session_task = asyncio.current_task()
    assert session_task is not None
    reading_input = False
    current_run: asyncio.Task[RunResult] | None = None
    interrupt_requested = False

    def handle_sigint(signum: int, frame: FrameType | None) -> None:
        nonlocal interrupt_requested

        if reading_input:
            raise KeyboardInterrupt

        task = current_run
        if task is None or task.done() or interrupt_requested:
            return

        interrupt_requested = True
        loop.call_soon_threadsafe(task.cancel)

    previous_handler = signal.signal(signal.SIGINT, handle_sigint)

    try:
        while True:
            try:
                reading_input = True
                prompt = reader()
            except KeyboardInterrupt:
                console.print()
                continue
            except EOFError:
                console.print()
                return 0
            finally:
                reading_input = False

            stripped = prompt.strip()

            if not stripped:
                continue

            if stripped == "/exit":
                return 0

            interrupt_requested = False
            current_run = asyncio.create_task(agent.run(prompt, on_event=renderer.render_event))
            try:
                result = await current_run
                # 即使子任务自行吞掉了取消，外部会话取消仍不能变成继续输入。
                if session_task.cancelling():
                    raise asyncio.CancelledError
                renderer.render_result(result)
            except asyncio.CancelledError:
                if session_task.cancelling() or not interrupt_requested:
                    raise
                renderer.render_cancelled()
            except Exception as exc:
                renderer.render_error(f"会话发生意外错误：{type(exc).__name__}: {exc}")
                return 1
            finally:
                current_run = None
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    return 0
