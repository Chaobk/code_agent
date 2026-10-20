"""Bash 执行器测试，使用模拟进程。"""

import asyncio
import sys
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import psutil
import pytest
from pydantic import ValidationError

from code_agent.events import ToolResult, ToolStatus
from code_agent.tools.builtin import shell as shell_module
from code_agent.tools.builtin.shell import BashArgs, run_bash


def test_bash_args_preserves_multiline_command_and_defaults_timeout() -> None:
    command = "  cat <<'EOF'\n    文件内容\nEOF\n"

    args = BashArgs.model_validate_json(BashArgs(command=command).model_dump_json())

    assert args.model_dump() == {
        "command": command,
        "timeout_seconds": 30,
    }


@pytest.mark.parametrize("command", ["", " ", "\t\r\n"])
def test_bash_args_rejects_blank_command(command: str) -> None:
    with pytest.raises(ValidationError):
        BashArgs(command=command)


@pytest.mark.parametrize("timeout_seconds", [1, 120])
def test_bash_args_accepts_timeout_boundaries(timeout_seconds: int) -> None:
    args = BashArgs(command="pwd", timeout_seconds=timeout_seconds)

    assert args.timeout_seconds == timeout_seconds


@pytest.mark.parametrize("timeout_seconds", [0, 121, True, 1.0, 1.5, "30", None])
def test_bash_args_rejects_invalid_timeout(timeout_seconds: object) -> None:
    with pytest.raises(ValidationError):
        BashArgs.model_validate({"command": "pwd", "timeout_seconds": timeout_seconds})


