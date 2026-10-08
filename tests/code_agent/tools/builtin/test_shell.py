"""Bash 执行器单测(D/F):使用假进程,不依赖本机 Bash 或真实模型。"""

import asyncio
import sys
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import psutil
import pytest

from code_agent.events import ToolResult, ToolStatus
from code_agent.tools.builtin import shell as shell_module
from code_agent.tools.builtin.shell import run_bash


@pytest.mark.parametrize("exit_code", [0, 1], ids=["zero-exit", "nonzero-exit"])
async def test_run_bash_uses_fixed_workspace_and_returns_process_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    """验证启动参数和统一结果;正常非零退出也保留为有效观察。"""
    stdout = asyncio.StreamReader()
    stdout.feed_data("文件内容\n".encode())
    stdout.feed_eof()
    stderr = asyncio.StreamReader()
    stderr.feed_eof()
    process = SimpleNamespace(
        stdout=stdout,
        stderr=stderr,
        pid=4321,
        returncode=exit_code,
        wait=AsyncMock(return_value=exit_code),
        communicate=AsyncMock(return_value=("文件内容\n".encode(), b"")),
    )
    create_process = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(psutil, "Process", Mock())
    bash_path = "configured-bash"

    result = await run_bash("cat example.txt", workspace_dir=tmp_path, bash_path=bash_path)

    create_process.assert_awaited_once()
    call = create_process.await_args
    assert call.args == (bash_path, "--noprofile", "--norc", "-c", "cat example.txt")
    assert Path(call.kwargs["cwd"]) == tmp_path
    assert call.kwargs["stdin"] == asyncio.subprocess.DEVNULL
    assert call.kwargs["stdout"] == asyncio.subprocess.PIPE
    assert call.kwargs["stderr"] == asyncio.subprocess.PIPE
    assert isinstance(result, ToolResult)
    assert result.status == ToolStatus.COMPLETED
    assert result.truncated is False
    assert isinstance(result.output, dict)
    assert result.output["stdout"] == "文件内容\n"
    assert result.output["stderr"] == ""
    assert result.output["exit_code"] == exit_code


def _tracked_process(pid: int) -> Mock:
    tracked = Mock(spec=psutil.Process)
    tracked.pid = pid
    tracked.children.return_value = []
    tracked.is_running.return_value = False
    return tracked


async def test_stop_process_tree_terminates_descendants_before_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _tracked_process(4321)
    child = _tracked_process(4322)
    grandchild = _tracked_process(4323)
    parent.children.return_value = [child, grandchild]
    calls: list[int] = []
    for tracked in (parent, child, grandchild):
        tracked.terminate.side_effect = lambda pid=tracked.pid: calls.append(pid)
    wait_procs = Mock(side_effect=[([child, grandchild], []), ([], [])])
    monkeypatch.setattr(psutil, "wait_procs", wait_procs)
    process = SimpleNamespace(pid=4321, wait=AsyncMock(return_value=0))

    await shell_module.stop_process_tree(process, parent)

    parent.children.assert_called_once_with(recursive=True)
    assert calls == [4323, 4322, 4321]
    assert all(parent not in call.args[0] for call in wait_procs.call_args_list)
    process.wait.assert_awaited_once()


async def test_stop_process_tree_kills_survivors(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _tracked_process(4321)
    child = _tracked_process(4322)
    parent.children.return_value = [child]
    parent.is_running.return_value = True
    monkeypatch.setattr(psutil, "wait_procs", Mock(side_effect=[([], [child]), ([child], [])]))
    process = SimpleNamespace(pid=4321, wait=AsyncMock(return_value=-1))

    await shell_module.stop_process_tree(process, parent)

    child.kill.assert_called_once()
    parent.kill.assert_called_once()
    process.wait.assert_awaited_once()


async def test_stop_process_tree_tolerates_process_disappearing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _tracked_process(4321)
    parent.terminate.side_effect = psutil.NoSuchProcess(parent.pid)
    monkeypatch.setattr(psutil, "wait_procs", Mock(return_value=([], [])))
    process = SimpleNamespace(pid=4321, wait=AsyncMock(return_value=0))

    await shell_module.stop_process_tree(process, parent)

    process.wait.assert_awaited_once()


async def test_stop_process_tree_reports_permission_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _tracked_process(4321)
    parent.terminate.side_effect = psutil.AccessDenied(parent.pid)
    monkeypatch.setattr(psutil, "wait_procs", Mock(return_value=([], [])))
    process = SimpleNamespace(pid=4321, wait=AsyncMock())

    with pytest.raises(RuntimeError, match="权限不足"):
        await shell_module.stop_process_tree(process, parent)


async def test_stop_process_tree_reports_surviving_descendants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _tracked_process(4321)
    child = _tracked_process(4322)
    parent.children.return_value = [child]
    monkeypatch.setattr(psutil, "wait_procs", Mock(return_value=([], [child])))
    process = SimpleNamespace(pid=4321, wait=AsyncMock())

    with pytest.raises(RuntimeError, match="仍有子进程存活"):
        await shell_module.stop_process_tree(process, parent)


async def test_stop_process_tree_limits_asyncio_reaping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shell_module, "PROCESS_CLEANUP_TIMEOUT_SECONDS", 0.01)

    async def never_finishes() -> None:
        await asyncio.Future()

    process = SimpleNamespace(pid=4321, wait=never_finishes)
    with pytest.raises(RuntimeError, match="回收超时"):
        await shell_module.stop_process_tree(process, None)


async def test_stop_process_tree_rejects_unrelated_process() -> None:
    process = SimpleNamespace(pid=4321, wait=AsyncMock())
    with pytest.raises(ValueError, match="本次启动"):
        await shell_module.stop_process_tree(process, _tracked_process(9876))


async def test_stop_process_tree_cleans_up_real_parent_and_child(tmp_path: Path) -> None:
    child_code = "import time; time.sleep(30)"
    parent_code = (
        "import subprocess, sys, time; "
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "print(child.pid, flush=True); time.sleep(30)"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        parent_code,
        cwd=tmp_path,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    parent = psutil.Process(process.pid)
    child = None
    output_task = None
    try:
        assert process.stdout is not None
        line = await asyncio.wait_for(process.stdout.readline(), timeout=5)
        child = psutil.Process(int(line))
        output_task = asyncio.create_task(process.communicate())

        await shell_module.stop_process_tree(process, parent)
        await asyncio.wait_for(output_task, timeout=3)

        assert process.returncode is not None
        assert not child.is_running()
    finally:
        # 只处理本测试保留身份的两个进程,不按名称或任意 PID 扫描。
        for tracked in (child, parent):
            if tracked is not None:
                with suppress(psutil.NoSuchProcess):
                    tracked.kill()
        if output_task is not None and not output_task.done():
            with suppress(TimeoutError):
                await asyncio.wait_for(output_task, timeout=3)
        await asyncio.wait_for(process.wait(), timeout=3)
