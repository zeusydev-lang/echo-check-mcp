# Module 3: Find the duplicates

## Goal

By the end of this module, `dupcheck` reads a fixed-width file, applies your
config and reports every duplicate group as JSON, fast enough for a million lines.

## The concept

**Stream, don't load.** The engine reads one line at a time. A 90 MB file
never sits in memory as a whole.

**A dict finds duplicates in one pass.** For each rule, build a key from the
rule's fields, for example `(account, amount, individual_id)`. Use it as a dict
key and append the line number:

```
("12345", 39378, "EMP288389") -> [3, 40]
("99881", 12000, "EMP000017") -> [7]
```

Any key with 2 or more lines is a duplicate group. Each line is looked at once,
so the work grows in a straight line with the file size (O(n)). Comparing every
line with every other line would be O(n²): about 500 billion comparisons for
a million lines.

**"Almost equal" needs sorting.** A dict only finds *exact* matches. For
`near_amount`, the engine groups by the exact keys first (account and ID).
Then, inside each small group, it sorts by amount and looks forward only while
the difference is at most `max_diff`. Each near pair is reported as its own group.

**Bad lines are data, not crashes.** A line with the wrong length, or letters in
a number field, goes into `bad_lines` with a reason. The check carries on.

**Masked by default.** Results show `****1409` instead of a full account
number, and a `_line` key has its sensitive positions starred out. Later, these
results go to an LLM, so the safe choice is the default. `--unmasked` is for
looking into a file on your own machine.

## Code walkthrough

All the code is in `src/echo_check/engine.py` and `cli.py`.

1. **`read_lines`** opens the file as ASCII and yields `(line number, text)`.
   It strips `\n` or `\r\n`, so Windows files work too.
2. **`slice_field`** is the one place where 1-based config positions become
   0-based Python slices: `line[start - 1 : end]`.
3. **`normalize`** applies `strip` or `int`. An `int` field accepts digits only.
4. **`check_file`** is the main loop. It checks the length, then the filter, then
   parses the record, then hands it to every `RuleIndex`.
5. **`RuleIndex`** holds one rule's dict. `add` puts a line in its bucket, and
   `find_groups` returns buckets with 2 or more lines.
6. **`find_near_pairs`** is the sorted sliding window for tolerance rules.
7. **`display_key` and `mask_value`** build the masked key you see in the report.
8. **The result models** (`CheckResult`, `RuleResult`, `Group`, `BadLine`, `Stats`)
   are Pydantic classes, so the CLI can turn them into JSON with `model_dump_json`.

Try it:

```powershell
uv run dupcheck data/sample.ach --config configs/nacha.yaml --out data/results.json
uv run python evals/benchmark.py
```

## Exercise

1. Open `data/sample.answers.json` and pick the `near_amount` entry. Find its
   two lines in `data/results.json`. Which other rules list those lines?
2. Edit a copy of `data/sample.ach` (in `data/`, so Git ignores it): delete
   one character from an entry line, and put a letter in another line's amount.
   Run `dupcheck` on it. What do `bad_lines` say?
3. Why can't `near_amount` just use a dict key like the other rules?

## What can go wrong

- **One rule, many hits.** An exact duplicate line also matches `trace_number`
  and `same_payment`. That's correct: each rule answers its own question.
- **Off-by-one slices.** If one field looks shifted by a character, check
  `start` in the config against the spec, not the Python slice.
- **Memory.** Speed is fine (about 7 seconds for a million lines), but holding
  every key for 4 rules takes about 1 GB. For much bigger files you would store
  a short hash of each key instead of the key itself.
- **Leaking data in errors.** A bad-line reason quotes the bad value. If that
  field is sensitive, the reason must be masked too.
- **The generator's ID limit.** It gives each new entry a unique ID out of a
  million, so it can't make more than about a million new entries.
  The benchmark stays at about 960k.
