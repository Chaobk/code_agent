"""异步 Bash 执行、输出收集与进程清理。"""

import asyncio
import codecs
import os
import signal
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Mapping, Any

import psutil
from pydantic import BaseModel, ConfigDict, Field, field_validator

from code_agent.events import ToolResult, ToolStatus
from code_agent.safety.permission import check_command, Decision

MAX_OUTPUT_CHARS = 20_000
OUTPUT_READ_CHUNK_BYTES = 4096


class BashArgs(BaseModel):
    """一次 Bash 调用的模型参数;执行目录、Bash 路径和输出预算由应用绑定。"""

    model_config = ConfigDict(extra="forbid")

    command: str = Field(
        min_length=1,
        description=(
            "在固定工作目录中执行的 Bash 命令，可包含多行；不能只包含空白。"
            "每次调用启动独立进程，cd 和 export 的状态不会跨调用保留。"
        ),
    )
    timeout_seconds: int = Field(
        default=30,
        strict=True,
        ge=1,
        le=120,
        description="本次命令的执行超时秒数，必须为 1–120 的整数，默认 30 秒",
    )

    @field_validator("command")
    @classmethod
    def reject_blank_command(cls, value: str) -> str:
        """拒绝空白命令,保留原始命令中的缩进与换行。"""
        if not value.strip():
            raise ValueError("command 不能为空白")
        return value


async def run_bash(
    command: str,
    timeout_seconds: int = 30,
    *,
    workspace_dir: Path,
    bash_path: str | None = None,
) -> ToolResult:
    """在指定目录启动独立 Bash 进程，返回输出、退出码与执行状态。

    非零退出保留为 COMPLETED；超时返回 TIMED_OUT 和已收集的输出。
    超时或取消时清理进程，取消完成清理后继续传播。
    """

    # 首先对命令的安全性进行检查
    decision = check_command(command)
    if decision is Decision.DENY:
        return ToolResult(error="command_denied", reason="命中硬拒绝规则")
    if decision in Decision.ASK:
        if not prompt_user(command):
            return ToolResult(error="user_denied", reason="用户拒绝")

    bash_path = "bash" if bash_path is None else bash_path
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    try:
        process = await asyncio.create_subprocess_exec(
            bash_path,
            "--noprofile",
            "--norc",
            "-c",
            command,
            cwd=workspace_dir,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )
    except OSError as e:
        return ToolResult(
            output={"stdout": "", "stderr": f"{type(e).__name__}: {e}", "exit_code": None},
            status=ToolStatus.ERROR,
        )
    tracked_process = None
    output_task = None
    try:
        # 从进程启动后即拥有资源；身份查询失败也必须回收直接子进程。
        output_task = asyncio.create_task(collect_output(process))
        with suppress(psutil.NoSuchProcess):
            tracked_process = psutil.Process(process.pid)
        await asyncio.wait_for(
            asyncio.shield(output_task),
            timeout=timeout_seconds,
        )
        status = ToolStatus.COMPLETED
        exit_code = process.returncode
    except TimeoutError:
        status = ToolStatus.TIMED_OUT
        exit_code = None
    finally:
        output = await _cleanup_execution(
            process, tracked_process, output_task, signal_process_group=bool(creationflags)
        )

    assert output is not None
    stdout, stderr, truncated = output

    return ToolResult(
        output={
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
        },
        status=status,
        truncated=truncated,
    )


async def collect_output(process: asyncio.subprocess.Process) -> tuple[str, str, bool]:
    """并发读取 stdout/stderr，共用字符保留预算，超额后继续排空。

    按读取顺序分配预算，使用增量 UTF-8 解码处理跨块字符。
    """
    remaining = MAX_OUTPUT_CHARS
    truncated = False

    def retain(text: str) -> str:
        nonlocal remaining, truncated
        kept = text[:remaining]
        remaining -= len(kept)
        if len(kept) < len(text):
            truncated = True
        return kept

    async def read_stream(stream: asyncio.StreamReader) -> str:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        parts: list[str] = []
        while chunk := await stream.read(OUTPUT_READ_CHUNK_BYTES):
            kept = retain(decoder.decode(chunk))
            if kept:
                parts.append(kept)
        kept = retain(decoder.decode(b"", final=True))
        if kept:
            parts.append(kept)
        return "".join(parts)

    assert process.stdout is not None
    assert process.stderr is not None
    # 一条管道读取失败时,TaskGroup 会取消并等待另一条读取任务。
    async with asyncio.TaskGroup() as group:
        stdout_task = group.create_task(read_stream(process.stdout))
        stderr_task = group.create_task(read_stream(process.stderr))
    await process.wait()
    return stdout_task.result(), stderr_task.result(), truncated


