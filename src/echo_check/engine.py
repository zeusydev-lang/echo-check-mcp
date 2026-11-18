"""Find duplicate records in a fixed-width file.

How a check works:
  1. Read the file one line at a time (streaming), so big files fit in memory.
  2. Lines with the wrong length go to `bad_lines`. The check never crashes on them.
  3. The filter skips lines we don't care about (in NACHA: headers and controls).
  4. Each remaining line becomes a record: a dict of field name to normalized value.
  5. For each rule, records are grouped by their key in a dict (one pass, O(n)).
  6. Groups with 2 or more lines are duplicates.

Rules with a `tolerance` work a little differently. Records are grouped by
their keys first. Then, inside each group, the amounts are sorted and every
pair with 0 < difference <= max_diff is reported as its own group of 2 lines.

Sensitive values are masked in the result by default.
"""

import bisect
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel

from echo_check.config import (
    LINE_KEY,
    BatchSpec,
    DuplicateRule,
    FieldSpec,
    LayoutConfig,
)

NO_BATCH = "none"  # batch name for records that come before any batch header

# A normalized field value: text, or a whole number for `normalize: int`.
Value = str | int

MASK_CHAR = "*"
VISIBLE_TAIL = 4  # how many characters of a sensitive value stay visible


# ---------- result models ----------


class BadLine(BaseModel):
    """A line that couldn't be checked, and why."""

    line: int
    reason: str


class Group(BaseModel):
    """Lines that count as duplicates of each other under one rule."""

    key: dict[str, Value]
    lines: list[int]


class RuleResult(BaseModel):
    """All duplicate groups found by one rule."""

    name: str
    description: str | None
    groups: list[Group]


class Stats(BaseModel):
    """Counts that summarize a check."""

    records_skipped: int
    bad_line_count: int
    groups_per_rule: dict[str, int]
    lines_in_groups_per_rule: dict[str, int]
    elapsed_seconds: float


class BatchBreakdown(BaseModel):
    """Where the duplicates sit, batch by batch (only when the config has `batch`).

    `groups_by_batches` counts each rule's groups by the batches their lines
    are in: "3" means inside batch 3, "3-4" means one line in 3 and one in 4.
    """

    records_per_batch: dict[str, int]
    groups_by_batches: dict[str, dict[str, int]]


class CheckResult(BaseModel):
    """The full result of checking one file against one config."""

    file: str
    config: str
    masked: bool
    total_lines: int
    records_checked: int
    bad_lines: list[BadLine]
    rules: list[RuleResult]
    stats: Stats
    batches: BatchBreakdown | None = None


# ---------- reading and slicing ----------


def read_lines(path: Path) -> Iterator[tuple[int, str]]:
    """Yield (line number, text) for each line, without the line ending.

    Line numbers start at 1. Both "\\n" and "\\r\\n" endings work.
    The file is read as ASCII. Any other byte becomes one placeholder
    character, so one byte is still one position.
    """
    with path.open("r", encoding="ascii", errors="replace", newline="") as file:
        for number, text in enumerate(file, start=1):
            yield number, text.rstrip("\r\n")


def slice_field(line: str, spec: FieldSpec) -> str:
    """Cut one field out of a line. Config positions are 1-based, Python's are 0-based."""
    return line[spec.start - 1 : spec.end]


def normalize(raw: str, mode: str) -> Value:
    """Clean a raw field value. Raises ValueError if an `int` field isn't a number."""
    if mode == "strip":
        return raw.strip()
    if mode == "int":
        text = raw.strip()
        if not text.isdigit():
            raise ValueError(f"not a whole number: {raw!r}")
        return int(text)
    return raw


# ---------- masking ----------


def mask_value(value: Value) -> str:
    """Hide all but the last few characters, like "****1409".

    The prefix is always the same length, so it doesn't reveal how long the
    value is. Short values are hidden completely.
    """
    text = str(value)
    if len(text) <= VISIBLE_TAIL:
        return MASK_CHAR * len(text)
    return MASK_CHAR * 4 + text[-VISIBLE_TAIL:]


def mask_line(line: str, fields: list[FieldSpec]) -> str:
    """Star out every sensitive field in a raw line, keeping the line's length."""
    for spec in fields:
        if spec.sensitive:
            stars = MASK_CHAR * spec.length
            line = line[: spec.start - 1] + stars + line[spec.end :]
    return line


# ---------- turning lines into records ----------


def length_problem(line: str, record_length: int) -> str | None:
    """Return a reason if the line has the wrong length, else None."""
    if len(line) == record_length:
        return None
    return f"length {len(line)}, expected {record_length}"


