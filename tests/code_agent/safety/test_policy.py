"""执行环境校验测试，使用临时文件模拟 Bash 路径。"""

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from code_agent.safety import policy
from code_agent.safety.policy import resolve_execution_environment


@pytest.fixture(autouse=True)
def _forbid_process_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    launch = Mock(side_effect=AssertionError("环境校验不得启动子进程"))
    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)


@pytest.fixture
def executable(tmp_path: Path) -> Path:
    path = tmp_path / "bin with spaces" / "review-bash.exe"
    path.parent.mkdir()
    path.touch()
    path.chmod(0o700)
    return path


def test_resolve_normalizes_workspace_and_binds_executable_without_changing_cwd(
    tmp_path: Path, executable: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(executable.parent))
    monkeypatch.setenv("PATHEXT", ".EXE")
    monkeypatch.setenv("NoDefaultCurrentDirectoryInExePath", "1")
    original_cwd = Path.cwd()
    supplied_workspace = Path("workspace") / "child" / ".."

    environment = resolve_execution_environment(
        workspace_dir=supplied_workspace, bash_path=executable.name
    )
    monkeypatch.setenv("PATH", "")

    assert environment.workspace_dir == workspace.resolve()
    assert environment.bash_path == str(executable.resolve())
    assert Path.cwd() == original_cwd
    assert not (workspace / "child").exists()
    assert supplied_workspace == Path("workspace") / "child" / ".."


def test_resolve_expands_home_in_workspace_and_executable(
    tmp_path: Path, executable: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    environment = resolve_execution_environment(
        workspace_dir=Path("~") / "workspace",
        bash_path="~/bin with spaces/review-bash.exe",
    )

    assert environment.workspace_dir == workspace.resolve()
    assert environment.bash_path == str(executable.resolve())


@pytest.mark.parametrize("local_exists", [True, False], ids=["local-exists", "local-missing"])
def test_explicit_relative_executable_never_falls_back_to_path(
    tmp_path: Path,
    executable: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_exists: bool,
) -> None:
    """保留 ./ 的语义,即使 PATH 中存在同名文件也不能替换指定路径。"""
    local = tmp_path / "local"
    local.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    local_executable = local / executable.name
    if local_exists:
        local_executable.touch()
        local_executable.chmod(0o700)
    monkeypatch.chdir(local)
    monkeypatch.setenv("PATH", str(executable.parent))
    monkeypatch.setenv("PATHEXT", ".EXE")
    monkeypatch.setenv("NoDefaultCurrentDirectoryInExePath", "1")
    configured = f"./{executable.name}"

    if local_exists:
        environment = resolve_execution_environment(workspace_dir=workspace, bash_path=configured)
        assert environment.bash_path == str(local_executable.resolve())
        assert environment.bash_path != str(executable.resolve())
    else:
        with pytest.raises(ValueError) as caught:
            resolve_execution_environment(workspace_dir=workspace, bash_path=configured)
        assert "bash_path" in str(caught.value)
        assert configured in str(caught.value)


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_invalid_workspace_is_rejected_before_executable_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    workspace = tmp_path / "invalid-workspace"
    if kind == "file":
        workspace.touch()
    lookup = Mock()
    monkeypatch.setattr(policy.shutil, "which", lookup)

    with pytest.raises(ValueError) as caught:
        resolve_execution_environment(workspace_dir=workspace, bash_path="bash")

    assert "workspace_dir" in str(caught.value)
    assert str(workspace) in str(caught.value)
    lookup.assert_not_called()
    if kind == "missing":
        assert not workspace.exists()


@pytest.mark.parametrize("bash_path", ["", " ", "\t\r\n"])
def test_blank_executable_is_rejected_without_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bash_path: str
) -> None:
    lookup = Mock()
    monkeypatch.setattr(policy.shutil, "which", lookup)

    with pytest.raises(ValueError, match="bash_path"):
        resolve_execution_environment(workspace_dir=tmp_path, bash_path=bash_path)

    lookup.assert_not_called()


def test_missing_executable_error_includes_actual_configured_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PATHEXT", ".EXE")
    monkeypatch.setenv("NoDefaultCurrentDirectoryInExePath", "1")
    configured = "missing-policy-review-bash.exe"

    with pytest.raises(ValueError) as caught:
        resolve_execution_environment(workspace_dir=tmp_path, bash_path=configured)

    assert "bash_path" in str(caught.value)
    assert configured in str(caught.value)
    assert "{candidate" not in str(caught.value)


def test_lookup_result_must_be_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy.shutil, "which", Mock(return_value=str(tmp_path)))

    with pytest.raises(ValueError) as caught:
        resolve_execution_environment(workspace_dir=tmp_path, bash_path="bash")

    assert "bash_path" in str(caught.value)
    assert str(tmp_path) in str(caught.value)


@pytest.mark.parametrize("error_type", [OSError, RuntimeError])
@pytest.mark.parametrize(
    "stage", ["workspace_expanduser", "workspace_resolve", "bash_lookup", "bash_resolve"]
)
def test_path_errors_become_configuration_errors_with_original_cause(
    tmp_path: Path,
    executable: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
    stage: str,
) -> None:
    failure = error_type("simulated path failure")
    original_resolve = Path.resolve
    expected_field = "workspace_dir" if stage.startswith("workspace") else "bash_path"

    def resolve(path: Path, *args: object, **kwargs: object) -> Path:
        target = tmp_path if stage == "workspace_resolve" else executable
        if path == target:
            raise failure
        return original_resolve(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(policy.shutil, "which", Mock(return_value=str(executable)))
        if stage == "workspace_expanduser":
            scoped.setattr(Path, "expanduser", Mock(side_effect=failure))
        elif stage == "bash_lookup":
            scoped.setattr(policy.shutil, "which", Mock(side_effect=failure))
        else:
            scoped.setattr(Path, "resolve", resolve)

        with pytest.raises(ValueError) as caught:
            resolve_execution_environment(workspace_dir=tmp_path, bash_path="bash")

    assert expected_field in str(caught.value)
    assert "simulated path failure" in str(caught.value)
    assert caught.value.__cause__ is failure


def test_unrelated_programming_error_is_not_hidden_as_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = LookupError("unexpected lookup failure")
    monkeypatch.setattr(policy.shutil, "which", Mock(side_effect=failure))

    with pytest.raises(LookupError) as caught:
        resolve_execution_environment(workspace_dir=tmp_path, bash_path="bash")

    assert caught.value is failure
