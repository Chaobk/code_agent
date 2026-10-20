"""终端多轮会话。"""

import asyncio
import signal
from collections.abc import Callable
from types import FrameType

from rich.console import Console

from code_agent.core.agent import Agent
from code_agent.events import AnyEvent, RunFinished
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
    current_run: asyncio.Task[str] | None = None
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

    def on_event(event: AnyEvent) -> None:
        nonlocal finished_reason

        renderer.render_event(event)
        if isinstance(event, RunFinished):
            finished_reason = event.reason

    async def run_current() -> str:
        nonlocal run_started
        run_started = True
        return await agent.run(prompt, on_event=on_event)

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
            finished_reason = None
            run_started = False
            current_run = asyncio.create_task(run_current())
            try:
                await current_run
            except asyncio.CancelledError:
                if session_task.cancelling() or not interrupt_requested:
                    raise

                if not run_started:
                    console.print("本次输入已取消，运行尚未开始")
                    continue

                if finished_reason == "cancelled":
                    continue

                console.print("运行中断，但未收到正常的取消结束事件，会话结束")
                return 1
            except Exception as exc:
                if not interrupt_requested and finished_reason in ("error", "max_steps"):
                    continue
                console.print(
                    f"会话发生意外错误：{type(exc).__name__}：{exc}",
                    style="red",
                    markup=False,
                )
                return 1
            finally:
                current_run = None
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    return 0
