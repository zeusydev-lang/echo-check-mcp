# echo-check-mcp

[![CI](https://github.com/zeusydev-lang/echo-check-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/zeusydev-lang/echo-check-mcp/actions/workflows/ci.yml)
[![Course](https://img.shields.io/badge/course-online-blue)](https://zeusydev-lang.github.io/echo-check-mcp/)

Find duplicate records in fixed-width files, such as NACHA (ACH) payment files.
The file layout and the duplicate rules live in a YAML config, not in code.
It runs as a command (`dupcheck`) and as an MCP server, so Claude can check
files for you. An optional summary from Claude explains what it found.

This is the running example for the course
[*Build a Config-Driven MCP Server*](https://zeusydev-lang.github.io/echo-check-mcp/).
All data in this project is synthetic.

## Quick start (2 minutes)

You need [uv](https://docs.astral.sh/uv/). Then:

```powershell
git clone https://github.com/zeusydev-lang/echo-check-mcp
cd echo-check-mcp
uv sync
uv run dupcheck data/sample.ach --config configs/nacha.yaml --out data/results.json
```

You'll see something like this:

```
Checked 36 records out of 50 lines in data\sample.ach (config nacha_entry), 0 bad line(s).
  exact_line: 1 group(s), 2 lines
  trace_number: 2 group(s), 4 lines
  same_payment: 10 group(s), 20 lines
  near_amount: 1 group(s), 2 lines
```

`data/results.json` has every duplicate group with line numbers and masked
values, plus a per-batch breakdown. Make more test files with the generator:

```powershell
uv run python generator/generate_nacha.py --out data/big --seed 7 --batches 10 --entries 500
```

## The config

One YAML file describes a file format. [`configs/nacha.yaml`](configs/nacha.yaml)
is the full example:

```yaml
name: nacha_entry
record_length: 94                              # every line must be this long
filter: {field: record_type, equals: "6"}      # only check entry records
batch: {header_starts_with: "5", id_start: 88, id_length: 7}   # optional

fields:                                        # positions are 1-based
  - {name: record_type,   start: 1,  length: 1}
  - {name: account,       start: 13, length: 17, normalize: strip, sensitive: true}
  - {name: amount,        start: 30, length: 10, normalize: int}
  - {name: individual_id, start: 40, length: 15, normalize: strip, sensitive: true}
  # ...

duplicate_rules:
  - {name: exact_line,   keys: [_line]}                    # the whole line
  - {name: same_payment, keys: [account, amount, individual_id]}
  - {name: near_amount,  keys: [account, individual_id],
     tolerance: {field: amount, max_diff: 500}}            # 0 < diff <= 500
```

| Setting | Meaning |
| --- | --- |
| `normalize` | `none` (default), `strip` (trim spaces) or `int` (a whole number). |
| `sensitive` | Masked in every result, for example `****1409`. |
| `keys` | Records with equal values for all keys are duplicates. `_line` is the whole raw line. |
| `tolerance` | Group by `keys`, then match amounts that differ by more than 0 and at most `max_diff`. |
| `filter` | Skip lines that don't match, such as headers. |
| `batch` | Where batch headers are, so results can say which batches are involved. |

Mistakes are reported all at once, in plain sentences, for example
`fields 'account' (13-29) and 'amount' (29-38) overlap`. The details are in
[Module 2](https://zeusydev-lang.github.io/echo-check-mcp/module-02/).

## Privacy

- **Local first.** Files are read on your machine. The MCP server talks over
  stdio and doesn't listen on the network.
- **Masked by default.** Sensitive fields are masked in every result. The MCP
  tools have no way to turn masking off. The `dupcheck` command has
  `--unmasked` for your own local investigation.
- **The LLM sees counts, not data.** The summary gets counts, the per-batch
  breakdown and a few masked sample groups. A test checks that no account
  number, employee ID or name from the sample file reaches the request.
- **Closed by default.** The MCP server only reads files under the folders in
  `ECHO_CHECK_ALLOWED_DIRS`. If the variable isn't set, every file is refused.

## MCP setup

Tools: `list_configs`, `describe_layout`, `check_duplicates` and `summarize_results`.

**Claude Desktop.** Open **Settings → Developer → Edit Config** and add:

```json
{
  "mcpServers": {
    "echo-check": {
      "command": "uv",
      "args": ["--directory", "C:\\path\\to\\echo-check-mcp", "run", "echo-check-mcp"],
      "env": {
        "ECHO_CHECK_ALLOWED_DIRS": "C:\\path\\to\\echo-check-mcp\\data",
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

Fully quit and restart Claude Desktop, then ask *"Check data\sample.ach for
duplicates with the nacha config."* The API key is only needed for
`summarize_results`. In JSON, every `\` in a Windows path must be doubled.

**MCP Inspector** (for testing; needs Node.js):

```powershell
npx @modelcontextprotocol/inspector -e ECHO_CHECK_ALLOWED_DIRS=C:\path\to\echo-check-mcp\data uv --directory C:\path\to\echo-check-mcp run echo-check-mcp
```

More detail is in [Module 4](https://zeusydev-lang.github.io/echo-check-mcp/module-04/).

## Summary from Claude

```powershell
# .env (Git ignores it): ANTHROPIC_API_KEY=sk-ant-...
uv run --env-file .env dupcheck data/sample.ach --config configs/nacha.yaml --summary
```

Claude returns `headline`, `likely_cause`, `affected_batches` and
`suggested_action`, for example *"Batch 4 looks like a resend of batch 3: 8 of
the 10 same-payment duplicate groups link batch 3 and batch 4."* The model is
`ECHO_CHECK_MODEL` (default `claude-opus-5-5`). Batches the data doesn't contain
are removed from the answer.

## Evals

```powershell
uv run python evals/run_evals.py                             # detection, no API key
uv run --env-file .env python evals/run_evals.py --judge     # + summary checks (API calls)
```

It generates seeded files and scores each rule's precision and recall against
the answer keys. It also checks that every number in a summary comes from the
data. The exit code is 1 if an exact rule drops below 100%. CI runs the
detection evals on every push.

## Development

```powershell
uv run pytest                          # all tests, no API calls
uvx ruff check src tests evals         # lint
uv run --group docs mkdocs serve       # course site at http://127.0.0.1:8000
```

Quit Claude Desktop and the MCP Inspector before `uv add` or `uv sync` on
Windows. A running server locks `echo-check-mcp.exe`.

## License

MIT. See [LICENSE](LICENSE).