def passes_filter(line: str, config: LayoutConfig) -> bool:
    """True if the line should be checked. Without a filter, every line is."""
    if config.filter is None:
        return True
    spec = config.field_by_name()[config.filter.field]
    try:
        value = normalize(slice_field(line, spec), spec.normalize)
    except ValueError:
        return False  # not a number, so it can't equal the filter's number
    return value == config.filter.equals


def parse_record(line: str, fields: list[FieldSpec], mask: bool) -> dict[str, Value]:
    """Slice and normalize every field. Raises ValueError with a readable reason."""
    record: dict[str, Value] = {}
    for spec in fields:
        raw = slice_field(line, spec)
        try:
            record[spec.name] = normalize(raw, spec.normalize)
        except ValueError:
            shown = mask_value(raw) if mask and spec.sensitive else raw
            raise ValueError(f"field '{spec.name}' is not a whole number: '{shown}'") from None
    return record


def build_key(rule: DuplicateRule, line: str, record: dict[str, Value]) -> tuple[Value, ...]:
    """Collect the rule's key values for one record. `_line` means the whole line."""
    values: list[Value] = []
    for name in rule.keys:
        if name == LINE_KEY:
            values.append(line)
        else:
            values.append(record[name])
    return tuple(values)


# ---------- grouping ----------


class RuleIndex:
    """Collects line numbers per key for one rule while the file streams by.

    For a plain rule, each key maps to a list of line numbers.
    For a tolerance rule, each key maps to a list of (amount, line number).
    """

    def __init__(self, rule: DuplicateRule) -> None:
        self.rule = rule
        self.buckets: dict[tuple[Value, ...], list] = {}

    def add(self, line_number: int, line: str, record: dict[str, Value]) -> None:
        """Put one record into the bucket for its key."""
        key = build_key(self.rule, line, record)
        if self.rule.tolerance is None:
            entry = line_number
        else:
            entry = (record[self.rule.tolerance.field], line_number)
        self.buckets.setdefault(key, []).append(entry)

    def find_groups(self) -> list[tuple[tuple[Value, ...], list[int]]]:
        """Return (key, line numbers) for every duplicate group, sorted by first line."""
        groups = []
        for key, entries in self.buckets.items():
            if len(entries) < 2:
                continue
            if self.rule.tolerance is None:
                groups.append((key, entries))
            else:
                for pair in find_near_pairs(entries, self.rule.tolerance.max_diff):
                    groups.append((key, list(pair)))
        groups.sort(key=lambda group: group[1])
        return groups


def find_near_pairs(entries: list[tuple[int, int]], max_diff: int) -> list[tuple[int, int]]:
    """Find pairs of lines whose amounts differ by more than 0 and at most max_diff.

    `entries` holds (amount, line number). After sorting by amount, we only
    need to look forward until the difference gets too big (a sliding window).
    Equal amounts are skipped: those are exact matches, not near ones.
    """
    ordered = sorted(entries)
    pairs = []
    for index, (amount, line) in enumerate(ordered):
        for other_amount, other_line in ordered[index + 1 :]:
            if other_amount - amount > max_diff:
                break
            if other_amount != amount:
                pairs.append((min(line, other_line), max(line, other_line)))
    pairs.sort()
    return pairs


def display_key(
    rule: DuplicateRule, key: tuple[Value, ...], config: LayoutConfig, mask: bool
) -> dict[str, Value]:
    """Name each key value for the report, masking sensitive ones if asked."""
    fields = config.field_by_name()
    shown: dict[str, Value] = {}
    for name, value in zip(rule.keys, key):
        if name == LINE_KEY and mask:
            shown[name] = mask_line(str(value), config.fields)
        elif name != LINE_KEY and mask and fields[name].sensitive:
            shown[name] = mask_value(value)
        else:
            shown[name] = value
    return shown


def build_rule_result(index: RuleIndex, config: LayoutConfig, mask: bool) -> RuleResult:
    """Turn one rule's buckets into a RuleResult for the report."""
    groups = []
    for key, lines in index.find_groups():
        groups.append(Group(key=display_key(index.rule, key, config, mask), lines=lines))
    return RuleResult(name=index.rule.name, description=index.rule.description, groups=groups)


# ---------- the whole check ----------


