"""配置加载的单测(J)。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from code_agent.config import Settings

_ENV_NAMES = (
    "DEEPSEEK_API_KEY",
    "CODE_AGENT_MODEL",
    "CODE_AGENT_BASE_URL",
    "CODE_AGENT_MAX_STEPS",
    "CODE_AGENT_LOG_DIR",
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
    assert isinstance(settings.log_dir, Path)


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
