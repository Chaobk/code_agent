"""命令行入口的单测(H)。"""

from typer.testing import CliRunner

from code_agent import __version__
from code_agent.ui.cli import app

runner = CliRunner()


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_missing_prompt_exits_nonzero() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 1
