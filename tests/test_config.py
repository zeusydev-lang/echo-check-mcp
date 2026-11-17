"""Tests for loading and validating layout configs."""

from pathlib import Path
from typing import Any

import pytest

from echo_check.config import ConfigError, LayoutConfig, load_config, parse_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NACHA_CONFIG = PROJECT_ROOT / "configs" / "nacha.yaml"


def minimal_config() -> dict[str, Any]:
    """A small valid config. Each test breaks one thing in a fresh copy."""
    return {
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
            {"name": "same", "keys": ["account", "amount"]},
            {"name": "near", "keys": ["account"], "tolerance": {"field": "amount", "max_diff": 100}},
        ],
    }


def problems_for(data: dict[str, Any]) -> str:
    """Validate data that should fail, and return all problems as one string."""
    with pytest.raises(ConfigError) as caught:
        parse_config(data)
    return "\n".join(caught.value.problems)


# ---------- valid configs ----------


def test_nacha_config_loads() -> None:
    """The shipped NACHA config is valid and has the expected layout."""
    config = load_config(NACHA_CONFIG)

    assert config.name == "nacha_entry"
    assert config.record_length == 94
    assert config.filter is not None
    assert config.filter.field == "record_type"
    assert config.filter.equals == "6"
    assert [rule.name for rule in config.duplicate_rules] == [
        "exact_line", "trace_number", "same_payment", "near_amount",
    ]


def test_nacha_sensitive_fields() -> None:
    """Account and name are marked sensitive, so they get masked later."""
    config = load_config(NACHA_CONFIG)

    sensitive = [spec.name for spec in config.fields if spec.sensitive]
    assert sensitive == ["account", "name"]


def test_nacha_field_positions() -> None:
    """Spot-check positions against the NACHA spec (1-based, inclusive)."""
    fields = load_config(NACHA_CONFIG).field_by_name()

    assert (fields["account"].start, fields["account"].end) == (13, 29)
    assert (fields["amount"].start, fields["amount"].end) == (30, 39)
    assert (fields["trace_number"].start, fields["trace_number"].end) == (80, 94)


def test_nacha_near_amount_tolerance() -> None:
    """near_amount allows up to 500 cents of difference."""
    rules = {rule.name: rule for rule in load_config(NACHA_CONFIG).duplicate_rules}

    tolerance = rules["near_amount"].tolerance
    assert tolerance is not None
    assert tolerance.field == "amount"
    assert tolerance.max_diff == 500


def test_minimal_config_is_valid() -> None:
    """The test helper's config passes, so the failures below are real."""
    config = parse_config(minimal_config())
    assert isinstance(config, LayoutConfig)


def test_defaults_apply() -> None:
    """normalize defaults to none, sensitive to False, description to None."""
    kind = parse_config(minimal_config()).field_by_name()["kind"]

    assert kind.normalize == "none"
    assert kind.sensitive is False
    assert kind.description is None


def test_filter_is_optional() -> None:
    """Without a filter, every record is checked."""
    data = minimal_config()
    del data["filter"]

    assert parse_config(data).filter is None


def test_descriptions_are_allowed() -> None:
    """The config, fields and rules can each have a description."""
    data = minimal_config()
    data["description"] = "A tiny test layout."
    data["fields"][0]["description"] = "Record kind."
    data["duplicate_rules"][0]["description"] = "Same line twice."

    config = parse_config(data)
    assert config.description == "A tiny test layout."
    assert config.fields[0].description == "Record kind."
    assert config.duplicate_rules[0].description == "Same line twice."


def test_gaps_between_fields_are_allowed() -> None:
    """Fields don't have to cover every position."""
    data = minimal_config()
    data["fields"][3]["start"] = 17
    data["fields"][3]["length"] = 3  # leaves position 16 and 20 unmapped

    parse_config(data)


def test_numeric_filter_on_int_field() -> None:
    """A filter on an int field compares numbers, so equals is a number."""
    data = minimal_config()
    data["filter"] = {"field": "amount", "equals": 0}

    parse_config(data)


# ---------- shape errors (stage 1, Pydantic) ----------


def test_bad_normalize_value() -> None:
    data = minimal_config()
    data["fields"][1]["normalize"] = "trim"

    text = problems_for(data)
    assert "fields[1] (account).normalize" in text
    assert "'trim'" in text


def test_misspelled_key_is_an_error() -> None:
    """A typo like 'normalise' must not be silently ignored."""
    data = minimal_config()
    data["fields"][1]["normalise"] = "strip"

    assert "fields[1] (account).normalise: unknown key" in problems_for(data)


def test_missing_required_key() -> None:
    data = minimal_config()
    del data["fields"][2]["start"]

    assert "fields[2] (amount).start: is required" in problems_for(data)


def test_wrong_type() -> None:
    data = minimal_config()
    data["record_length"] = "ninety-four"

    assert "record_length" in problems_for(data)


# ---------- meaning errors (stage 2, our checks) ----------


def test_field_past_record_length() -> None:
    data = minimal_config()
    data["fields"][3]["length"] = 10  # 16 + 10 - 1 = 25, past 20

    assert "field 'ref' ends at position 25, past record_length 20" in problems_for(data)


