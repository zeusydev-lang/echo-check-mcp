# Build a Config-Driven MCP Server

In this course you build **echo-check**, a tool that finds duplicate records
in fixed-width files such as NACHA (ACH) payment files. You then hand it to
Claude as an MCP server.

The file layout and the duplicate rules live in a YAML config, not in code.
Supporting a new file format means writing a new config, not new code.

## What you'll build

- A config format with clear error messages (Module 2).
- A streaming engine that checks a million lines in seconds (Module 3).
- An MCP server that lets Claude run checks on your machine, safely (Module 4).
- A summary written by Claude from counts and masked samples only (Module 5).
- Evals that prove it works, run on every push (Modules 6 and 7).

## Modules

| | Module | You learn |
| --- | --- | --- |
| 1 | [Set up the project](module-01.md) | uv, the `src/` layout, entry points, seeded test data |
| 2 | [Describe the file in a config](module-02.md) | Pydantic models, validation with plain error messages |
| 3 | [Find the duplicates](module-03.md) | Streaming, dict grouping in O(n), masking |
| 4 | [Serve it to an AI assistant](module-04.md) | MCP tools, stdio, allowed folders, size caps |
| 5 | [Let Claude explain the results](module-05.md) | Structured output, privacy by design, code checks on LLM output |
| 6 | [Prove it works](module-06.md) | Precision and recall, number checks, LLM-as-judge |
| 7 | [Ship it](module-07.md) | README, CI, a docs site, releases |

## Before you start

- Python basics (functions, classes, type hints).
- [uv](https://docs.astral.sh/uv/) installed.
- For Modules 4-5: Claude Desktop or the MCP Inspector, and an Anthropic API key
  for the summary.

The code is at
[github.com/zeusydev-lang/echo-check-mcp](https://github.com/zeusydev-lang/echo-check-mcp).
All data in the course is synthetic.
