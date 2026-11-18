"""The echo-check MCP server: duplicate checks as tools for an AI assistant.

Run it over stdio:
  uv run echo-check-mcp

Environment variables:
  ECHO_CHECK_ALLOWED_DIRS  folders the server may read files from, separated
                           by ";" on Windows and ":" elsewhere. Required:
                           when it's unset, every file is refused.
  ECHO_CHECK_CONFIG_DIR    folder with the YAML configs (default: configs/).

Results are always masked. The assistant sees counts, line numbers and the
last few characters of sensitive values, never full account numbers or names.
"""

import os
from pathlib import Path
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from echo_check.config import ConfigError, LayoutConfig, load_config
from echo_check.engine import BadLine, CheckResult, Group, Stats, check_file

ALLOWED_DIRS_VAR = "ECHO_CHECK_ALLOWED_DIRS"
CONFIG_DIR_VAR = "ECHO_CHECK_CONFIG_DIR"
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"
DEFAULT_MAX_GROUPS = 20
MAX_GROUPS_LIMIT = 100

server = MCPServer(
    name="echo-check",
    instructions=(
        "Finds duplicate records in fixed-width files such as NACHA (ACH) files. "
        "Call list_configs to see the layouts, describe_layout to see fields and "
        "rules, then check_duplicates on a file. Sensitive values are masked."
    ),
)


# ---------- result models (what the assistant receives) ----------


class ConfigInfo(BaseModel):
    """One config the server can use."""

    config_name: str
    layout_name: str | None
    description: str | None
    problem: str | None = None


class CappedRule(BaseModel):
    """One rule's groups, cut to a maximum count."""

    name: str
    description: str | None
    total_groups: int
    truncated: bool
    groups: list[Group]


class CappedResult(BaseModel):
    """A check result small enough to hand to an assistant."""

    file: str
    config: str
    total_lines: int
    records_checked: int
    bad_line_count: int
    bad_lines_truncated: bool
    bad_lines: list[BadLine]
    rules: list[CappedRule]
    stats: Stats


# ---------- which files may be read ----------


def allowed_dirs() -> list[Path]:
    """Read the allowed folders from ECHO_CHECK_ALLOWED_DIRS."""
    raw = os.environ.get(ALLOWED_DIRS_VAR, "")
    parts = [part.strip() for part in raw.split(os.pathsep)]
    return [Path(part).resolve() for part in parts if part]


def resolve_allowed_file(file_path: str, dirs: list[Path]) -> Path:
    """Return the full path of `file_path` if it's inside an allowed folder.

    The path is resolved first, so "..", relative parts and symlinks can't
    be used to step outside the allowed folders.
    """
    if not dirs:
        raise ToolError(
            f"No folders are allowed. Set {ALLOWED_DIRS_VAR} to the folders "
            "this server may read."
        )
    path = Path(file_path).expanduser().resolve()
    if not any(path.is_relative_to(folder) for folder in dirs):
        raise ToolError(f"{file_path}: not inside an allowed folder ({ALLOWED_DIRS_VAR}).")
    if not path.is_file():
        raise ToolError(f"{file_path}: file not found.")
    return path


# ---------- which configs exist ----------


def config_dir() -> Path:
    """The folder that holds the YAML configs."""
    return Path(os.environ.get(CONFIG_DIR_VAR) or DEFAULT_CONFIG_DIR)


def list_config_names() -> list[str]:
    """Config names are the YAML file names without ".yaml"."""
    return sorted(path.stem for path in config_dir().glob("*.yaml"))


def find_config_path(config_name: str) -> Path:
    """Turn a config name into its file. Only listed names are accepted."""
    names = list_config_names()
    if config_name not in names:
        raise ToolError(f"Unknown config {config_name!r}. Available: {', '.join(names)}.")
    return config_dir() / f"{config_name}.yaml"


def load_named_config(config_name: str) -> LayoutConfig:
    """Load a config by name, turning config problems into tool errors."""
    try:
        return load_config(find_config_path(config_name))
    except ConfigError as error:
        raise ToolError(str(error)) from error


# ---------- keeping results small ----------


def cap_result(result: CheckResult, max_groups: int) -> CappedResult:
    """Keep the first `max_groups` groups per rule (and bad lines), plus the totals."""
    rules = []
    for rule in result.rules:
        capped_rule = CappedRule(
            name=rule.name,
            description=rule.description,
            total_groups=len(rule.groups),
            truncated=len(rule.groups) > max_groups,
            groups=rule.groups[:max_groups],
        )
        rules.append(capped_rule)
    return CappedResult(
        file=result.file,
        config=result.config,
        total_lines=result.total_lines,
        records_checked=result.records_checked,
        bad_line_count=len(result.bad_lines),
        bad_lines_truncated=len(result.bad_lines) > max_groups,
        bad_lines=result.bad_lines[:max_groups],
        rules=rules,
        stats=result.stats,
    )


# ---------- the tools ----------


@server.tool()
def list_configs() -> list[ConfigInfo]:
    """List the layout configs this server can check files with."""
    infos = []
    for name in list_config_names():
        try:
            config = load_config(config_dir() / f"{name}.yaml")
        except ConfigError as error:
            infos.append(
                ConfigInfo(config_name=name, layout_name=None, description=None, problem=str(error))
            )
            continue
        infos.append(
            ConfigInfo(config_name=name, layout_name=config.name, description=config.description)
        )
    return infos


@server.tool()
def describe_layout(config_name: str) -> LayoutConfig:
    """Show a config's record layout, filter and duplicate rules.

    Positions are 1-based. Fields marked sensitive are masked in results.
    """
    return load_named_config(config_name)


@server.tool()
def check_duplicates(
    file_path: str,
    config_name: str,
    max_groups: Annotated[int, Field(ge=1, le=MAX_GROUPS_LIMIT)] = DEFAULT_MAX_GROUPS,
) -> CappedResult:
    """Find duplicate records in a fixed-width file.

    Returns counts for every rule, plus up to `max_groups` example groups per
    rule (line numbers and masked keys). `truncated` says when more exist.
    """
    path = resolve_allowed_file(file_path, allowed_dirs())
    config = load_named_config(config_name)
    result = check_file(path, config, mask=True)
    return cap_result(result, max_groups)


def main() -> None:
    """Start the server over stdio."""
    server.run("stdio")


if __name__ == "__main__":
    main()
