"""Tests for the `dupcheck` command."""

import json
from pathlib import Path

import pytest

from echo_check.cli import EXIT_INPUT_ERROR, EXIT_OK, main

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = str(PROJECT_ROOT / "data" / "sample.ach")
NACHA_CONFIG = str(PROJECT_ROOT / "configs" / "nacha.yaml")


def test_json_goes_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([SAMPLE, "--config", NACHA_CONFIG])

    result = json.loads(capsys.readouterr().out)
    assert exit_code == EXIT_OK
    assert result["config"] == "nacha_entry"
    assert result["masked"] is True
    assert [rule["name"] for rule in result["rules"]] == [
        "exact_line", "trace_number", "same_payment", "near_amount",
    ]


def test_out_writes_a_file_and_prints_a_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "results.json"

    exit_code = main([SAMPLE, "--config", NACHA_CONFIG, "--out", str(out)])

    printed = capsys.readouterr().out
    assert exit_code == EXIT_OK
    assert "near_amount: 1 group(s), 2 lines" in printed
    assert json.loads(out.read_text(encoding="utf-8"))["records_checked"] == 36


def test_unmasked_flag(capsys: pytest.CaptureFixture[str]) -> None:
    main([SAMPLE, "--config", NACHA_CONFIG, "--unmasked"])

    assert json.loads(capsys.readouterr().out)["masked"] is False


def test_masked_output_has_no_full_account_numbers(capsys: pytest.CaptureFixture[str]) -> None:
    """No account number from the sample file shows up in masked output."""
    main([SAMPLE, "--config", NACHA_CONFIG])
    output = capsys.readouterr().out

    for line in Path(SAMPLE).read_text(encoding="ascii").splitlines():
        if line.startswith("6"):
            account = line[12:29].strip()
            assert account not in output


def test_bad_config_exits_with_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "bad.yaml"
    config.write_text("name: x\n", encoding="utf-8")

    exit_code = main([SAMPLE, "--config", str(config)])

    assert exit_code == EXIT_INPUT_ERROR
    assert "problem(s) in config" in capsys.readouterr().err


def test_missing_file_exits_with_2(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["no_such_file.ach", "--config", NACHA_CONFIG])

    assert exit_code == EXIT_INPUT_ERROR
    assert "file not found" in capsys.readouterr().err


def test_missing_config_argument_exits_with_2() -> None:
    with pytest.raises(SystemExit) as caught:
        main([SAMPLE])
    assert caught.value.code == EXIT_INPUT_ERROR
