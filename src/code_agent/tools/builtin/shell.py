"""异步 Bash 执行、输出收集与进程清理。"""

import asyncio
import codecs
import os
from pathlib import Path

import psutil
from pydantic import BaseModel, ConfigDict, Field, field_validator

from code_agent.events import ToolResult, ToolStatus

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
    bash_path = "bash" if bash_path is None else bash_path
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
        )
    except OSError as e:
        return ToolResult(
            output={"stdout": "", "stderr": f"{type(e).__name__}: {e}", "exit_code": None},
            status=ToolStatus.ERROR,
        )
    # 启动时记录 PID 与创建时间,清理时复用同一个 psutil.Process。
    try:
        tracked_process = psutil.Process(process.pid)
    except psutil.NoSuchProcess:
        tracked_process = None

    output_task = asyncio.create_task(collect_output(process))
    try:
        stdout, stderr, truncated = await asyncio.wait_for(
            asyncio.shield(output_task),
            timeout=timeout_seconds,
        )
        status = ToolStatus.COMPLETED
        exit_code = process.returncode

    except TimeoutError:
        await stop_process_tree(process, tracked_process)
        stdout, stderr, truncated = await output_task
        status = ToolStatus.TIMED_OUT
        exit_code = None

    except asyncio.CancelledError:
        await stop_process_tree(process, tracked_process)
        await output_task
        raise

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

    try:
        await asyncio.wait_for(process.wait(), timeout=PROCESS_CLEANUP_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise RuntimeError("等待 Bash 进程回收超时") from exc


def _stop_tracked_process_tree(parent: psutil.Process) -> None:
    """使用 psutil 清理快照中的后代,保留根进程给 asyncio 回收。"""
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


if __name__ == "__main__":
    res = asyncio.run(
        run_bash(
            "ls", workspace_dir=r"C:\Users\1004945427\Desktop\pros\code_agent", bash_path="bash"
        )
    )
    print(res)