def test_field_start_below_one() -> None:
    """Positions are 1-based, so start 0 is a mistake (often a 0-based habit)."""
    data = minimal_config()
    data["fields"][0]["start"] = 0

    assert "field 'kind': start must be at least 1, got 0" in problems_for(data)


def test_field_length_below_one() -> None:
    data = minimal_config()
    data["fields"][0]["length"] = 0

    assert "field 'kind': length must be at least 1, got 0" in problems_for(data)


def test_record_length_below_one() -> None:
    data = minimal_config()
    data["record_length"] = 0

    assert "record_length must be at least 1" in problems_for(data)


def test_overlapping_fields() -> None:
    data = minimal_config()
    data["fields"][2]["start"] = 9  # amount now covers 9-14, account is 2-9

    assert "fields 'account' (2-9) and 'amount' (9-14) overlap" in problems_for(data)


def test_duplicate_field_names() -> None:
    data = minimal_config()
    data["fields"][3]["name"] = "kind"

    assert "duplicate field name 'kind'" in problems_for(data)


def test_reserved_field_name() -> None:
    data = minimal_config()
    data["fields"][3]["name"] = "_line"

    assert "names starting with '_' are reserved" in problems_for(data)


def test_no_fields() -> None:
    data = minimal_config()
    data["fields"] = []

    assert "config has no fields" in problems_for(data)


def test_unknown_field_in_rule() -> None:
    data = minimal_config()
    data["duplicate_rules"][1]["keys"] = ["acount", "amount"]

    assert "rule 'same' uses unknown field 'acount'" in problems_for(data)


def test_duplicate_rule_names() -> None:
    data = minimal_config()
    data["duplicate_rules"][1]["name"] = "exact"

    assert "duplicate rule name 'exact'" in problems_for(data)


def test_rule_without_keys() -> None:
    data = minimal_config()
    data["duplicate_rules"][1]["keys"] = []

    assert "rule 'same' has no keys" in problems_for(data)


def test_rule_repeats_a_key() -> None:
    data = minimal_config()
    data["duplicate_rules"][1]["keys"] = ["account", "account"]

    assert "rule 'same' lists key 'account' more than once" in problems_for(data)


def test_no_rules() -> None:
    data = minimal_config()
    data["duplicate_rules"] = []

    assert "config has no duplicate_rules" in problems_for(data)


def test_unknown_field_in_tolerance() -> None:
    data = minimal_config()
    data["duplicate_rules"][2]["tolerance"]["field"] = "amt"

    assert "rule 'near' tolerance uses unknown field 'amt'" in problems_for(data)


def test_tolerance_needs_int_field() -> None:
    data = minimal_config()
    data["duplicate_rules"][2]["tolerance"]["field"] = "ref"

    assert "field 'ref' must have normalize: int" in problems_for(data)


def test_tolerance_field_cannot_be_a_key() -> None:
    data = minimal_config()
    data["duplicate_rules"][2]["keys"] = ["account", "amount"]

    assert "field 'amount' can't also be one of the keys" in problems_for(data)


def test_tolerance_max_diff_must_be_positive() -> None:
    data = minimal_config()
    data["duplicate_rules"][2]["tolerance"]["max_diff"] = 0

    assert "max_diff must be at least 1, got 0" in problems_for(data)


def test_unknown_field_in_filter() -> None:
    data = minimal_config()
    data["filter"]["field"] = "record_type"

    assert "filter uses unknown field 'record_type'" in problems_for(data)


def test_filter_number_on_text_field() -> None:
    """equals: 6 (a number) on a text field would never match, so it's an error."""
    data = minimal_config()
    data["filter"]["equals"] = 6

    assert "put equals in quotes" in problems_for(data)


def test_filter_text_on_int_field() -> None:
    data = minimal_config()
    data["filter"] = {"field": "amount", "equals": "0"}

    assert "equals must be a number" in problems_for(data)


def test_all_problems_reported_together() -> None:
    """Several mistakes in one config are all reported in one error."""
    data = minimal_config()
    data["fields"][3]["length"] = 10
    data["duplicate_rules"][1]["keys"] = ["acount"]
    data["filter"]["field"] = "nope"

    with pytest.raises(ConfigError) as caught:
        parse_config(data)
    assert len(caught.value.problems) == 3


# ---------- loading files ----------


def test_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"

    with pytest.raises(ConfigError) as caught:
        load_config(missing)
    assert "config file not found" in str(caught.value)
    assert str(missing) in str(caught.value)


def test_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("name: [unclosed\n", encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert "invalid YAML" in str(caught.value)


def test_top_level_must_be_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert "must be a mapping" in str(caught.value)


def test_error_names_the_file(tmp_path: Path) -> None:
    """Validation errors from a file start with the file path."""
    path = tmp_path / "bad.yaml"
    path.write_text("name: x\nrecord_length: 10\nfields: []\nduplicate_rules: []\n", encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert str(caught.value).startswith(str(path))