def check_file(path: str | Path, config: LayoutConfig, mask: bool = True) -> CheckResult:
    """Check one file for duplicates under every rule in the config."""
    started = time.perf_counter()
    path = Path(path)
    indexes = [RuleIndex(rule) for rule in config.duplicate_rules]
    batches = BatchTracker(config.batch) if config.batch is not None else None
    bad_lines: list[BadLine] = []
    total_lines = 0
    records_checked = 0
    records_skipped = 0

    for line_number, line in read_lines(path):
        total_lines = line_number
        problem = length_problem(line, config.record_length)
        if problem is not None:
            bad_lines.append(BadLine(line=line_number, reason=problem))
            continue
        if batches is not None:
            batches.see_line(line_number, line)
        if not passes_filter(line, config):
            records_skipped += 1
            continue
        try:
            record = parse_record(line, config.fields, mask)
        except ValueError as error:
            bad_lines.append(BadLine(line=line_number, reason=str(error)))
            continue
        records_checked += 1
        if batches is not None:
            batches.count_record(line_number)
        for index in indexes:
            index.add(line_number, line, record)

    rules = [build_rule_result(index, config, mask) for index in indexes]
    stats = build_stats(rules, records_skipped, len(bad_lines), time.perf_counter() - started)
    return CheckResult(
        batches=batches.breakdown(rules) if batches is not None else None,
        file=str(path),
        config=config.name,
        masked=mask,
        total_lines=total_lines,
        records_checked=records_checked,
        bad_lines=bad_lines,
        rules=rules,
        stats=stats,
    )


# ---------- batches ----------


class BatchTracker:
    """Remembers where each batch starts, so any line can be traced to its batch.

    Only the header line numbers are stored (one per batch), not one entry per
    record, so this stays small even for a million lines.
    """

    def __init__(self, spec: BatchSpec) -> None:
        self.spec = spec
        self.header_lines: list[int] = []
        self.batch_ids: list[str] = []
        self.records_per_batch: Counter[str] = Counter()

    def see_line(self, line_number: int, line: str) -> None:
        """If this line is a batch header, a new batch starts here."""
        if line.startswith(self.spec.header_starts_with):
            raw_id = line[self.spec.id_start - 1 : self.spec.id_end]
            self.header_lines.append(line_number)
            self.batch_ids.append(clean_batch_id(raw_id))

    def count_record(self, line_number: int) -> None:
        """Count one checked record in its batch."""
        self.records_per_batch[self.batch_of(line_number)] += 1

    def batch_of(self, line_number: int) -> str:
        """The batch a line belongs to: the last header at or before it."""
        position = bisect.bisect_right(self.header_lines, line_number) - 1
        if position < 0:
            return NO_BATCH
        return self.batch_ids[position]

    def breakdown(self, rules: list[RuleResult]) -> BatchBreakdown:
        """Count each rule's groups by the batches their lines are in."""
        groups_by_batches: dict[str, dict[str, int]] = {}
        for rule in rules:
            counts: Counter[str] = Counter()
            for group in rule.groups:
                counts[self.batch_label(group.lines)] += 1
            groups_by_batches[rule.name] = dict(sorted(counts.items()))
        return BatchBreakdown(
            records_per_batch=dict(self.records_per_batch),
            groups_by_batches=groups_by_batches,
        )

    def batch_label(self, lines: list[int]) -> str:
        """"3" if all lines are in batch 3, "3-4" if they are in batches 3 and 4."""
        batch_ids = {self.batch_of(line) for line in lines}
        return "-".join(sorted(batch_ids, key=batch_sort_key))


def clean_batch_id(raw_id: str) -> str:
    """Turn "0000003" into "3". Ids that aren't numbers are only stripped."""
    text = raw_id.strip()
    if text.isdigit():
        return str(int(text))
    return text


def batch_sort_key(batch_id: str) -> tuple[int, int, str]:
    """Sort numeric batch ids as numbers (2 before 10), and others after them."""
    if batch_id.isdigit():
        return (0, int(batch_id), "")
    return (1, 0, batch_id)


def build_stats(
    rules: list[RuleResult], records_skipped: int, bad_line_count: int, elapsed: float
) -> Stats:
    """Count groups and the distinct lines involved, per rule."""
    groups_per_rule = {}
    lines_in_groups_per_rule = {}
    for rule in rules:
        groups_per_rule[rule.name] = len(rule.groups)
        lines: set[int] = set()
        for group in rule.groups:
            lines.update(group.lines)
        lines_in_groups_per_rule[rule.name] = len(lines)
    return Stats(
        records_skipped=records_skipped,
        bad_line_count=bad_line_count,
        groups_per_rule=groups_per_rule,
        lines_in_groups_per_rule=lines_in_groups_per_rule,
        elapsed_seconds=round(elapsed, 3),
    )
