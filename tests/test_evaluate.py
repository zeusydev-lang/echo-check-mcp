"""Tests for the evals: pair scoring, the summary number check and the judge."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from echo_check.config import load_config
from echo_check.engine import CheckResult, Group, RuleResult, Stats, check_file
from echo_check.evaluate import (
    JUDGE_MODEL_VAR,
    JudgeVerdict,
    check_summary_numbers,
    exact_rules_perfect,
    expected_pairs,
    judge_summary,
    numbers_in_text,
    pairs_from_groups,
    score_rules,
)
from echo_check.summary import Summary, build_summary_input

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = PROJECT_ROOT / "data" / "sample.ach"
SAMPLE_KEY = json.loads((PROJECT_ROOT / "data" / "sample.answers.json").read_text(encoding="utf-8"))
NACHA_CONFIG = PROJECT_ROOT / "configs" / "nacha.yaml"


def load_run_evals() -> ModuleType:
    """Import evals/run_evals.py (evals/ is a folder of scripts, not a package)."""
    path = PROJECT_ROOT / "evals" / "run_evals.py"
    spec = importlib.util.spec_from_file_location("run_evals", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def result_with(groups_per_rule: dict[str, list[list[int]]]) -> CheckResult:
    """A hand-made CheckResult with only the groups we care about."""
    rules = [
        RuleResult(name=name, description=None, groups=[Group(key={}, lines=lines) for lines in groups])
        for name, groups in groups_per_rule.items()
    ]
    stats = Stats(
        records_skipped=0, bad_line_count=0, groups_per_rule={},
        lines_in_groups_per_rule={}, elapsed_seconds=0.0,
    )
    return CheckResult(
        file="x", config="x", masked=True, total_lines=0, records_checked=0,
        bad_lines=[], rules=rules, stats=stats,
    )


# ---------- pairs ----------


def test_a_group_of_three_is_three_pairs() -> None:
    assert pairs_from_groups([Group(key={}, lines=[7, 3, 5])]) == {(3, 5), (3, 7), (5, 7)}


def test_expected_pairs_from_the_sample_key() -> None:
    expected = expected_pairs(SAMPLE_KEY)

    assert expected["exact_line"] == {(19, 21)}
    assert expected["trace_number"] == {(15, 20)}
    assert expected["near_amount"] == {(9, 24)}
    # 1 planted same_payment + 8 pairs from the resent batch
    assert len(expected["same_payment"]) == 9
    assert (27, 37) in expected["same_payment"]


# ---------- scoring ----------


def test_true_positive_false_positive_cross_and_miss() -> None:
    key = {
        "planted": [
            {"type": "exact_line", "original_line": 1, "duplicate_lines": [2]},
            {"type": "trace_collision", "original_line": 5, "duplicate_lines": [6]},
        ]
    }
    result = result_with({
        "exact_line": [[1, 2]],                  # TP
        "trace_number": [[1, 2], [8, 9]],        # cross (1-2) + FP (8-9); misses 5-6
    })

    exact, trace = score_rules(result, key)

    assert (exact.tp, exact.fp, exact.fn, exact.cross) == (1, 0, 0, 0)
    assert (trace.tp, trace.fp, trace.fn, trace.cross) == (0, 1, 1, 1)
    assert trace.precision == 0.0
    assert trace.recall == 0.0
    assert not exact_rules_perfect([exact, trace])


def test_nothing_reported_and_nothing_expected_scores_one() -> None:
    [score] = score_rules(result_with({"near_amount": []}), {"planted": []})
    assert (score.precision, score.recall) == (1.0, 1.0)


def test_sample_file_scores_perfectly() -> None:
    result = check_file(SAMPLE, load_config(NACHA_CONFIG))

    scores = score_rules(result, SAMPLE_KEY)

    assert all(score.precision == 1.0 and score.recall == 1.0 for score in scores)
    assert exact_rules_perfect(scores)


def test_seeded_files_score_100_percent_on_exact_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The Phase 5 bar: generated files, exact rules at 100% precision and recall."""
    run_evals = load_run_evals()
    monkeypatch.setattr(run_evals, "EVALS_DIR", tmp_path)

    exit_code = run_evals.main(["--seeds", "1", "2", "3", "--json"])

    scorecard = json.loads((tmp_path / "scorecard.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert scorecard["exact_rules_perfect"] is True
    assert "Exact rules at 100%: yes" in capsys.readouterr().out


# ---------- the summary number check ----------

# The live summary from 2026-11-17, written before individual_id was masked.
LIVE_SUMMARY = Summary(
    headline=(
        "Batch 4 looks like a resend of batch 3: 8 of the 10 same-payment duplicate groups "
        "link batch 3 and batch 4, which each have 8 records."
    ),
    likely_cause="Batch 3 was most likely sent a second time as batch 4.",
    affected_batches=["3", "4", "2", "1"],
    suggested_action=(
        "Hold the file. In batch 2, remove the exact duplicate (line 21 repeats line 19). "
        "Check lines 15 and 20, which share trace number 091000010000010. "
        "Review the near-amount pair for EMP225772 (lines 9 and 24)."
    ),
)


@pytest.fixture
def sample_input() -> dict[str, Any]:
    return build_summary_input(check_file(SAMPLE, load_config(NACHA_CONFIG), mask=True))


def test_numbers_in_text_ignore_leading_zeros() -> None:
    assert numbers_in_text("trace 0091 and batch 3, ****5772") == ["91", "3", "5772"]


def test_summary_using_only_real_numbers_passes(sample_input: dict[str, Any]) -> None:
    masked_id = LIVE_SUMMARY.suggested_action.replace("EMP225772", "****5772")
    summary = LIVE_SUMMARY.model_copy(update={"suggested_action": masked_id})

    assert check_summary_numbers(summary, sample_input) == []


def test_unmasked_id_is_now_an_unknown_number(sample_input: dict[str, Any]) -> None:
    """Claude only sees ****5772 now, so quoting EMP225772 would be made up."""
    assert check_summary_numbers(LIVE_SUMMARY, sample_input) == ["225772"]


def test_invented_count_fails(sample_input: dict[str, Any]) -> None:
    invented = Summary(
        headline="12 duplicate groups were found.",
        likely_cause="unclear",
        affected_batches=["3"],
        suggested_action="Review them.",
    )

    assert check_summary_numbers(invented, sample_input) == ["12"]


# ---------- the judge and summary evals (fake client, no API) ----------


class FakeMessages:
    """Records requests and returns whichever canned output fits the request."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def parse(self, **request: Any) -> SimpleNamespace:
        self.requests.append(request)
        if request["output_format"] is JudgeVerdict:
            parsed: Any = JudgeVerdict(
                clarity=5, actionable=True, names_resent_batch=True, notes="none"
            )
        else:
            parsed = LIVE_SUMMARY.model_copy(
                update={"suggested_action": "Hold batch 4 and confirm with the sender."}
            )
        return SimpleNamespace(parsed_output=parsed, stop_reason="end_turn")


class FakeClient:
    def __init__(self) -> None:
        self.messages = FakeMessages()


def test_judge_uses_the_judge_model(
    sample_input: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(JUDGE_MODEL_VAR, "claude-sonnet-5-5")
    client = FakeClient()

    verdict = judge_summary(LIVE_SUMMARY, sample_input, client)

    assert verdict.clarity == 5
    assert client.messages.requests[0]["model"] == "claude-sonnet-5-5"
    assert "<summary>" in client.messages.requests[0]["messages"][0]["content"]


def test_eval_summary_with_judge() -> None:
    run_evals = load_run_evals()
    result = check_file(SAMPLE, load_config(NACHA_CONFIG), mask=True)
    client = FakeClient()

    outcome = run_evals.eval_summary(result, client, judge=True)

    assert outcome["ok"] is True
    assert outcome["unknown_numbers"] == []
    assert outcome["judge"]["names_resent_batch"] is True
    assert len(client.messages.requests) == 2  # one summary, one judge
