"""运行配置的加载与校验测试。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from code_agent.config import Settings

_ENV_NAMES = (
    "DEEPSEEK_API_KEY",
    "CODE_AGENT_MODEL",
    "CODE_AGENT_BASE_URL",
    "CODE_AGENT_MAX_STEPS",
    "CODE_AGENT_REQUEST_TIMEOUT_SECONDS",
    "CODE_AGENT_LOG_DIR",
    "CODE_AGENT_WORKSPACE_DIR",
    "CODE_AGENT_BASH_PATH",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离外部环境,测试只看自己设置的值。"""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _settings() -> Settings:
    """显式忽略 .env,保证测试结果确定。"""
    return Settings(_env_file=None)


def test_defaults() -> None:
    settings = _settings()
    assert settings.api_key == ""
    assert settings.model == "deepseek/deepseek-chat"
    assert settings.base_url == "https://api.deepseek.com"
    assert settings.max_steps == 30
    assert settings.request_timeout_seconds == 60.0
    assert isinstance(settings.log_dir, Path)
    assert settings.workspace_dir == Path.cwd()
    assert settings.bash_path == "bash"


def test_execution_environment_uses_code_agent_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = Path.cwd() / "sample-workspace"
    monkeypatch.setenv("CODE_AGENT_WORKSPACE_DIR", str(workspace))
    monkeypatch.setenv("CODE_AGENT_BASH_PATH", "configured-bash")

    settings = _settings()

    assert settings.workspace_dir == workspace
    assert settings.bash_path == "configured-bash"


def test_workspace_default_is_evaluated_when_settings_are_loaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert _settings().workspace_dir == tmp_path


def test_api_key_uses_deepseek_env_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert _settings().api_key == "sk-test"


def test_other_fields_use_code_agent_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_AGENT_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("CODE_AGENT_MAX_STEPS", "7")
    settings = _settings()
    assert settings.model == "openai/gpt-4o-mini"
    assert settings.max_steps == 7


def test_max_steps_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_AGENT_MAX_STEPS", "0")
    with pytest.raises(ValidationError):
        _settings()


def test_request_timeout_uses_code_agent_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_AGENT_REQUEST_TIMEOUT_SECONDS", "12.5")

    assert _settings().request_timeout_seconds == 12.5


def test_request_timeout_accepts_positive_fraction() -> None:
    settings = Settings(_env_file=None, request_timeout_seconds=0.25)

    assert settings.request_timeout_seconds == 0.25


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf", "not-a-number"])
def test_request_timeout_rejects_nonpositive_nonfinite_or_invalid_value(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("CODE_AGENT_REQUEST_TIMEOUT_SECONDS", value)

    with pytest.raises(ValidationError):
        _settings()
