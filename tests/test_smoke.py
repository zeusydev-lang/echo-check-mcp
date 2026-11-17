"""Smoke tests: the package imports and the `dupcheck` command is installed."""

import subprocess

import echo_check
from echo_check.cli import STUB_MESSAGE


def test_package_has_a_version() -> None:
    """The package imports and reports a version."""
    assert echo_check.__version__ == "0.1.0"


def test_dupcheck_command_runs() -> None:
    """The `dupcheck` entry point from pyproject.toml is installed and runs."""
    result = subprocess.run(["dupcheck"], capture_output=True, text=True)
    assert result.returncode == 0
    assert STUB_MESSAGE in result.stdout
