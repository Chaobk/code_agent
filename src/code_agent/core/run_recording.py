"""记录 Agent 的运行边界。"""

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TYPE_CHECKING, Concatenate, ParamSpec

from code_agent.events import RunResult
from code_agent.sessions.manager import SessionManager

if TYPE_CHECKING:
    from code_agent.core.agent import Agent

P = ParamSpec("P")


def record_run(
    run: Callable[Concatenate["Agent", P], Awaitable[RunResult]],
) -> Callable[Concatenate["Agent", P], Awaitable[RunResult]]:
    """围绕异步运行保存开始及终态，异常和取消继续传播。"""

    @wraps(run)
    async def wrapper(agent: "Agent", *args: P.args, **kwargs: P.kwargs) -> RunResult:
        manager = agent.session_manager
        run_id = manager.start_run()
        try:
            result = await run(agent, *args, **kwargs)
        except asyncio.CancelledError as exc:
            _record_exception_end(manager, run_id, "cancelled", exc)
            raise
        except Exception as exc:
            _record_exception_end(manager, run_id, "exception", exc)
            raise

        manager.end_run(run_id, status=result.status, error=result.error)
        return result

    return wrapper


def _record_exception_end(
    manager: SessionManager, run_id: str, status: str, original_error: BaseException
) -> None:
    if manager.storage_error is not None:
        if manager.storage_error is not original_error:
            original_error.add_note(f"会话保存失败：{type(manager.storage_error).__name__}")
        return
    try:
        manager.end_run(run_id, status=status, error=type(original_error).__name__)
    except Exception as storage_error:
        original_error.add_note(f"保存运行终态失败：{type(storage_error).__name__}")