PROCESS_CLEANUP_TIMEOUT_SECONDS = 2.0


async def _cleanup_execution(
    process: asyncio.subprocess.Process,
    tracked_process: psutil.Process | None,
    output_task: asyncio.Task[tuple[str, str, bool]] | None,
    *,
    signal_process_group: bool = False,
) -> tuple[str, str, bool] | None:
    """回收进程及读取任务，首个收尾故障向外传播，其他故障附在诊断中。"""
    error: Exception | None = None
    output = None
    if signal_process_group and process.returncode is None:
        try:
            # 仅向本次用 CREATE_NEW_PROCESS_GROUP 创建的组发送，不广播到整个控制台。
            process.send_signal(signal.CTRL_BREAK_EVENT)
        except ProcessLookupError:
            pass
        except OSError as exc:
            error = exc
        else:
            # 给 MSYS 将控制事件传给子进程的机会，再执行原生快照清理。
            await asyncio.sleep(0.1)
    try:
        if process.returncode is None or (output_task is not None and not output_task.done()):
            await stop_process_tree(process, tracked_process)
    except Exception as exc:
        if error is None:
            error = exc
        else:
            error.add_note(f"进程回收也失败：{type(exc).__name__}: {exc}")

    if output_task is not None:
        try:
            output = await asyncio.wait_for(
                asyncio.shield(output_task), timeout=PROCESS_CLEANUP_TIMEOUT_SECONDS
            )
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                exc = RuntimeError("等待 Bash 输出读取收尾超时")
            if error is None:
                error = exc
            else:
                error.add_note(f"输出回收也失败：{type(exc).__name__}: {exc}")
        finally:
            if not output_task.done():
                output_task.cancel()
                await asyncio.gather(output_task, return_exceptions=True)

    if error is not None:
        raise error
    return output


async def stop_process_tree(
    process: asyncio.subprocess.Process,
    tracked_process: psutil.Process | None,
) -> None:
    """终止已发现的后代进程，并通过 asyncio 回收直接子进程。

    tracked_process 必须是在启动时保存的进程对象，以免 PID 复用后清理错误目标。
    同步查询在线程中执行，各等待阶段都有超时；调用方须持续排空输出管道。
    后代查询基于快照，可能遗漏查询后新建或已脱离父进程的后代。
    """
    if tracked_process is not None:
        if tracked_process.pid != process.pid or tracked_process.pid == os.getpid():
            raise ValueError("清理目标必须是本次启动的子进程")
        await asyncio.to_thread(_stop_tracked_process_tree, tracked_process)
    elif process.returncode is None:
        # 只使用本次启动的子进程句柄，不按名称或未经核实的 PID 清理。
        with suppress(ProcessLookupError):
            process.kill()

    try:
        await asyncio.wait_for(process.wait(), timeout=PROCESS_CLEANUP_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise RuntimeError("等待 Bash 进程回收超时") from exc


def _stop_tracked_process_tree(parent: psutil.Process) -> None:
    """使用 psutil 清理快照中的后代，保留根进程给 asyncio 回收。"""
    try:
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        return

    targets = [*reversed(children), parent]
    denied: list[psutil.AccessDenied] = []
    for target in targets:
        try:
            target.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as exc:
            denied.append(exc)

    # 根进程由 asyncio 等待,避免 POSIX 上两个等待者竞争 waitpid。
    _, alive = psutil.wait_procs(children, timeout=PROCESS_CLEANUP_TIMEOUT_SECONDS)
    remaining = [*alive, parent] if parent.is_running() else alive
    for target in remaining:
        try:
            target.kill()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as exc:
            denied.append(exc)

    _, alive = psutil.wait_procs(alive, timeout=PROCESS_CLEANUP_TIMEOUT_SECONDS)
    if alive:
        raise RuntimeError(f"清理后仍有子进程存活: {[target.pid for target in alive]}")
    if denied:
        raise RuntimeError("清理进程树时权限不足") from denied[0]


class BashApprovalPolicy:
    """bash 的审批策略。"""

    def evaluate(self, args: Mapping[str, Any]) -> Decision:
        return check_command(str(args.get("command", "")))

    def scope_key(self, args: Mapping[str, Any]) -> str:
        parts = str(args.get("command", "")).strip().split()
        # 取前两个 token：批准 "git push" 不放行 "git status"
        return " ".join(parts[:2]) if parts else ""

    def describe(self, args: Mapping[str, Any]) -> str:
        return f"bash 命令：{args.get('command', '')}"


