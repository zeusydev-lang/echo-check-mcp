"""Score the engine and the summary against the generator's answer keys.

Detection is scored in pairs of lines. A group of 3 lines is 3 pairs.
For each rule:
  TP     a reported pair the answer key expects for this rule.
  FP     a reported pair the answer key doesn't list at all.
  cross  a reported pair the key lists for another rule. For example, an exact
         copy is also caught by same_payment. That's correct behavior, so it
         is neither a false positive nor a miss. It's counted, not scored.
  FN     an expected pair this rule didn't report.

The summary is checked in code: every number it uses must appear in the data
Claude was given. An optional LLM judge scores clarity.
"""

import json
import os
import re
from itertools import combinations
from typing import Any

import anthropic
from pydantic import BaseModel, Field

from echo_check.engine import CheckResult, Group
from echo_check.summary import Summary

# Which rule should catch each planted duplicate type.
EXPECTED_RULE = {
    "exact_line": "exact_line",
    "trace_collision": "trace_number",
    "same_payment": "same_payment",
    "near_amount": "near_amount",
    "resent_batch": "same_payment",
}

# Rules that compare exact values. These must score 100%.
EXACT_RULES = ("exact_line", "trace_number", "same_payment")

Pair = tuple[int, int]


# ---------- detection ----------


class RuleScore(BaseModel):
    """Detection counts and scores for one rule."""

    rule: str
    tp: int
    fp: int
    fn: int
    cross: int
    precision: float
    recall: float


def make_pair(first: int, second: int) -> Pair:
    """A pair always has the lower line number first."""
    return (min(first, second), max(first, second))


def pairs_from_groups(groups: list[Group]) -> set[Pair]:
    """Every pair of lines inside every group."""
    pairs: set[Pair] = set()
    for group in groups:
        for first, second in combinations(group.lines, 2):
            pairs.add(make_pair(first, second))
    return pairs


def expected_pairs(answer_key: dict[str, Any]) -> dict[str, set[Pair]]:
    """The pairs each rule should report, according to the answer key."""
    expected: dict[str, set[Pair]] = {rule: set() for rule in set(EXPECTED_RULE.values())}
    for planted in answer_key["planted"]:
        rule = EXPECTED_RULE[planted["type"]]
        if planted["type"] == "resent_batch":
            for original, copy in planted["pairs"]:
                expected[rule].add(make_pair(original, copy))
        else:
            for duplicate in planted["duplicate_lines"]:
                expected[rule].add(make_pair(planted["original_line"], duplicate))
    return expected


def ratio(part: int, whole: int) -> float:
    """part / whole, or 1.0 when there is nothing to count."""
    if whole == 0:
        return 1.0
    return part / whole


def score_rules(result: CheckResult, answer_key: dict[str, Any]) -> list[RuleScore]:
    """Precision and recall for every rule in the result."""
    expected = expected_pairs(answer_key)
    all_expected = set().union(*expected.values())
    scores = []
    for rule in result.rules:
        reported = pairs_from_groups(rule.groups)
        wanted = expected.get(rule.name, set())
        tp = len(reported & wanted)
        cross = len((reported & all_expected) - wanted)
        fp = len(reported - all_expected)
        fn = len(wanted - reported)
        scores.append(
            RuleScore(
                rule=rule.name, tp=tp, fp=fp, fn=fn, cross=cross,
                precision=ratio(tp, tp + fp), recall=ratio(tp, tp + fn),
            )
        )
    return scores


def add_scores(scores: list[RuleScore]) -> list[RuleScore]:
    """Total the counts per rule (for example, over several files)."""
    totals: dict[str, dict[str, int]] = {}
    for score in scores:
        total = totals.setdefault(score.rule, {"tp": 0, "fp": 0, "fn": 0, "cross": 0})
        for name in total:
            total[name] += getattr(score, name)
    return [
        RuleScore(
            rule=rule, **counts,
            precision=ratio(counts["tp"], counts["tp"] + counts["fp"]),
            recall=ratio(counts["tp"], counts["tp"] + counts["fn"]),
        )
        for rule, counts in totals.items()
    ]


def exact_rules_perfect(scores: list[RuleScore]) -> bool:
    """True if every exact rule has 100% precision and recall."""
    return all(
        score.precision == 1.0 and score.recall == 1.0
        for score in scores
        if score.rule in EXACT_RULES
    )


# ---------- summary: the number check ----------


def numbers_in_text(text: str) -> list[str]:
    """Every run of digits in the text, with leading zeros removed."""
    return [str(int(match)) for match in re.findall(r"\d+", text)]


def allowed_numbers(summary_input: dict[str, Any]) -> set[str]:
    """Every number anywhere in the data Claude was given."""
    return set(numbers_in_text(json.dumps(summary_input)))


def check_summary_numbers(summary: Summary, summary_input: dict[str, Any]) -> list[str]:
    """Numbers in the summary that aren't in the data. Empty means it passed."""
    allowed = allowed_numbers(summary_input)
    text = " ".join(
        [summary.headline, summary.likely_cause, summary.suggested_action, *summary.affected_batches]
    )
    unknown = [number for number in numbers_in_text(text) if number not in allowed]
    return sorted(set(unknown), key=int)


# ---------- summary: the optional LLM judge ----------

JUDGE_MODEL_VAR = "ECHO_CHECK_JUDGE_MODEL"
DEFAULT_JUDGE_MODEL = "claude-opus-5-5"

JUDGE_PROMPT = """\
You grade short summaries of duplicate-payment reports. You get the data the
writer saw (inside <results>) and the summary it wrote (inside <summary>).

Grade:
- clarity: 1 (confusing) to 5 (an operations person gets it in one read).
- actionable: true if the suggested action is specific enough to act on.
- names_resent_batch: true if the summary says a whole batch was sent again
  and names the right batches. If the data shows no resent batch, true only
  if the summary doesn't claim one.
- notes: one or two sentences on the biggest problem, or "none".
"""


class JudgeVerdict(BaseModel):
    """The judge's grades for one summary."""

    clarity: int = Field(ge=1, le=5)
    actionable: bool
    names_resent_batch: bool
    notes: str


def judge_model_name() -> str:
    """The judge model from ECHO_CHECK_JUDGE_MODEL, or the default."""
    return os.environ.get(JUDGE_MODEL_VAR) or DEFAULT_JUDGE_MODEL


def judge_summary(
    summary: Summary, summary_input: dict[str, Any], client: anthropic.Anthropic
) -> JudgeVerdict:
    """Ask a judge model to grade one summary."""
    content = (
        f"<results>\n{json.dumps(summary_input, indent=1)}\n</results>\n\n"
        f"<summary>\n{summary.model_dump_json(indent=1)}\n</summary>"
    )
    response = client.messages.parse(
        model=judge_model_name(),
        max_tokens=4000,
        output_config={"effort": "low"},
        system=JUDGE_PROMPT,
        messages=[{"role": "user", "content": content}],
        output_format=JudgeVerdict,
    )
    if response.parsed_output is None:
        raise ValueError(f"The judge returned no verdict (stop reason: {response.stop_reason}).")
    return response.parsed_output
