# Module 2: Describe the file in a config

## Goal

By the end of this module you can describe a fixed-width file and its
duplicate rules in YAML, and get clear errors when the config is wrong.

## The concept

**Layout in config, not in code.** A fixed-width file has no separators. Each
field is a slice of the line, such as positions 13 to 29. If those numbers live
in the code, every new file format means new code. If they live in a config,
a new format is just a new YAML file.

**A config has three parts:**

1. `fields`: where each field starts, how long it is, and how to clean it
   (`normalize: strip` trims spaces, `normalize: int` turns `0000393786` into `393786`).
2. `filter`: which records to check. In NACHA, only lines starting with `6`.
3. `duplicate_rules`: which fields must match for two records to be duplicates.

**Positions are 1-based.** `start: 1` is the first character, the same as in
the NACHA spec. Python counts from 0, so the engine converts the positions later. The config
stays readable next to the spec.

**Validation in two stages.**

1. *Shape.* Pydantic checks that keys exist, types are right, values are
   allowed and there are no unknown keys.
2. *Meaning.* Our own checks look at how the parts fit: fields fit inside the
   record, fields don't overlap, and rules only use fields that exist.

Stage 2 only runs once stage 1 passes. Within each stage, *every* problem is
reported at once.

## Code walkthrough

All the code is in `src/echo_check/config.py`.

1. **Models.** `FieldSpec`, `RecordFilter`, `Tolerance`, `DuplicateRule` and
   `LayoutConfig` are small Pydantic classes. Each one matches a part of the YAML.
2. **`StrictModel`.** All models inherit `extra="forbid"`. A typo like
   `normalise: strip` becomes an error instead of being silently ignored.
3. **`Normalize = Literal["none", "strip", "int"]`.** Pydantic rejects any other value.
4. **`FieldSpec.end`.** A property that works out the last position, so no
   other code repeats `start + length - 1`.
5. **`check_meaning`.** A Pydantic `model_validator` that runs after stage 1.
   It calls `find_problems`, which calls one small `check_...` function per idea.
   Each check returns a list of sentences.
6. **`load_config` and `parse_config`.** These read the file and run validation.
   Every failure becomes one `ConfigError` with a list of plain problems.
7. **`describe_error` and `format_location`.** These turn Pydantic's raw errors into
   lines like `fields[2] (account).normalize: Input should be 'none', 'strip' or 'int'`.

Try it:

```powershell
uv run pytest tests/test_config.py -v
uv run python -c "from echo_check.config import load_config; print(load_config('configs/nacha.yaml').field_by_name()['amount'])"
```

## Exercise

Copy `configs/nacha.yaml` to `data/broken.yaml` (ignored by Git). Then make
three mistakes at once:

- change `amount` to `start: 29`,
- rename `trace_number` in the rules to `trace`,
- write `equals: 6` without quotes.

Load it. Do you get all three problems in one error? Now add a fourth mistake,
`normalise: strip` on any field. Why do the other three problems disappear?

## What can go wrong

- **Off-by-one positions.** If you copy positions from code that counts from 0,
  every field is shifted by one. `start: 0` is caught, but a shift of one inside
  the record isn't. Check one real line by hand.
- **Overlapping fields.** Usually a wrong length. The error shows both ranges
  so you can see where they collide.
- **`equals: 6` vs `equals: "6"`.** YAML reads `6` as a number. A text field
  never equals a number, so the filter would skip every record. The config
  check catches this.
- **Silent typos.** Without `extra="forbid"`, a misspelled key does nothing,
  and you only notice when results look wrong.
- **Fixing errors in rounds.** Shape errors hide meaning errors. Fix the first
  round, then load again.
