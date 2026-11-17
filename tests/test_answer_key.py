"""The engine must find every duplicate the generator planted.

Each planted type maps to the rule that should catch it. A pair is found
when both of its lines appear in the same group of that rule.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from echo_check.config import load_config
from echo_check.engine import CheckResult, check_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GENERATOR = PROJECT_ROOT / "generator" / "generate_nacha.py"
NACHA_CONFIG = PROJECT_ROOT / "configs" / "nacha.yaml"
SAMPLE = PROJECT_ROOT / "data" / "sample"

TYPE_TO_RULE = {
    "exact_line": "exact_line",
    "trace_collision": "trace_number",
    "same_payment": "same_payment",
    "near_amount": "near_amount",
    "resent_batch": "same_payment",
}


def expected_pairs(answers: dict[str, Any]) -> list[tuple[str, int, int]]:
    """Turn the answer key into (rule name, line, line) pairs."""
    pairs = []
    for planted in answers["planted"]:
        rule = TYPE_TO_RULE[planted["type"]]
        if planted["type"] == "resent_batch":
            for first, second in planted["pairs"]:
                pairs.append((rule, first, second))
        else:
            for duplicate in planted["duplicate_lines"]:
                pairs.append((rule, planted["original_line"], duplicate))
    return pairs


def missed_pairs(result: CheckResult, answers: dict[str, Any]) -> list[tuple[str, int, int]]:
    """Return every planted pair that no group of its rule contains."""
    lines_by_rule = {rule.name: [set(group.lines) for group in rule.groups] for rule in result.rules}
    missed = []
    for rule, first, second in expected_pairs(answers):
        found = any(first in group and second in group for group in lines_by_rule[rule])
        if not found:
            missed.append((rule, first, second))
    return missed


def check_generated(prefix: Path) -> tuple[CheckResult, dict[str, Any]]:
    """Check <prefix>.ach and load <prefix>.answers.json."""
    result = check_file(prefix.with_suffix(".ach"), load_config(NACHA_CONFIG))
    answers = json.loads(prefix.with_suffix(".answers.json").read_text(encoding="utf-8"))
    return result, answers


def test_sample_every_planted_pair_is_found() -> None:
    result, answers = check_generated(SAMPLE)

    assert len(expected_pairs(answers)) == answers["planted_count"]
    assert missed_pairs(result, answers) == []


def test_sample_counts() -> None:
    """The sample has no bad lines, and every entry record is checked."""
    result, answers = check_generated(SAMPLE)

    assert result.total_lines == answers["total_lines"]
    assert result.records_checked == answers["entry_records"]
    assert result.bad_lines == []


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_bigger_generated_files(tmp_path: Path, seed: int) -> None:
    """More seeds and more duplicates of each type."""
    prefix = tmp_path / f"seed{seed}"
    command = [
        sys.executable, str(GENERATOR), "--out", str(prefix), "--seed", str(seed),
        "--batches", "5", "--entries", "40", "--dupes", "10",
    ]
    subprocess.run(command, check=True, capture_output=True)

    result, answers = check_generated(prefix)
    assert missed_pairs(result, answers) == []
