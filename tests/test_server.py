"""Tests for the MCP server: file access, config names, size caps and the tools."""

import json
import os
from pathlib import Path

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from echo_check.config import load_config
from echo_check.engine import check_file
from echo_check.server import (
    ALLOWED_DIRS_VAR,
    allowed_dirs,
    cap_result,
    find_config_path,
    resolve_allowed_file,
    server,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SAMPLE = DATA_DIR / "sample.ach"
NACHA_CONFIG = PROJECT_ROOT / "configs" / "nacha.yaml"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def allow_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOWED_DIRS_VAR, str(DATA_DIR))


# ---------- which files may be read ----------


def test_unset_variable_allows_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOWED_DIRS_VAR, raising=False)

    with pytest.raises(ToolError, match=ALLOWED_DIRS_VAR):
        resolve_allowed_file(str(SAMPLE), allowed_dirs())


def test_several_folders_are_split(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ALLOWED_DIRS_VAR, f"{DATA_DIR}{os.pathsep}{tmp_path}")

    assert allowed_dirs() == [DATA_DIR.resolve(), tmp_path.resolve()]


def test_file_inside_an_allowed_folder() -> None:
    assert resolve_allowed_file(str(SAMPLE), [DATA_DIR]) == SAMPLE.resolve()


def test_file_outside_is_refused() -> None:
    with pytest.raises(ToolError, match="not inside an allowed folder"):
        resolve_allowed_file(str(NACHA_CONFIG), [DATA_DIR])


def test_dot_dot_cannot_escape() -> None:
    sneaky = str(DATA_DIR / ".." / "configs" / "nacha.yaml")

    with pytest.raises(ToolError, match="not inside an allowed folder"):
        resolve_allowed_file(sneaky, [DATA_DIR])


def test_missing_file_inside_allowed_folder() -> None:
    with pytest.raises(ToolError, match="file not found"):
        resolve_allowed_file(str(DATA_DIR / "nope.ach"), [DATA_DIR])


# ---------- config names ----------


def test_known_config_name() -> None:
    assert find_config_path("nacha").name == "nacha.yaml"


@pytest.mark.parametrize("name", ["missing", "../configs/nacha", "nacha.yaml", str(NACHA_CONFIG)])
def test_only_listed_config_names_are_accepted(name: str) -> None:
    with pytest.raises(ToolError, match="Unknown config"):
        find_config_path(name)


# ---------- size cap ----------


def test_cap_keeps_totals_and_marks_truncation() -> None:
    result = check_file(SAMPLE, load_config(NACHA_CONFIG))

    capped = cap_result(result, max_groups=1)

    for full_rule, capped_rule in zip(result.rules, capped.rules):
        assert capped_rule.total_groups == len(full_rule.groups)
        assert len(capped_rule.groups) == min(1, len(full_rule.groups))
        assert capped_rule.truncated == (len(full_rule.groups) > 1)
    assert capped.stats == result.stats


# ---------- the tools, through a real MCP client ----------


@pytest.mark.anyio
async def test_lists_the_three_tools() -> None:
    async with Client(server) as client:
        tools = await client.list_tools()

    assert sorted(tool.name for tool in tools.tools) == [
        "check_duplicates", "describe_layout", "list_configs",
    ]


@pytest.mark.anyio
async def test_list_configs() -> None:
    async with Client(server) as client:
        result = await client.call_tool("list_configs", {})

    [info] = result.structured_content["result"]
    assert info["config_name"] == "nacha"
    assert info["layout_name"] == "nacha_entry"


@pytest.mark.anyio
async def test_describe_layout() -> None:
    async with Client(server) as client:
        result = await client.call_tool("describe_layout", {"config_name": "nacha"})

    layout = result.structured_content
    assert layout["record_length"] == 94
    sensitive = [field["name"] for field in layout["fields"] if field["sensitive"]]
    assert sensitive == ["account", "name"]


@pytest.mark.anyio
async def test_check_duplicates_finds_every_rule(allow_data_dir: None) -> None:
    arguments = {"file_path": str(SAMPLE), "config_name": "nacha"}
    async with Client(server) as client:
        result = await client.call_tool("check_duplicates", arguments)

    assert not result.is_error
    rules = {rule["name"]: rule for rule in result.structured_content["rules"]}
    assert list(rules) == ["exact_line", "trace_number", "same_payment", "near_amount"]
    assert all(rule["total_groups"] > 0 for rule in rules.values())


@pytest.mark.anyio
async def test_disallowed_file_is_an_error(allow_data_dir: None) -> None:
    arguments = {"file_path": str(NACHA_CONFIG), "config_name": "nacha"}
    async with Client(server) as client:
        result = await client.call_tool("check_duplicates", arguments)

    assert result.is_error
    assert "not inside an allowed folder" in result.content[0].text


@pytest.mark.anyio
async def test_max_groups_is_limited(allow_data_dir: None) -> None:
    arguments = {"file_path": str(SAMPLE), "config_name": "nacha", "max_groups": 1000}
    async with Client(server) as client:
        result = await client.call_tool("check_duplicates", arguments)

    assert result.is_error


@pytest.mark.anyio
async def test_no_sensitive_value_leaves_the_server(allow_data_dir: None) -> None:
    config = load_config(NACHA_CONFIG)
    sensitive_values = set()
    for line in SAMPLE.read_text(encoding="ascii").splitlines():
        if not line.startswith("6"):
            continue
        for spec in config.fields:
            value = line[spec.start - 1 : spec.end].strip()
            if spec.sensitive and len(value) > 4:
                sensitive_values.add(value)
    assert sensitive_values  # the test must actually check something

    arguments = {"file_path": str(SAMPLE), "config_name": "nacha", "max_groups": 100}
    async with Client(server) as client:
        result = await client.call_tool("check_duplicates", arguments)

    output = json.dumps(result.structured_content) + result.content[0].text
    leaked = [value for value in sensitive_values if value in output]
    assert leaked == []
