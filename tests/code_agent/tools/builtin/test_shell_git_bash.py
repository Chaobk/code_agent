"""Windows Git Bash 的真实取消回归，不访问模型服务。"""

import asyncio
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from code_agent.events import ToolStatus
from code_agent.tools.builtin.shell import run_bash


@pytest.mark.skipif(sys.platform != "win32", reason="只验证 Windows Git Bash")
@pytest.mark.parametrize("cancel", [True, False], ids=["cancellation", "timeout"])
async def test_git_bash_stop_closes_pipes_without_waiting_for_sleep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("未安装 Git Bash")
    executable = Path(bash)
    candidates = [executable.parent / "sleep.exe", executable.parent.parent / "usr/bin/sleep.exe"]
    if not any(candidate.is_file() for candidate in candidates):
        pytest.skip("PATH 中的 Bash 不是带 sleep.exe 的 Git Bash")

    processes: list[asyncio.subprocess.Process] = []
    spawn = asyncio.create_subprocess_exec

    async def observe_spawn(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe_spawn)
    # 与用户的 sleep 20 && echo done 同一模式；缩短自然存活上限，便于红测兜底回收。
    task = asyncio.create_task(
        run_bash(
            'sleep 6 && echo "done"',
            timeout_seconds=30 if cancel else 1,
            workspace_dir=tmp_path,
            bash_path=bash,
        )
    )
    try:
        await asyncio.sleep(0.8)
        if task.done():
            pytest.fail(f"取消前命令已意外结束：{task.result()}")
        started = time.monotonic()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), timeout=4)
        else:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=4)
            assert result.status == ToolStatus.TIMED_OUT
            assert result.output["exit_code"] is None
            assert "done" not in result.output["stdout"]
        assert time.monotonic() - started < 3.5, "取消不应等待 sleep 自然结束"
        assert len(processes) == 1
        process = processes[0]
        assert process.returncode is not None
        assert process.stdout is not None and process.stdout.at_eof()
        assert process.stderr is not None and process.stderr.at_eof()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        # 红测也等本次命令的管道关闭，避免在事件循环关闭后触发 transport 析构异常。
        # 不按名称寻找或终止其他 sleep 进程；本次命令自身最多存活 6 秒。
        for process in processes:
            await asyncio.wait_for(process.communicate(), timeout=8)
