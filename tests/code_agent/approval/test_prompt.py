"""审批文本输入、SIGINT 分流与处理器恢复；不使用真实终端。"""

import signal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from code_agent.approval import prompt as prompt_module


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prompt_module.sys, "stdin", SimpleNamespace(isatty=lambda: True))


@pytest.mark.parametrize("text", ["", " \t "])
def test_blank_input_denies_without_reading_again(
    terminal: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    reader = Mock(side_effect=[text, "o"])
    monkeypatch.setattr("builtins.input", reader)

    assert prompt_module.prompt_decision("待执行的命令") == "r"

    reader.assert_called_once_with("> ")
    output = capsys.readouterr().err
    assert "[r/Enter] 拒绝" in output
    assert "无效输入" not in output


@pytest.mark.parametrize("text, expected", [("o", "o"), (" A ", "a"), ("r", "r")])
def test_explicit_choice_is_normalized(
    terminal: None, monkeypatch: pytest.MonkeyPatch, text: str, expected: str
) -> None:
    reader = Mock(return_value=text)
    monkeypatch.setattr("builtins.input", reader)

    assert prompt_module.prompt_decision("待执行的命令") == expected
    reader.assert_called_once()


@pytest.mark.parametrize(
    "inputs, expected, reads",
    [(["invalid", "o"], "o", 2), (["invalid", "", "o"], "r", 2), (["x", "y", "z", "o"], "r", 3)],
)
def test_invalid_input_retries_without_changing_blank_or_retry_limit_behavior(
    terminal: None,
    monkeypatch: pytest.MonkeyPatch,
    inputs: list[str],
    expected: str,
    reads: int,
) -> None:
    reader = Mock(side_effect=inputs)
    monkeypatch.setattr("builtins.input", reader)

    assert prompt_module.prompt_decision("待执行的命令") == expected
    assert reader.call_count == reads


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_input_exception_returns_denial(
    terminal: None, monkeypatch: pytest.MonkeyPatch, error: type[BaseException]
) -> None:
    reader = Mock(side_effect=error)
    monkeypatch.setattr("builtins.input", reader)

    assert prompt_module.prompt_decision("待执行的命令") == "r"
    reader.assert_called_once()


def test_noninteractive_input_denies_without_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prompt_module.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    reader = Mock(side_effect=AssertionError("非交互入口不应读取输入"))
    monkeypatch.setattr("builtins.input", reader)

    assert prompt_module.prompt_decision("待执行的命令") == "r"
    reader.assert_not_called()


@pytest.mark.parametrize("outcome", ["o", EOFError, KeyboardInterrupt, RuntimeError])
def test_approval_restores_previous_signal_handler_on_every_exit(
    terminal: None, monkeypatch: pytest.MonkeyPatch, outcome: str | type[BaseException]
) -> None:
    previous_handler = Mock()
    original_handler = signal.signal(signal.SIGINT, previous_handler)

    def read_input(prompt: str) -> str:
        assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
        if isinstance(outcome, str):
            return outcome
        raise outcome

    monkeypatch.setattr("builtins.input", read_input)
    try:
        if outcome is RuntimeError:
            with pytest.raises(RuntimeError):
                prompt_module.prompt_decision("待执行的命令")
        else:
            expected = outcome if isinstance(outcome, str) else "r"
            assert prompt_module.prompt_decision("待执行的命令") == expected
        assert signal.getsignal(signal.SIGINT) is previous_handler
        previous_handler.assert_not_called()
    finally:
        signal.signal(signal.SIGINT, original_handler)
