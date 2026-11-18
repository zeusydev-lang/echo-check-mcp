"""Load and validate a layout config from YAML.

A layout config describes two things:
  1. Where each field sits in a fixed-width record (the "layout").
  2. Which fields make two records count as duplicates (the "rules").

Validation happens in two stages:
  Stage 1. Pydantic checks the shape: required keys, types, allowed values,
           and no unknown keys.
  Stage 2. Our own checks look at the meaning: fields fit the record, fields
           don't overlap, rules only use fields that exist, and so on.

Every problem is turned into a short, plain sentence and raised as one
ConfigError, so you can fix a config in one pass.

Positions are 1-based, as in the NACHA spec: `start: 1` is the first character.
"""

from itertools import combinations
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

# A rule key that means "the whole raw line" instead of one field.
LINE_KEY = "_line"

# Field names starting with this are reserved for special keys like _line.
RESERVED_PREFIX = "_"

Normalize = Literal["none", "strip", "int"]


class ConfigError(Exception):
    """A config could not be loaded. `problems` lists every reason."""

    def __init__(self, source: str, problems: list[str]) -> None:
        self.source = source
        self.problems = problems
        lines = [f"{source}: {len(problems)} problem(s) in config"]
        lines += [f"  - {problem}" for problem in problems]
        super().__init__("\n".join(lines))


# ---------- models ----------


class StrictModel(BaseModel):
    """Base for all config models: unknown keys are errors, values can't change."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FieldSpec(StrictModel):
    """One field in a fixed-width record."""

    name: str
    start: int
    length: int
    normalize: Normalize = "none"
    sensitive: bool = False
    description: str | None = None

    @property
    def end(self) -> int:
        """The last position of this field (1-based, inclusive)."""
        return self.start + self.length - 1


class RecordFilter(StrictModel):
    """Only check records where `field` equals `equals` (after normalizing)."""

    field: str
    equals: str | int


class Tolerance(StrictModel):
    """Treat values of `field` as matching when 0 < difference <= max_diff."""

    field: str
    max_diff: int


class DuplicateRule(StrictModel):
    """Records with the same values for all `keys` are duplicates."""

    name: str
    keys: list[str]
    tolerance: Tolerance | None = None
    description: str | None = None


class BatchSpec(StrictModel):
    """How to spot batch header lines and read the batch id from them.

    Records after a header belong to that batch, until the next header.
    """

    header_starts_with: str
    id_start: int
    id_length: int

    @property
    def id_end(self) -> int:
        """The last position of the batch id (1-based, inclusive)."""
        return self.id_start + self.id_length - 1


class LayoutConfig(StrictModel):
    """A whole config: the record layout plus the duplicate rules."""

    name: str
    record_length: int
    fields: list[FieldSpec]
    duplicate_rules: list[DuplicateRule]
    filter: RecordFilter | None = None
    batch: BatchSpec | None = None
    description: str | None = None

    def field_by_name(self) -> dict[str, FieldSpec]:
        """Return the fields in a dict keyed by field name."""
        return {spec.name: spec for spec in self.fields}

    @model_validator(mode="after")
    def check_meaning(self) -> "LayoutConfig":
        """Stage 2: run every meaning check and report all problems together."""
        problems = find_problems(self)
        if problems:
            raise ValueError("\n".join(problems))
        return self


# ---------- stage 2 checks ----------


def find_problems(config: LayoutConfig) -> list[str]:
    """Run every meaning check and collect the problems."""
    fields = config.field_by_name()
    problems: list[str] = []
    if config.record_length < 1:
        problems.append(f"record_length must be at least 1, got {config.record_length}")
    problems += check_field_names(config.fields)
    problems += check_field_bounds(config.fields, config.record_length)
    problems += check_overlaps(config.fields)
    problems += check_rules(config.duplicate_rules, fields)
    if config.filter is not None:
        problems += check_filter(config.filter, fields)
    if config.batch is not None:
        problems += check_batch(config.batch, config.record_length)
    return problems


def check_batch(batch: BatchSpec, record_length: int) -> list[str]:
    """The header prefix must not be empty, and the batch id must fit the record."""
    problems = []
    if batch.header_starts_with == "":
        problems.append("batch: header_starts_with must not be empty")
    if batch.id_start < 1 or batch.id_length < 1 or batch.id_end > record_length:
        problems.append(
            f"batch: id at {batch.id_start}-{batch.id_end} "
            f"is outside the record (1-{record_length})"
        )
    return problems


def check_field_names(fields: list[FieldSpec]) -> list[str]:
    """Field names must exist, be unique and not use the reserved prefix."""
    if not fields:
        return ["config has no fields"]
    problems = []
    names = [spec.name for spec in fields]
    for name in find_repeats(names):
        problems.append(f"duplicate field name '{name}'")
    for spec in fields:
        if spec.name.startswith(RESERVED_PREFIX):
            problems.append(
                f"field '{spec.name}': names starting with '{RESERVED_PREFIX}' are reserved"
            )
    return problems


def check_field_bounds(fields: list[FieldSpec], record_length: int) -> list[str]:
    """Every field must start at 1 or later and end inside the record."""
    problems = []
    for spec in fields:
        if spec.start < 1:
            problems.append(f"field '{spec.name}': start must be at least 1, got {spec.start}")
        if spec.length < 1:
            problems.append(f"field '{spec.name}': length must be at least 1, got {spec.length}")
        elif spec.end > record_length:
            problems.append(
                f"field '{spec.name}' ends at position {spec.end}, "
                f"past record_length {record_length}"
            )
    return problems


def check_overlaps(fields: list[FieldSpec]) -> list[str]:
    """No two fields may share a position. Gaps between fields are fine."""
    problems = []
    for first, second in combinations(fields, 2):
        if first.length < 1 or second.length < 1:
            continue  # already reported by check_field_bounds
        if first.start <= second.end and second.start <= first.end:
            problems.append(
                f"fields '{first.name}' ({first.start}-{first.end}) and "
                f"'{second.name}' ({second.start}-{second.end}) overlap"
            )
    return problems


def check_rules(rules: list[DuplicateRule], fields: dict[str, FieldSpec]) -> list[str]:
    """Check every duplicate rule against the known fields."""
    if not rules:
        return ["config has no duplicate_rules"]
    problems = []
    names = [rule.name for rule in rules]
    for name in find_repeats(names):
        problems.append(f"duplicate rule name '{name}'")
    for rule in rules:
        problems += check_rule_keys(rule, fields)
        if rule.tolerance is not None:
            problems += check_tolerance(rule, rule.tolerance, fields)
    return problems


def check_rule_keys(rule: DuplicateRule, fields: dict[str, FieldSpec]) -> list[str]:
    """A rule needs at least one key, no repeats, and only known fields (or _line)."""
    if not rule.keys:
        return [f"rule '{rule.name}' has no keys"]
    problems = []
    for key in find_repeats(rule.keys):
        problems.append(f"rule '{rule.name}' lists key '{key}' more than once")
    for key in rule.keys:
        if key != LINE_KEY and key not in fields:
            problems.append(f"rule '{rule.name}' uses unknown field '{key}'")
    return problems


def check_tolerance(
    rule: DuplicateRule, tolerance: Tolerance, fields: dict[str, FieldSpec]
) -> list[str]:
    """A tolerance needs a known numeric field that isn't also a key."""
    problems = []
    label = f"rule '{rule.name}' tolerance"
    spec = fields.get(tolerance.field)
    if spec is None:
        problems.append(f"{label} uses unknown field '{tolerance.field}'")
    elif spec.normalize != "int":
        problems.append(
            f"{label}: field '{tolerance.field}' must have normalize: int "
            f"to compare amounts, got '{spec.normalize}'"
        )
    if tolerance.field in rule.keys:
        problems.append(f"{label}: field '{tolerance.field}' can't also be one of the keys")
    if tolerance.max_diff < 1:
        problems.append(f"{label}: max_diff must be at least 1, got {tolerance.max_diff}")
    return problems


