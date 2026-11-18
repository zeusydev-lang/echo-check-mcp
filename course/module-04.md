# Module 4: Serve it to an AI assistant

## Goal

By the end of this module, Claude (or any MCP client) can list your configs,
read a layout and run a duplicate check, all on your machine, with sensitive
values masked.

## The concept

**MCP is a plug for tools.** An MCP server lists a few tools, each with a name,
a description and typed inputs. The assistant reads that list and decides when
to call them. Our server has three:

| Tool | What it does |
| --- | --- |
| `list_configs()` | Lists the configs in `configs/`. |
| `describe_layout(config_name)` | Shows fields, positions, filter and rules. |
| `check_duplicates(file_path, config_name, max_groups=20)` | Runs the engine. |

**stdio means local.** The client starts the server as a child process and
talks to it through stdin and stdout. Nothing listens on the network, and the
files are read on your machine.

**A server is a door, so check who walks through.** The assistant chooses the
file path, so the server decides what it may read:

- Only folders listed in `ECHO_CHECK_ALLOWED_DIRS` (separated by `;` on
  Windows). If it's not set, every file is refused.
- The path is fully resolved first, so `data\..\secrets.txt` can't sneak out.
- A config is chosen by **name** (`nacha`), never by path.

**Small answers.** A big file can produce thousands of groups, which would
flood the assistant's context. Each rule returns its first `max_groups`
groups plus `total_groups` and `truncated`, so the counts are always complete.

**Always masked.** The MCP tools have no "unmasked" switch. The assistant
sees `****1409`, never a full account number or name.

**A note on names.** Older tutorials say `FastMCP`. In version 2 of the
official Python SDK it's called `MCPServer`
(`from mcp.server.mcpserver import MCPServer`). It works the same way.

## Code walkthrough

All the code is in `src/echo_check/server.py`.

1. **`server = MCPServer(...)`** creates the server, with `instructions` that
   tell the assistant which tool to call first.
2. **`@server.tool()`** turns a normal function into a tool. The type hints
   become the input schema, and the docstring becomes the description.
3. **Return types are Pydantic models** (`CappedResult`, `ConfigInfo`,
   `LayoutConfig`), so the client gets structured JSON with a schema.
4. **`allowed_dirs` and `resolve_allowed_file`** are the access check.
5. **`find_config_path`** only accepts names that are in the configs folder.
6. **`cap_result`** keeps results small but keeps the totals.
7. **`ToolError`** turns a problem into an error the assistant can read,
   for example "not inside an allowed folder".
8. **Tests** use `mcp.Client(server)`, which talks to the server in memory,
   with no process to start.

Try it with the MCP Inspector (needs Node.js):

```powershell
$env:ECHO_CHECK_ALLOWED_DIRS = "C:\zeusy\echo-check-mcp\data"
npx @modelcontextprotocol/inspector uv --directory C:\zeusy\echo-check-mcp run echo-check-mcp
```

Then add it to Claude Desktop. Edit `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "echo-check": {
      "command": "uv",
      "args": ["--directory", "C:\\zeusy\\echo-check-mcp", "run", "echo-check-mcp"],
      "env": {"ECHO_CHECK_ALLOWED_DIRS": "C:\\zeusy\\echo-check-mcp\\data"}
    }
  }
}
```

Restart Claude Desktop and ask: *"Check data\sample.ach for duplicates with the
nacha config."*

## Exercise

1. In the Inspector, call `check_duplicates` with `max_groups` set to 1. Which
   rules say `truncated: true`?
2. Ask for `C:\Windows\win.ini`. What error comes back? Now try
   `data\..\pyproject.toml`.
3. Copy `configs/nacha.yaml` to `configs/nacha_strict.yaml` and remove the
   `near_amount` rule. Call `list_configs` again. Did you need to restart?

## What can go wrong

- **Printing to stdout.** stdout carries the protocol. A stray `print()` in
  the server breaks the connection. Log to stderr instead.
- **Backslashes in JSON.** Windows paths in `claude_desktop_config.json` need
  `\\`. A single `\` makes the file invalid and the server silently doesn't load.
- **Allowed folder not set.** Every check fails with a message naming
  `ECHO_CHECK_ALLOWED_DIRS`. That's on purpose: closed by default.
- **Paths are relative to the server.** A relative path is resolved from
  the server's working folder, not yours. Use full paths.
- **Huge results.** Without the cap, a million-line file could return
  thousands of groups and use up the assistant's context.
