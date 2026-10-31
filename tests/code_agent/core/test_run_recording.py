"""运行边界与实际 JSONL 文件的契约。"""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from code_agent.core.run_recording import record_run
from code_agent.events import RunResult
from code_agent.sessions.manager import SessionManager
from code_agent.sessions.store import SessionStore


class ExampleAgent:
    def __init__(self, manager: SessionManager) -> None:
        self.session_manager = manager
        self.calls = 0

    @record_run
    async def run(self, result: RunResult | BaseException) -> RunResult:
        self.calls += 1
        self.session_manager.append_message({"role": "user", "content": "任务"})
        if isinstance(result, BaseException):
            raise result
        return result


def records(root: Path) -> list[dict]:
    path = next(root.glob("*.jsonl"))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def test_two_runs_record_messages_and_actual_results_in_order(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    agent = ExampleAgent(SessionManager(tmp_path, root))
    first = RunResult(status="completed", text="完成")
    second = RunResult(status="max_steps", error="达到上限")

    assert await agent.run(first) == first
    assert await agent.run(second) == second

    rows = records(root)
    assert [row["type"] for row in rows] == [
        "session_start",
        "run_start",
        "message",
        "run_end",
        "run_start",
        "message",
        "run_end",
    ]
    assert rows[1]["run_id"] == rows[3]["run_id"]
    assert rows[4]["run_id"] == rows[6]["run_id"] != rows[1]["run_id"]
    assert rows[3] == {
        "type": "run_end",
        "run_id": rows[1]["run_id"],
        "status": "completed",
        "error": None,
    }
    assert rows[6] == {
        "type": "run_end",
        "run_id": rows[4]["run_id"],
        "status": "max_steps",
        "error": "达到上限",
    }


@pytest.mark.parametrize("failure", [asyncio.CancelledError(), RuntimeError("故障")])
async def test_exception_records_end_and_propagates_original(
    tmp_path: Path, failure: BaseException
) -> None:
    root = tmp_path / "sessions"
    agent = ExampleAgent(SessionManager(tmp_path, root))
    with pytest.raises(type(failure)) as caught:
        await agent.run(failure)
    assert caught.value is failure
    end = records(root)[-1]
    assert end["status"] == (
        "cancelled" if isinstance(failure, asyncio.CancelledError) else "exception"
    )
    assert end["error"] == type(failure).__name__
    assert await agent.run(RunResult(status="failed", error="模型故障")) == RunResult(
        status="failed", error="模型故障"
    )


async def test_start_failure_blocks_run_and_future_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = SessionManager(tmp_path, tmp_path / "sessions")
    agent = ExampleAgent(manager)
    error = OSError("写入失败")
    write = Mock(side_effect=error)
    monkeypatch.setattr(SessionStore, "append_record", write)
    for _ in range(2):
        with pytest.raises(OSError) as caught:
            await agent.run(RunResult(status="completed"))
        assert caught.value is error
    assert agent.calls == 0
    assert manager.get_messages() == []
    write.assert_called_once()


@pytest.mark.parametrize("failure", [None, RuntimeError("原始异常"), asyncio.CancelledError()])
async def test_end_failure_is_reported_without_masking_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException | None
) -> None:
    manager = SessionManager(tmp_path, tmp_path / "sessions")
    agent = ExampleAgent(manager)
    original = SessionStore.append_record
    error = OSError("终态写入失败")

    def append(store: SessionStore, session_id: str, record: dict) -> None:
        if record["type"] == "run_end":
            raise error
        original(store, session_id, record)

    monkeypatch.setattr(SessionStore, "append_record", append)
    outcome = failure if failure is not None else RunResult(status="completed")
    with pytest.raises(type(failure) if failure is not None else OSError) as caught:
        await agent.run(outcome)
    assert caught.value is (failure if failure is not None else error)
    assert manager.storage_error is error
    if failure is not None:
        assert "OSError" in caught.value.__notes__[0]
    assert [row["type"] for row in records(tmp_path / "sessions")] == [
        "session_start",
        "run_start",
        "message",
    ]


async def test_memory_only_and_overlapping_runs(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, None)
    run_id = manager.start_run()
    with pytest.raises(RuntimeError, match="运行进行中"):
        await ExampleAgent(manager).run(RunResult(status="completed"))
    with pytest.raises(ValueError, match="不匹配"):
        manager.end_run("other", status="completed", error=None)
    manager.end_run(run_id, status="completed", error=None)
    assert await ExampleAgent(manager).run(RunResult(status="completed")) == RunResult(
        status="completed"
    )