def check_filter(record_filter: RecordFilter, fields: dict[str, FieldSpec]) -> list[str]:
    """The filter must use a known field, and `equals` must match its type.

    The filter compares the *normalized* value. An `int` field gives a number,
    every other field gives text. Comparing text to a number never matches,
    which would silently skip every record, so we catch it here.
    """
    spec = fields.get(record_filter.field)
    if spec is None:
        return [f"filter uses unknown field '{record_filter.field}'"]
    wants_number = spec.normalize == "int"
    is_number = isinstance(record_filter.equals, int)
    if wants_number and not is_number:
        return [f"filter: field '{spec.name}' is normalize: int, so equals must be a number"]
    if is_number and not wants_number:
        return [f"filter: field '{spec.name}' is text, so put equals in quotes, like \"6\""]
    return []


def find_repeats(names: list[str]) -> list[str]:
    """Return each name that appears more than once, in order of the repeat."""
    seen: set[str] = set()
    repeats = []
    for name in names:
        if name in seen:
            repeats.append(name)
        seen.add(name)
    return repeats


# ---------- loading ----------


def load_config(path: str | Path) -> LayoutConfig:
    """Read a YAML config file and return a validated LayoutConfig.

    Raises ConfigError if the file is missing, isn't valid YAML, or fails validation.
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(str(path), ["config file not found"])
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigError(str(path), [f"invalid YAML: {error}"]) from error
    return parse_config(data, source=str(path))


def parse_config(data: Any, source: str = "<config>") -> LayoutConfig:
    """Validate already-loaded data (usually a dict from YAML)."""
    if not isinstance(data, dict):
        raise ConfigError(source, ["the top level of the config must be a mapping of keys"])
    try:
        return LayoutConfig.model_validate(data)
    except ValidationError as error:
        problems = []
        for detail in error.errors():
            problems += describe_error(detail, data)
        raise ConfigError(source, problems) from None


def describe_error(detail: dict[str, Any], data: dict[str, Any]) -> list[str]:
    """Turn one Pydantic error into one or more plain sentences."""
    if detail["type"] == "value_error":
        # Raised by check_meaning: our own sentences, one per line.
        return str(detail["ctx"]["error"]).splitlines()
    where = format_location(detail["loc"], data)
    if detail["type"] == "extra_forbidden":
        return [f"{where}: unknown key (check the spelling)"]
    if detail["type"] == "missing":
        return [f"{where}: is required"]
    return [f"{where}: {detail['msg']} (got {detail['input']!r})"]


def format_location(loc: tuple[Any, ...], data: dict[str, Any]) -> str:
    """Turn a Pydantic location like ('fields', 2, 'normalize') into readable text.

    The result looks like "fields[2] (account).normalize". The name in
    brackets comes from the raw data, so you can find the entry quickly.
    """
    text = ""
    current: Any = data
    for part in loc:
        if isinstance(part, int):
            text += f"[{part}]"
            current = item_at(current, part)
            if isinstance(current, dict) and current.get("name"):
                text += f" ({current['name']})"
        else:
            text += f".{part}" if text else str(part)
            current = current.get(part) if isinstance(current, dict) else None
    return text or "config"


def item_at(items: Any, index: int) -> Any:
    """Return items[index] if items is a list long enough, else None."""
    if isinstance(items, list) and 0 <= index < len(items):
        return items[index]
    return None
