# echo-check-mcp

Find duplicate records in fixed-width files. The file layout and the duplicate
rules live in a YAML config, not in code. The tool runs as a command (`dupcheck`)
and as an MCP server.

This is the running example for the course *Build a Config-Driven MCP Server*.

> **Work in progress.** The duplicate check, the `dupcheck` command and the MCP
> server work. The LLM summary, evals and docs site are next. See `CLAUDE.md`.

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

## License

MIT. See [LICENSE](LICENSE).
