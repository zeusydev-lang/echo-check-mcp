# echo-check-mcp

Find duplicate records in fixed-width files. The file layout and the duplicate
rules live in a YAML config, not in code. The tool runs as a command (`dupcheck`)
and as an MCP server.

This is the running example for the course *Build a Config-Driven MCP Server*.

> **Work in progress.** The duplicate check isn't built yet. See `CLAUDE.md` for the roadmap.

## Try the sample data

```powershell
uv sync
uv run python generator/generate_nacha.py --out data/sample --seed 42
uv run pytest
```

All data in this project is synthetic.

## License

MIT. See [LICENSE](LICENSE).
