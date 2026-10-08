"""Bash 执行器单测(D/F):使用假进程,不依赖本机 Bash 或真实模型。"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from code_agent.tools.builtin.shell import run_bash


async def test_run_bash_uses_fixed_workspace_and_returns_process_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证启动参数、执行目录与完整结果;执行器实现前应失败。"""
    stdout = asyncio.StreamReader()
    stdout.feed_data("文件内容\n".encode("utf-8"))
    stdout.feed_eof()
    stderr = asyncio.StreamReader()
    stderr.feed_eof()
    process = SimpleNamespace(
        stdout=stdout,
        stderr=stderr,
        returncode=0,
        wait=AsyncMock(return_value=0),
        communicate=AsyncMock(return_value=("文件内容\n".encode("utf-8"), b"")),
    )
    create_process = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    bash_path = "configured-bash"

    result = await run_bash("cat example.txt", workspace_dir=tmp_path, bash_path=bash_path)

    create_process.assert_awaited_once()
    call = create_process.await_args
    assert call.args == (bash_path, "--noprofile", "--norc", "-c", "cat example.txt")
    assert Path(call.kwargs["cwd"]) == tmp_path
    assert call.kwargs["stdin"] == asyncio.subprocess.DEVNULL
    assert call.kwargs["stdout"] == asyncio.subprocess.PIPE
    assert call.kwargs["stderr"] == asyncio.subprocess.PIPE
    assert json.loads(result) == {
        "stdout": "文件内容\n",
        "stderr": "",
        "exit_code": 0,
        "timed_out": False,
        "truncated": False,
    }
