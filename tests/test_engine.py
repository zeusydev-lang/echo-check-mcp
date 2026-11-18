"""Unit tests for the engine, using a tiny 20-character layout.

Layout of the test records (1-based positions):
  1      kind     ("6" = check this line)
  2-9    account  (sensitive, strip)
  10-15  amount   (int)
  16-20  ref
"""

from pathlib import Path
from typing import Any

import pytest

from echo_check.config import LayoutConfig, load_config, parse_config
from echo_check.engine import (
    CheckResult,
    Group,
    check_file,
    clean_batch_id,
    find_near_pairs,
    mask_line,
    mask_value,
    normalize,
    slice_field,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = PROJECT_ROOT / "data" / "sample.ach"
NACHA_CONFIG = PROJECT_ROOT / "configs" / "nacha.yaml"


def tiny_config() -> LayoutConfig:
    """The 20-character test layout with one rule of each kind."""
    data: dict[str, Any] = {
        "name": "tiny",
        "record_length": 20,
        "filter": {"field": "kind", "equals": "6"},
        "fields": [
            {"name": "kind", "start": 1, "length": 1},
            {"name": "account", "start": 2, "length": 8, "normalize": "strip", "sensitive": True},
            {"name": "amount", "start": 10, "length": 6, "normalize": "int"},
            {"name": "ref", "start": 16, "length": 5},
        ],
        "duplicate_rules": [
            {"name": "exact", "keys": ["_line"]},
            {"name": "same_ref", "keys": ["ref"]},
            {"name": "same", "keys": ["account", "amount"]},
            {"name": "near", "keys": ["account"], "tolerance": {"field": "amount", "max_diff": 500}},
        ],
    }
    return parse_config(data)


def record(account: str, amount: int, ref: str, kind: str = "6") -> str:
    """Build one 20-character test line."""
    return kind + account.ljust(8) + str(amount).rjust(6, "0") + ref.ljust(5)


def run_check(tmp_path: Path, lines: list[str], ending: str = "\n", mask: bool = True) -> CheckResult:
    """Write lines to a temp file and check it with the tiny config."""
    path = tmp_path / "test.txt"
    path.write_bytes((ending.join(lines) + ending).encode("ascii"))
    return check_file(path, tiny_config(), mask=mask)


def groups_of(result: CheckResult, rule_name: str) -> list[Group]:
    """The groups found by one rule."""
    for rule in result.rules:
        if rule.name == rule_name:
            return rule.groups
    raise KeyError(rule_name)


def lines_of(result: CheckResult, rule_name: str) -> list[list[int]]:
    """Just the line numbers of each group, for short asserts."""
    return [group.lines for group in groups_of(result, rule_name)]


# ---------- small helpers ----------


def test_record_helper_makes_20_characters() -> None:
    assert len(record("ACC1", 100, "R1")) == 20


def test_slice_field_uses_1_based_positions() -> None:
    line = record("ACC1", 1234, "R1")
    account = tiny_config().field_by_name()["account"]

    assert slice_field(line, account) == "ACC1    "


def test_normalize_modes() -> None:
    assert normalize("  AB  ", "none") == "  AB  "
    assert normalize("  AB  ", "strip") == "AB"
    assert normalize("000120", "int") == 120


@pytest.mark.parametrize("raw", ["", "   ", "12A4", "-5", "1_000"])
def test_normalize_int_rejects_non_digits(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize(raw, "int")


def test_mask_value_keeps_last_four() -> None:
    assert mask_value("12345678901") == "****8901"
    assert mask_value("12345") == "****2345"


def test_mask_value_hides_short_values_completely() -> None:
    assert mask_value("123") == "***"


def test_mask_line_stars_sensitive_fields_only() -> None:
    line = record("SECRET1", 100, "R1")

    masked = mask_line(line, tiny_config().fields)
    assert masked == "6" + "*" * 8 + "000100R1   "
    assert len(masked) == len(line)


# ---------- near-amount window ----------


def test_near_pairs_window_edges() -> None:
    """A difference of 0 is out, 1 to 500 is in, 501 is out."""
    entries = [(1000, 1), (1000, 2), (1500, 3), (2001, 4)]

    pairs = find_near_pairs(entries, max_diff=500)
    # 1000->1500 is 500 (in), 1500->2001 is 501 (out), 1000->1000 is 0 (out)
    assert pairs == [(1, 3), (2, 3)]


def test_near_pairs_line_order_is_smallest_first() -> None:
    assert find_near_pairs([(1200, 9), (1000, 4)], max_diff=500) == [(4, 9)]


# ---------- reading the file ----------


def test_filter_skips_other_record_types(tmp_path: Path) -> None:
    lines = [record("ACC1", 100, "R1", kind="5"), record("ACC1", 100, "R1")]

    result = run_check(tmp_path, lines)
    assert result.total_lines == 2
    assert result.records_checked == 1
    assert result.stats.records_skipped == 1


def test_wrong_length_is_a_bad_line_not_a_crash(tmp_path: Path) -> None:
    lines = [record("ACC1", 100, "R1"), "6SHORT", record("ACC2", 200, "R2") + "X"]

    result = run_check(tmp_path, lines)
    assert [bad.line for bad in result.bad_lines] == [2, 3]
    assert result.bad_lines[0].reason == "length 6, expected 20"
    assert result.records_checked == 1


def test_bad_number_is_a_bad_line(tmp_path: Path) -> None:
    line = "6ACC1    0001A0R1   "

    result = run_check(tmp_path, [line])
    assert result.records_checked == 0
    assert "field 'amount' is not a whole number: '0001A0'" in result.bad_lines[0].reason


def test_crlf_line_endings_work(tmp_path: Path) -> None:
    lines = [record("ACC1", 100, "R1"), record("ACC1", 100, "R1")]

    result = run_check(tmp_path, lines, ending="\r\n")
    assert result.bad_lines == []
    assert lines_of(result, "exact") == [[1, 2]]


def test_non_ascii_byte_keeps_the_line_length(tmp_path: Path) -> None:
    """A stray byte counts as one position, so the line isn't flagged as bad."""
    line = record("ACC1", 100, "R1").encode("ascii")
    line = line[:17] + b"\xe9" + line[18:]
    path = tmp_path / "test.txt"
    path.write_bytes(line + b"\n")

    result = check_file(path, tiny_config())
    assert result.bad_lines == []
    assert result.records_checked == 1


# ---------- rules ----------


def test_exact_line_rule(tmp_path: Path) -> None:
    lines = [record("ACC1", 100, "R1"), record("ACC2", 100, "R2"), record("ACC1", 100, "R1")]

    assert lines_of(run_check(tmp_path, lines), "exact") == [[1, 3]]


def test_single_field_rule(tmp_path: Path) -> None:
    lines = [record("ACC1", 100, "R1"), record("ACC2", 900, "R1"), record("ACC3", 300, "R3")]

    assert lines_of(run_check(tmp_path, lines), "same_ref") == [[1, 2]]


def test_multi_field_rule_needs_every_key_to_match(tmp_path: Path) -> None:
    lines = [
        record("ACC1", 100, "R1"),
        record("ACC1", 100, "R2"),  # same account and amount: match
        record("ACC1", 101, "R3"),  # different amount: no match
    ]

    assert lines_of(run_check(tmp_path, lines), "same") == [[1, 2]]


def test_group_with_three_lines(tmp_path: Path) -> None:
    lines = [record("ACC1", 100, f"R{n}") for n in range(3)]

    assert lines_of(run_check(tmp_path, lines), "same") == [[1, 2, 3]]


def test_near_rule_reports_pairs(tmp_path: Path) -> None:
    lines = [
        record("ACC1", 1000, "R1"),
        record("ACC1", 1300, "R2"),  # 300 away from line 1: near
        record("ACC1", 1000, "R3"),  # equal to line 1: not near, but near line 2
        record("ACC2", 1100, "R4"),  # different account: never near ACC1
    ]

    assert lines_of(run_check(tmp_path, lines), "near") == [[1, 2], [2, 3]]


def test_groups_are_sorted_by_first_line(tmp_path: Path) -> None:
    lines = [
        record("ACC2", 200, "R1"),
        record("ACC1", 100, "R2"),
        record("ACC1", 100, "R3"),
        record("ACC2", 200, "R4"),
    ]

    assert lines_of(run_check(tmp_path, lines), "same") == [[1, 4], [2, 3]]


def test_no_duplicates_means_no_groups(tmp_path: Path) -> None:
    lines = [record("ACC1", 100, "R1"), record("ACC2", 5000, "R2")]

    result = run_check(tmp_path, lines)
    assert all(rule.groups == [] for rule in result.rules)


# ---------- masking in results ----------


def test_keys_are_masked_by_default(tmp_path: Path) -> None:
    lines = [record("SECRET01", 100, "R1"), record("SECRET01", 100, "R1")]

    result = run_check(tmp_path, lines)
    assert result.masked is True
    assert groups_of(result, "same")[0].key == {"account": "****ET01", "amount": 100}
    assert groups_of(result, "exact")[0].key == {"_line": "6********000100R1   "}


def test_no_sensitive_value_anywhere_in_masked_output(tmp_path: Path) -> None:
    """The full account must not appear anywhere in the JSON."""
    lines = [record("SECRET01", 100, "R1"), record("SECRET01", 100, "R1"), "6SECRET01" + "X" * 11]

    output = run_check(tmp_path, lines).model_dump_json()
    assert "SECRET01" not in output


def test_unmasked_shows_raw_values(tmp_path: Path) -> None:
    lines = [record("SECRET01", 100, "R1"), record("SECRET01", 100, "R1")]

    result = run_check(tmp_path, lines, mask=False)
    assert result.masked is False
    assert groups_of(result, "same")[0].key == {"account": "SECRET01", "amount": 100}


# ---------- stats ----------


def test_stats_count_groups_and_lines(tmp_path: Path) -> None:
    lines = [record("ACC1", 100, "R1"), record("ACC1", 100, "R2"), record("ACC1", 100, "R3")]

    stats = run_check(tmp_path, lines).stats
    assert stats.groups_per_rule["same"] == 1
    assert stats.lines_in_groups_per_rule["same"] == 3
    assert stats.groups_per_rule["exact"] == 0


# ---------- batches ----------


def test_no_batch_section_means_no_breakdown(tmp_path: Path) -> None:
    result = run_check(tmp_path, [record("A1", 100, "r1"), record("A1", 100, "r2")])
    assert result.batches is None


def test_resent_batch_shows_as_a_batch_link() -> None:
    """The sample file's resent batch (3 sent again as 4) links batches 3 and 4."""
    result = check_file(SAMPLE, load_config(NACHA_CONFIG))

    assert result.batches is not None
    assert result.batches.records_per_batch == {"1": 9, "2": 11, "3": 8, "4": 8}
    assert result.batches.groups_by_batches["same_payment"]["3-4"] == 8


@pytest.mark.parametrize(("raw", "expected"), [("0000003", "3"), ("  12", "12"), ("B-7 ", "B-7")])
def test_clean_batch_id(raw: str, expected: str) -> None:
    assert clean_batch_id(raw) == expected


def test_lines_are_traced_to_their_batch(tmp_path: Path) -> None:
    """A header line "5...<id>" starts a batch; records after it belong to it."""
    config = parse_config(
        {**tiny_config().model_dump(), "batch": {"header_starts_with": "5", "id_start": 18, "id_length": 3}}
    )
    header_1 = "5" + " " * 16 + "001"
    header_2 = "5" + " " * 16 + "002"
    lines = [
        header_1, record("A1", 100, "r1"), record("B2", 200, "r2"),
        header_2, record("A1", 100, "r3"), record("B2", 200, "r4"), record("B2", 200, "r5"),
    ]
    path = tmp_path / "batches.txt"
    path.write_text("\n".join(lines) + "\n", encoding="ascii")

    result = check_file(path, config)

    assert result.batches is not None
    assert result.batches.records_per_batch == {"1": 2, "2": 3}
    # A1 pair spans batches 1-2; B2 group (3 lines) also spans 1-2.
    assert result.batches.groups_by_batches["same"] == {"1-2": 2}
