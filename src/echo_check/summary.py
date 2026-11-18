"""A short, structured summary of a duplicate check, written by Claude.

Privacy: Claude only sees counts, the batch breakdown and a few sample groups
from a *masked* result. Full account numbers and names never leave the machine.

Accuracy: the prompt forbids inventing numbers, and the code removes any
batch the summary names that isn't in the data.

Environment variables:
  ANTHROPIC_API_KEY  your API key (read by the Anthropic SDK).
  ECHO_CHECK_MODEL   the Claude model to use (default: claude-opus-5-5).
"""

import json
import os
from typing import Any

import anthropic
from pydantic import BaseModel, Field

from echo_check.engine import CheckResult

MODEL_VAR = "ECHO_CHECK_MODEL"
DEFAULT_MODEL = "claude-opus-5-5"
EFFORT = "medium"
MAX_TOKENS = 16000
DEFAULT_SAMPLES = 3

SYSTEM_PROMPT = """\
You review duplicate-record reports for fixed-width payment files (such as
NACHA/ACH). You get the results of a duplicate check as JSON inside <results>
tags: counts per rule, a per-batch breakdown and a few masked sample groups.

How to read the batch breakdown: in groups_by_batches, "3" means a group whose
lines are all in batch 3, and "3-4" means a group with lines in batch 3 and
batch 4. Many groups linking the same two batches usually means a whole batch
was sent twice.

Rules:
- Use only numbers that appear in the results. Do not invent, estimate or
  calculate new numbers.
- Only name batches that appear in the results.
- If the data can't explain a cause, say so plainly.
- Values with **** are masked on purpose. Don't guess the hidden parts.
- Write for an operations person: short, plain sentences.
"""


class SummaryError(Exception):
    """The summary couldn't be made (for example, a refused request)."""


class MissingCredentialsError(SummaryError):
    """No API key (or login) was found."""


class Summary(BaseModel):
    """What Claude returns. The fields match the course spec."""

    headline: str = Field(description="One sentence: the most important finding.")
    likely_cause: str = Field(description="The most likely cause, or 'unclear'.")
    affected_batches: list[str] = Field(description="Batch ids from the results only.")
    suggested_action: str = Field(description="What the operator should do next.")


class SummaryResult(BaseModel):
    """The summary plus anything our own checks noticed."""

    summary: Summary
    model: str
    warnings: list[str]


# ---------- what Claude gets to see ----------


def build_summary_input(result: CheckResult, max_samples: int = DEFAULT_SAMPLES) -> dict[str, Any]:
    """Counts, batch breakdown and a few masked sample groups. Nothing else."""
    if not result.masked:
        raise SummaryError("Refusing to summarize an unmasked result.")
    rules = []
    for rule in result.rules:
        rules.append(
            {
                "name": rule.name,
                "description": rule.description,
                "groups": len(rule.groups),
                "lines_in_groups": result.stats.lines_in_groups_per_rule[rule.name],
                "sample_groups": [group.model_dump() for group in rule.groups[:max_samples]],
            }
        )
    summary_input: dict[str, Any] = {
        "config": result.config,
        "total_lines": result.total_lines,
        "records_checked": result.records_checked,
        "bad_line_count": result.stats.bad_line_count,
        "rules": rules,
    }
    if result.batches is not None:
        summary_input["batches"] = result.batches.model_dump()
    return summary_input


def known_batch_ids(result: CheckResult) -> set[str]:
    """Every batch id in the result, including both sides of "3-4" labels."""
    if result.batches is None:
        return set()
    ids = set(result.batches.records_per_batch)
    for counts in result.batches.groups_by_batches.values():
        for label in counts:
            ids.update(label.split("-"))
    return ids


# ---------- asking Claude ----------


def model_name() -> str:
    """The model from ECHO_CHECK_MODEL, or the default."""
    return os.environ.get(MODEL_VAR) or DEFAULT_MODEL


def build_request(result: CheckResult) -> dict[str, Any]:
    """Everything sent to the API, in one dict (so tests can inspect it)."""
    results_json = json.dumps(build_summary_input(result), indent=1)
    return {
        "model": model_name(),
        "max_tokens": MAX_TOKENS,
        "output_config": {"effort": EFFORT},
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": f"<results>\n{results_json}\n</results>\n\nSummarize these results.",
            }
        ],
        "output_format": Summary,
    }


def make_client() -> anthropic.Anthropic:
    """The Anthropic client. Tests replace this function with a fake."""
    return anthropic.Anthropic()


def summarize(result: CheckResult, client: anthropic.Anthropic | None = None) -> SummaryResult:
    """Ask Claude for a summary, then check it against the data."""
    if client is None:
        client = make_client()
    request = build_request(result)
    try:
        response = client.messages.parse(**request)
    except TypeError as error:
        # With no key or login, the SDK raises a TypeError when the request is sent.
        if "authentication method" in str(error):
            raise MissingCredentialsError("Summary needs an API key. Set ANTHROPIC_API_KEY.") from error
        raise

    if response.stop_reason == "refusal":
        raise SummaryError("Claude declined to summarize these results.")
    if response.parsed_output is None:
        raise SummaryError(f"No summary came back (stop reason: {response.stop_reason}).")

    return check_summary(response.parsed_output, result, request["model"])


def check_summary(summary: Summary, result: CheckResult, model: str) -> SummaryResult:
    """Remove batches the data doesn't know about, and say that we did."""
    known = known_batch_ids(result)
    kept = [batch for batch in summary.affected_batches if batch in known]
    removed = [batch for batch in summary.affected_batches if batch not in known]
    warnings = []
    if removed:
        warnings.append(f"Removed batches not found in the results: {', '.join(removed)}")
    checked = summary.model_copy(update={"affected_batches": kept})
    return SummaryResult(summary=checked, model=model, warnings=warnings)
