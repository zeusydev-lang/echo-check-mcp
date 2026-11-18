# echo-check-mcp

Find duplicate records in fixed-width files. The file layout and the duplicate
rules live in a YAML config, not in code. The tool runs as a command (`dupcheck`)
and as an MCP server.

This is the running example for the course *Build a Config-Driven MCP Server*.

> **Work in progress.** The duplicate check, the `dupcheck` command, the MCP
> server, the LLM summary and the evals work. The docs site is next. See `CLAUDE.md`.

## Try the sample data

```powershell
uv sync
uv run python generator/generate_nacha.py --out data/sample --seed 42
uv run pytest
```

All data in this project is synthetic.

## MCP server

```powershell
$env:ECHO_CHECK_ALLOWED_DIRS = "C:\path\to\echo-check-mcp\data"
uv run echo-check-mcp
```

Tools: `list_configs`, `describe_layout`, `check_duplicates`. The server only
reads files under the folders in `ECHO_CHECK_ALLOWED_DIRS` (`;`-separated on
Windows), and results are always masked. Setup for the MCP Inspector and
Claude Desktop is in [course/module-04.md](course/module-04.md).

## Summary

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
uv run dupcheck data/sample.ach --config configs/nacha.yaml --summary
```

Claude writes a short summary (`headline`, `likely_cause`, `affected_batches`,
`suggested_action`) from counts, the per-batch breakdown and masked samples only.
The model is `ECHO_CHECK_MODEL` (default `claude-opus-5-5`). Over MCP, use the
`summarize_results` tool. See [course/module-05.md](course/module-05.md).

## Evals

```powershell
uv run python evals/run_evals.py                              # detection, no API key
uv run --env-file .env python evals/run_evals.py --judge      # + summary checks (API calls)
```

Generates seeded files, scores each rule's precision and recall against the
answer keys, and checks that summaries only use numbers from the data. Exits
with 1 if an exact rule drops below 100%. See [course/module-06.md](course/module-06.md).

## License

MIT. See [LICENSE](LICENSE).