@pytest.mark.parametrize(
    "extra", [{"workspace_dir": "/tmp"}, {"bash_path": "other-bash"}, {"max_output_chars": 100}]
)
def test_bash_args_rejects_execution_settings(extra: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        BashArgs.model_validate({"command": "pwd", **extra})


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


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError(2, "找不到 Bash", "missing-bash"),
        FileNotFoundError(2, "找不到执行目录", "missing-workspace"),
        PermissionError(13, "没有执行权限", "denied-bash"),
        OSError(8, "可执行文件格式无效", "invalid-bash"),
    ],
    ids=["missing-bash", "missing-workspace", "permission-denied", "invalid-executable"],
)
async def test_run_bash_returns_error_when_process_cannot_start(
    error: OSError, monkeypatch: pytest.MonkeyPatch
) -> None:
    """启动失败应返回可序列化的错误观察,不进入输出读取或进程清理。"""
    create_process = AsyncMock(side_effect=error)
    track_process = Mock()
    collect_output = AsyncMock()
    cleanup = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(psutil, "Process", track_process)
    monkeypatch.setattr(shell_module, "collect_output", collect_output)
    monkeypatch.setattr(shell_module, "stop_process_tree", cleanup)

    result = await run_bash("pwd", workspace_dir=Path.cwd(), bash_path="configured-bash")

    assert result.status == ToolStatus.ERROR
    assert result.truncated is False
    assert result.output == {
        "stdout": "",
        "stderr": f"{type(error).__name__}: {error}",
        "exit_code": None,
    }
    assert ToolResult.model_validate_json(result.model_dump_json()) == result
    create_process.assert_awaited_once()
    track_process.assert_not_called()
    collect_output.assert_not_called()
    cleanup.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [asyncio.CancelledError(), RuntimeError("启动代码内部错误")],
    ids=["cancellation", "programming-error"],
)
async def test_run_bash_propagates_non_os_startup_errors(
    error: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取消和内部错误继续传播,不转换为普通工具错误。"""
    create_process = AsyncMock(side_effect=error)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(type(error)) as caught:
        await run_bash("pwd", workspace_dir=Path.cwd(), bash_path="configured-bash")

    assert caught.value is error
    create_process.assert_awaited_once()


def _output_process(stdout_data: bytes = b"", stderr_data: bytes = b"") -> SimpleNamespace:
    stdout = asyncio.StreamReader()
    stdout.feed_data(stdout_data)
    stdout.feed_eof()
    stderr = asyncio.StreamReader()
    stderr.feed_data(stderr_data)
    stderr.feed_eof()
    return SimpleNamespace(stdout=stdout, stderr=stderr, wait=AsyncMock(return_value=0))


@pytest.mark.parametrize(
    ("stdout_data", "stderr_data", "truncated"),
    [
        (b"", b"", False),
        (b"12345", b"", False),
        (b"", b"12345", False),
        (b"123456", b"", True),
        (b"", b"123456", True),
        (b"123", b"456", True),
    ],
)
async def test_collect_output_shares_budget_between_streams(
    stdout_data: bytes, stderr_data: bytes, truncated: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shell_module, "MAX_OUTPUT_CHARS", 5)
    process = _output_process(stdout_data, stderr_data)

    stdout, stderr, actual_truncated = await shell_module.collect_output(process)

    assert len(stdout) + len(stderr) == min(len(stdout_data) + len(stderr_data), 5)
    assert stdout_data.decode().startswith(stdout)
    assert stderr_data.decode().startswith(stderr)
    assert actual_truncated is truncated
    assert process.stdout.at_eof()
    assert process.stderr.at_eof()
    process.wait.assert_awaited_once()


@pytest.mark.parametrize(
    ("limit", "expected", "truncated"), [(10, "你🙂��", False), (3, "你🙂�", True)]
)
async def test_collect_output_decodes_split_utf8_and_replaces_invalid_bytes(
    limit: int, expected: str, truncated: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shell_module, "MAX_OUTPUT_CHARS", limit)
    data = "你🙂".encode() + b"\xff\xe4"
    reader = AsyncMock(side_effect=[bytes([byte]) for byte in data] + [b""])
    process = _output_process()
    process.stdout = SimpleNamespace(read=reader)

    stdout, stderr, actual_truncated = await shell_module.collect_output(process)

    assert (stdout, stderr, actual_truncated) == (expected, "", truncated)
    assert all(
        call.args == (shell_module.OUTPUT_READ_CHUNK_BYTES,) for call in reader.await_args_list
    )
    assert reader.await_count == len(data) + 1


async def test_collect_output_reads_both_pipes_concurrently() -> None:
    stderr_started = asyncio.Event()
    stdout_chunks = iter([b"out", b""])
    stderr_chunks = iter([b"err", b""])

    async def read_stdout(size: int) -> bytes:
        await stderr_started.wait()
        return next(stdout_chunks)

    async def read_stderr(size: int) -> bytes:
        stderr_started.set()
        return next(stderr_chunks)

    process = _output_process()
    process.stdout = SimpleNamespace(read=read_stdout)
    process.stderr = SimpleNamespace(read=read_stderr)

    result = await asyncio.wait_for(shell_module.collect_output(process), timeout=1)

    assert result == ("out", "err", False)


@pytest.mark.parametrize("cancel", [False, True], ids=["timeout", "cancellation"])
async def test_run_bash_drains_output_during_cleanup(
    cancel: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shell_module, "MAX_OUTPUT_CHARS", 5)
    stdout = asyncio.StreamReader()
    stdout.feed_data(b"partial output")
    stderr = asyncio.StreamReader()
    process = SimpleNamespace(
        pid=4321, stdout=stdout, stderr=stderr, returncode=None, wait=AsyncMock(return_value=-15)
    )
    tracked = Mock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr(psutil, "Process", Mock(return_value=tracked))

    async def stop(*args: object) -> None:
        stdout.feed_eof()
        stderr.feed_eof()
        process.returncode = -15

    cleanup = AsyncMock(side_effect=stop)
    monkeypatch.setattr(shell_module, "stop_process_tree", cleanup)
    collect_started = asyncio.Event()
    collect = shell_module.collect_output

    async def observe_collect(process: asyncio.subprocess.Process) -> tuple[str, str, bool]:
        collect_started.set()
        return await collect(process)

    monkeypatch.setattr(shell_module, "collect_output", observe_collect)
    task = asyncio.create_task(
        run_bash("long-command", timeout_seconds=1, workspace_dir=tmp_path, bash_path="bash")
    )
    await asyncio.wait_for(collect_started.wait(), timeout=1)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
    else:
        result = await asyncio.wait_for(task, timeout=2)
        assert result.status == ToolStatus.TIMED_OUT
        assert result.truncated is True
        assert result.output == {"stdout": "parti", "stderr": "", "exit_code": None}
    cleanup.assert_awaited_once_with(process, tracked)
    assert stdout.at_eof()
    assert stderr.at_eof()
    process.wait.assert_awaited_once()


async def test_run_bash_cleanup_failure_stops_and_reclaims_output_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """清理失败传播故障和原取消，并确保读取任务结束。"""
    process = SimpleNamespace(pid=4321, returncode=None)
    tracked = Mock()
    started = asyncio.Event()
    output_closed = asyncio.Event()
    readers: list[asyncio.Task[Any]] = []

    async def collect(process: Any) -> tuple[str, str, bool]:
        reader = asyncio.current_task()
        assert reader is not None
        readers.append(reader)
        started.set()
        await output_closed.wait()
        return "partial", "", False

    async def stop(*args: object) -> None:
        raise RuntimeError("CLEANUP_FAILED")

    cleanup = AsyncMock(side_effect=stop)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr(psutil, "Process", Mock(return_value=tracked))
    monkeypatch.setattr(shell_module, "collect_output", collect)
    monkeypatch.setattr(shell_module, "stop_process_tree", cleanup)
    monkeypatch.setattr(shell_module, "PROCESS_CLEANUP_TIMEOUT_SECONDS", 0.05)
    task = asyncio.create_task(run_bash("long-command", workspace_dir=Path.cwd()))

    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(RuntimeError, match="CLEANUP_FAILED") as caught:
            await asyncio.wait_for(task, timeout=2)
        assert isinstance(caught.value.__context__, asyncio.CancelledError)
        assert all(reader.done() for reader in readers)
    finally:
        # 即使被测实现异常退出，也回收模拟输出读取任务。
        output_closed.set()
        task.cancel()
        await asyncio.gather(task, *readers, return_exceptions=True)

    cleanup.assert_awaited_once_with(process, tracked)


@pytest.mark.parametrize("failure", ["identity", "reader", "output-timeout"])
async def test_run_bash_reclaims_resources_on_internal_failure(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    started = asyncio.Event()
    read_done = asyncio.Event()
    release = asyncio.Event()
    process = SimpleNamespace(pid=4321, returncode=None, kill=Mock(), wait=AsyncMock())
    identity_error = psutil.AccessDenied(4321)
    reader_error = RuntimeError("READ_FAILED")

    async def collect(process: Any) -> tuple[str, str, bool]:
        try:
            started.set()
            if failure == "reader":
                raise reader_error
            if failure == "output-timeout":
                await release.wait()
            return "", "", False
        finally:
            read_done.set()

    async def stop(*args: object) -> None:
        process.returncode = -15

    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr(shell_module, "collect_output", collect)
    monkeypatch.setattr(shell_module, "PROCESS_CLEANUP_TIMEOUT_SECONDS", 0.1)
    tracked = Mock(side_effect=identity_error) if failure == "identity" else Mock()
    monkeypatch.setattr(psutil, "Process", tracked)
    if failure != "identity":
        monkeypatch.setattr(shell_module, "stop_process_tree", AsyncMock(side_effect=stop))

    task = asyncio.create_task(run_bash("long-command", workspace_dir=Path.cwd()))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        if failure == "output-timeout":
            task.cancel()
        expected = psutil.AccessDenied if failure == "identity" else RuntimeError
        with pytest.raises(expected) as caught:
            await asyncio.wait_for(task, timeout=2)
        assert read_done.is_set()
        if failure == "identity":
            assert caught.value is identity_error
            process.kill.assert_called_once_with()
            process.wait.assert_awaited_once()
        elif failure == "reader":
            assert caught.value is reader_error
        else:
            assert "输出读取收尾超时" in str(caught.value)
            assert isinstance(caught.value.__context__, asyncio.CancelledError)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_collect_output_drains_large_real_process_output(tmp_path: Path) -> None:
    code = "import os; [(os.write(1, b'o' * 200000), os.write(2, b'e' * 200000)) for _ in range(3)]"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        cwd=tmp_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr, truncated = await asyncio.wait_for(
            shell_module.collect_output(process), timeout=5
        )
        assert len(stdout) + len(stderr) == shell_module.MAX_OUTPUT_CHARS
        assert truncated is True
        assert process.returncode == 0
    finally:
        if process.returncode is None:
            process.kill()
        await asyncio.wait_for(process.wait(), timeout=3)


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

    process = SimpleNamespace(pid=4321, returncode=None, kill=Mock(), wait=never_finishes)
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
