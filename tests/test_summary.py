"""Tests for the LLM summary. A fake client stands in for the Anthropic API."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest
from mcp import Client

from echo_check import summary
from echo_check.cli import EXIT_INPUT_ERROR, EXIT_OK, EXIT_SUMMARY_ERROR, main
from echo_check.config import load_config
from echo_check.engine import CheckResult, check_file
from echo_check.server import ALLOWED_DIRS_VAR, server
from echo_check.summary import (
    MODEL_VAR,
    Summary,
    SummaryError,
    build_request,
    build_summary_input,
    summarize,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SAMPLE = DATA_DIR / "sample.ach"
NACHA_CONFIG = PROJECT_ROOT / "configs" / "nacha.yaml"

GOOD_SUMMARY = Summary(
    headline="Batch 4 repeats every payment in batch 3.",
    likely_cause="Batch 3 was sent twice.",
    affected_batches=["3", "4"],
    suggested_action="Hold batch 4 and confirm with the sender.",
)


class FakeMessages:
    """Records each request and returns a canned response."""

    def __init__(self, parsed: Summary | None, stop_reason: str) -> None:
        self.parsed = parsed
        self.stop_reason = stop_reason
        self.requests: list[dict[str, Any]] = []

    def parse(self, **request: Any) -> SimpleNamespace:
        self.requests.append(request)
        return SimpleNamespace(parsed_output=self.parsed, stop_reason=self.stop_reason)


class FakeClient:
    """Just enough of anthropic.Anthropic for summarize()."""

    def __init__(self, parsed: Summary | None = GOOD_SUMMARY, stop_reason: str = "end_turn") -> None:
        self.messages = FakeMessages(parsed, stop_reason)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def masked_result() -> CheckResult:
    return check_file(SAMPLE, load_config(NACHA_CONFIG), mask=True)


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    """Every summarize() call in this test uses the same fake client."""
    client = FakeClient()
    monkeypatch.setattr(summary, "make_client", lambda: client)
    return client


def request_text(request: dict[str, Any]) -> str:
    """Everything in a request that goes over the wire, as one string."""
    return request["system"] + json.dumps(request["messages"])


# ---------- what Claude sees ----------


def test_input_has_counts_and_batch_links(masked_result: CheckResult) -> None:
    summary_input = build_summary_input(masked_result)

    rules = {rule["name"]: rule for rule in summary_input["rules"]}
    assert rules["same_payment"]["groups"] == 10
    assert len(rules["same_payment"]["sample_groups"]) == 3
    assert summary_input["batches"]["groups_by_batches"]["same_payment"]["3-4"] == 8


def test_unmasked_result_is_refused() -> None:
    unmasked = check_file(SAMPLE, load_config(NACHA_CONFIG), mask=False)

    with pytest.raises(SummaryError, match="unmasked"):
        build_summary_input(unmasked)


def test_no_sensitive_value_reaches_the_prompt(masked_result: CheckResult) -> None:
    """Every account number and name in the sample file stays out of the request."""
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

    client = FakeClient()
    summarize(masked_result, client=client)

    sent = request_text(client.messages.requests[0])
    leaked = [value for value in sensitive_values if value in sent]
    assert leaked == []


# ---------- the request ----------


def test_model_comes_from_the_environment(
    masked_result: CheckResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MODEL_VAR, "claude-sonnet-5-5")
    assert build_request(masked_result)["model"] == "claude-sonnet-5-5"


def test_default_model(masked_result: CheckResult, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MODEL_VAR, raising=False)
    assert build_request(masked_result)["model"] == "claude-opus-5-5"


def test_request_asks_for_structured_output(masked_result: CheckResult) -> None:
    request = build_request(masked_result)

    assert request["output_format"] is Summary
    assert "<results>" in request["messages"][0]["content"]
    assert "Do not invent" in request["system"]


# ---------- checking the answer ----------


def test_good_summary_passes_through(masked_result: CheckResult) -> None:
    written = summarize(masked_result, client=FakeClient())

    assert written.summary == GOOD_SUMMARY
    assert written.warnings == []


def test_unknown_batches_are_removed(masked_result: CheckResult) -> None:
    invented = GOOD_SUMMARY.model_copy(update={"affected_batches": ["3", "4", "9"]})

    written = summarize(masked_result, client=FakeClient(parsed=invented))

    assert written.summary.affected_batches == ["3", "4"]
    assert written.warnings == ["Removed batches not found in the results: 9"]


def test_refusal_raises(masked_result: CheckResult) -> None:
    with pytest.raises(SummaryError, match="declined"):
        summarize(masked_result, client=FakeClient(parsed=None, stop_reason="refusal"))


def test_missing_output_raises(masked_result: CheckResult) -> None:
    with pytest.raises(SummaryError, match="max_tokens"):
        summarize(masked_result, client=FakeClient(parsed=None, stop_reason="max_tokens"))


# ---------- CLI ----------


def test_cli_summary(fake_client: FakeClient, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([str(SAMPLE), "--config", str(NACHA_CONFIG), "--summary"])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == EXIT_OK
    assert output["summary"]["summary"]["affected_batches"] == ["3", "4"]
    assert output["batches"]["groups_by_batches"]["same_payment"]["3-4"] == 8


def test_cli_summary_with_out(
    fake_client: FakeClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "results.json"

    exit_code = main([str(SAMPLE), "--config", str(NACHA_CONFIG), "--summary", "--out", str(out)])

    assert exit_code == EXIT_OK
    assert "Summary: Batch 4 repeats" in capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["model"]


def test_cli_summary_and_unmasked_conflict(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([str(SAMPLE), "--config", str(NACHA_CONFIG), "--summary", "--unmasked"])

    assert exit_code == EXIT_INPUT_ERROR
    assert "--unmasked" in capsys.readouterr().err


def test_cli_missing_key_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Uses the real SDK with every credential removed. It fails before any network call.

    The base URL points nowhere, so even a saved login on this machine can't
    turn this test into a real (billed) API call.
    """
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)

    def offline_client() -> anthropic.Anthropic:
        return anthropic.Anthropic(max_retries=0, base_url="http://127.0.0.1:9")

    monkeypatch.setattr(summary, "make_client", offline_client)

    exit_code = main([str(SAMPLE), "--config", str(NACHA_CONFIG), "--summary"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_INPUT_ERROR
    assert "ANTHROPIC_API_KEY" in captured.err
    assert json.loads(captured.out)["rules"]  # the check itself still printed


def test_cli_other_failure_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeClient(parsed=None, stop_reason="refusal")
    monkeypatch.setattr(summary, "make_client", lambda: client)

    exit_code = main([str(SAMPLE), "--config", str(NACHA_CONFIG), "--summary"])

    assert exit_code == EXIT_SUMMARY_ERROR
    assert "declined" in capsys.readouterr().err


# ---------- MCP tool ----------


@pytest.mark.anyio
async def test_mcp_summarize_results(
    fake_client: FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ALLOWED_DIRS_VAR, str(DATA_DIR))
    arguments = {"file_path": str(SAMPLE), "config_name": "nacha"}

    async with Client(server) as client:
        result = await client.call_tool("summarize_results", arguments)

    assert not result.is_error
    assert result.structured_content["summary"]["affected_batches"] == ["3", "4"]


@pytest.mark.anyio
async def test_mcp_summarize_needs_an_allowed_file(
    fake_client: FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ALLOWED_DIRS_VAR, str(DATA_DIR))
    arguments = {"file_path": str(NACHA_CONFIG), "config_name": "nacha"}

    async with Client(server) as client:
        result = await client.call_tool("summarize_results", arguments)

    assert result.is_error
    assert fake_client.messages.requests == []  # nothing was sent to the API
