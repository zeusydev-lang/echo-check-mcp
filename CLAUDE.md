# Claude Code prompt: echo-check-mcp

You are helping me build **echo-check-mcp**, a public GitHub project and the running example for a course called *Build a Config-Driven MCP Server*. The tool finds duplicate records in fixed-width files. The file layout and the duplicate rules live in a YAML config, not in code.

## Context

- Windows, PowerShell, VS Code. The project was created with `uv init` and has a `.venv` made by `uv venv`.
- Use `uv add` for dependencies and `uv run` for everything. Never call `python` or `pip` directly.
- `generator/generate_nacha.py` already exists. It writes synthetic NACHA (ACH) files with planted duplicates (`data/sample.ach`) plus an answer key (`data/sample.answers.json`). Read it first. Keep its behavior and its CLI flags.
- Running example: NACHA files. Every record is 94 characters, and entry detail records start with `6`.

## Hard rules

1. **Synthetic data only.** Never add real, company or healthcare claims data. Don't commit generated data except `data/sample.*` (seed 42). Add everything else under `data/` to `.gitignore`.
2. **Local first.** Files never leave the machine. The LLM gets counts, patterns and masked samples only, never full account numbers or names.
3. **Small, reviewable steps.** Do one phase at a time. At the end of each phase, stop, show me what changed and how to run it, and wait for my OK before starting the next phase.
4. **Tests with every phase** (pytest). All tests must pass before you stop.
5. **Explaining code to me:** use simple sentences and break the explanation into small numbered units, stating the total (for example "Unit 2 of 6"). No long paragraphs.
6. Keep code beginner-readable, since this is teaching material: clear names, short functions, type hints, docstrings. No clever one-liners.

## Target layout

```
echo-check-mcp/
├── src/echo_check/
│   ├── config.py        # Pydantic models + YAML loading
│   ├── engine.py        # parse, build keys, group duplicates
│   ├── cli.py           # `dupcheck` command
│   ├── server.py        # MCP server (FastMCP, stdio)
│   └── summary.py       # LLM summary
├── configs/nacha.yaml
├── generator/generate_nacha.py
├── evals/               # scorecard scripts
├── tests/
├── course/              # module-01.md … module-07.md
├── data/sample.ach, data/sample.answers.json
└── README.md
```

## Config format (start here, improve if needed, and tell me why)

```yaml
name: nacha_entry
record_length: 94
filter: {field: record_type, equals: "6"}
fields:
  - {name: record_type,   start: 1,  length: 1}
  - {name: rdfi,          start: 4,  length: 8}
  - {name: account,       start: 13, length: 17, normalize: strip}
  - {name: amount,        start: 30, length: 10, normalize: int}
  - {name: individual_id, start: 40, length: 15, normalize: strip}
  - {name: name,          start: 55, length: 22, normalize: strip, sensitive: true}
  - {name: trace_number,  start: 80, length: 15}
duplicate_rules:
  - {name: exact_line,   keys: [_line]}
  - {name: trace_number, keys: [trace_number]}
  - {name: same_payment, keys: [account, amount, individual_id]}
  - {name: near_amount,  keys: [account, individual_id],
     tolerance: {field: amount, max_diff: 500}}   # 0 < diff <= 500 cents
```

Positions are 1-based, as in the NACHA spec. `_line` means the whole raw line. Mark `account` as sensitive too, so it gets masked.

## Phases

### Phase 0: Setup
- Move to the `src/` layout. Add pytest, pydantic, pyyaml. Add a `dupcheck` entry point in `pyproject.toml`.
- Add `.gitignore`, an MIT `LICENSE` and a stub README.
- Done when `uv run pytest` runs (even with zero tests) and the generator still works with `uv run python generator/generate_nacha.py --out data/sample --seed 42`.

### Phase 1: Config (course Module 2)
- Pydantic models for the config, loaded from YAML.
- Validation with clear error messages: fields outside `record_length`, overlapping fields, unknown field names in rules or filter, bad `normalize` values.
- Done when tests cover a valid config and each kind of error.

### Phase 2: Core engine + CLI (course Module 3)
- Stream the file line by line. Slice fields by config, normalize them, apply the filter.
- For each rule, group records by key in a dict of key to line numbers (O(n)). The `near_amount` rule groups by its keys first, then compares amounts inside each group.
- Report bad lines (wrong length) separately. Don't crash on them.
- JSON output: `{file, config, total_lines, records_checked, bad_lines: [...], rules: [{name, groups: [{key, lines: [...]}]}], stats}`.
- CLI: `uv run dupcheck data/sample.ach --config configs/nacha.yaml [--out results.json]`.
- Done when the engine finds every planted duplicate in `data/sample.answers.json` (planted type maps to rule; `resent_batch` maps to `same_payment`), and a generated file of 1M entry lines runs in well under a minute. Report the timing.

### Phase 3: MCP server (course Module 4)
- Use the official Python MCP SDK (FastMCP) over stdio.
- Tools: `check_duplicates(file_path, config_name)`, `list_configs()`, `describe_layout(config_name)`.
- Only allow files under folders listed in an `ECHO_CHECK_ALLOWED_DIRS` environment variable. Return structured results, capped in size (top N groups plus counts).
- Show me how to test it with the MCP Inspector, and the exact `claude_desktop_config.json` entry for Windows.

### Phase 4: LLM summary (course Module 5)
- `summary.py` uses the Anthropic Python SDK. Read the model from `ECHO_CHECK_MODEL` and the key from `ANTHROPIC_API_KEY`.
- Input to the LLM: counts per rule, per-batch breakdown, and masked samples (for example `****0601409`). Never raw sensitive fields.
- The prompt forbids inventing numbers. Output is structured JSON: `headline, likely_cause, affected_batches, suggested_action`.
- It should notice patterns such as a resent batch.
- Available as `dupcheck ... --summary` and as an MCP tool `summarize_results`.
- Tests mock the API and include a test proving that no unmasked sensitive value reaches the prompt.

### Phase 5: Evals (course Module 6)
- `uv run python evals/run_evals.py` generates several seeded files, runs the engine and prints a scorecard.
- Detection: precision and recall per rule against the answer key. A pair the answer key doesn't list counts as a false positive. A pair reported by a rule other than its expected one is not a miss.
- Summary: a code check that every number in the summary exists in the engine stats. Optionally an LLM-as-judge rubric for clarity, behind a `--judge` flag.
- Done when exact rules score 100% precision and recall.

### Phase 6: Shipping (course Module 7)
- README: what it is, a 2-minute quick start, a config guide, the privacy note, and an MCP setup section.
- GitHub Actions running tests and evals (no API key needed; mock or skip the summary).
- MkDocs Material site built from `course/`, deployed to GitHub Pages by an Action.
- Tag `v1.0.0`.

## Course notes

After each phase, draft or update the matching `course/module-0N.md` from what we actually built. Each draft has: goal, the concept explained simply, the code walkthrough, an exercise, and a "what can go wrong" section. Keep it short and plain.

Start with Phase 0. First read the existing files and tell me your plan for the phase in a few lines, then begin.

Work on one phase at a time. Wait for me to say which phase to start