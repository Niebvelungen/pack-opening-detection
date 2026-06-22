"""Smoke tests confirming the package imports and the CLI wires up.

Real contract/pipeline tests arrive with each milestone (see implementation-plan.md).
"""

from typer.testing import CliRunner

import pack_config_miner
from pack_config_miner.cli import app


def test_package_has_version() -> None:
    assert pack_config_miner.__version__


def test_cli_version_command() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert pack_config_miner.__version__ in result.stdout


def test_cli_help_lists_run() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.stdout
