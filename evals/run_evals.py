"""Run the evals: generate seeded files, check them, print a scorecard.

Usage:
  uv run python evals/run_evals.py                          # detection only, no API
  uv run python evals/run_evals.py --seeds 1 2 3
  uv run --env-file .env python evals/run_evals.py --summary  # + number check (1 call per seed)
  uv run --env-file .env python evals/run_evals.py --judge    # + LLM judge (2 calls per seed)
  uv run python evals/run_evals.py --json                   # also save data/evals/scorecard.json

Exit code 1 if an exact rule (exact_line, trace_number, same_payment) scores
below 100%, or if a summary fails its number check. Otherwise 0.
Generated files go to data/evals/, which Git ignores.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import anthropic

from echo_check import summary
from echo_check.config import load_config
from echo_check.engine import CheckResult, check_file
from echo_check.evaluate import (
    RuleScore,
    add_scores,
    check_summary_numbers,
    exact_rules_perfect,
    judge_summary,
    score_rules,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GENERATOR = PROJECT_ROOT / "generator" / "generate_nacha.py"
NACHA_CONFIG = PROJECT_ROOT / "configs" / "nacha.yaml"
EVALS_DIR = PROJECT_ROOT / "data" / "evals"
DEFAULT_SEEDS = [1, 2, 3, 4, 5]


# ---------- files ----------


def file_size_for(seed: int) -> dict[str, int]:
    """Vary the file shape by seed, so the evals don't test one shape only."""
    return {
        "batches": 3 + seed % 4,
        "entries": 20 + (seed * 10) % 41,
        "dupes": 2 + seed % 4,
    }


def generate(seed: int) -> tuple[Path, dict[str, Any]]:
    """Write data/evals/seed-N.ach and its answer key. Return the path and the key."""
    prefix = EVALS_DIR / f"seed-{seed}"
    size = file_size_for(seed)
    command = [
        sys.executable, str(GENERATOR), "--out", str(prefix), "--seed", str(seed),
        "--batches", str(size["batches"]), "--entries", str(size["entries"]),
        "--dupes", str(size["dupes"]),
    ]
    subprocess.run(command, check=True, capture_output=True)
    answer_key = json.loads(prefix.with_suffix(".answers.json").read_text(encoding="utf-8"))
    return prefix.with_suffix(".ach"), answer_key


# ---------- printing ----------


def percent(score: float, has_data: bool) -> str:
    """'100.0%', or 'n/a' when there was nothing to count."""
    return f"{score:6.1%}" if has_data else "   n/a"


def print_scorecard(totals: list[RuleScore]) -> None:
    """One row per rule, totalled over all seeds."""
    print(f"\n{'rule':<14}{'TP':>6}{'FP':>6}{'FN':>6}{'cross':>7}  {'precision':>9}  {'recall':>7}")
    for score in totals:
        precision = percent(score.precision, score.tp + score.fp > 0)
        recall = percent(score.recall, score.tp + score.fn > 0)
        print(
            f"{score.rule:<14}{score.tp:>6}{score.fp:>6}{score.fn:>6}{score.cross:>7}"
            f"  {precision:>9}  {recall:>7}"
        )
    print("(cross = pairs also caught by another rule; not scored)")


# ---------- summary evals ----------


def eval_summary(result: CheckResult, client: anthropic.Anthropic, judge: bool) -> dict[str, Any]:
    """Run the summary on one result, check its numbers and optionally judge it."""
    try:
        written = summary.summarize(result, client=client)
    except (anthropic.AnthropicError, summary.SummaryError) as error:
        return {"ok": False, "error": str(error)}
    summary_input = summary.build_summary_input(result)
    unknown = check_summary_numbers(written.summary, summary_input)
    outcome: dict[str, Any] = {
        "ok": not unknown,
        "headline": written.summary.headline,
        "unknown_numbers": unknown,
        "warnings": written.warnings,
    }
    if judge:
        try:
            outcome["judge"] = judge_summary(written.summary, summary_input, client).model_dump()
        except (anthropic.AnthropicError, ValueError) as error:
            outcome["judge"] = {"error": str(error)}
    return outcome


def print_summary_outcome(seed: int, outcome: dict[str, Any]) -> None:
    """One or two lines per seed."""
    if "error" in outcome:
        print(f"  seed {seed}: FAILED: {outcome['error']}")
        return
    status = "pass" if outcome["ok"] else f"FAIL, unknown numbers {outcome['unknown_numbers']}"
    print(f"  seed {seed}: numbers {status}. {outcome['headline']}")
    verdict = outcome.get("judge")
    if verdict and "error" not in verdict:
        print(
            f"           judge: clarity {verdict['clarity']}/5, "
            f"actionable {verdict['actionable']}, resent batch {verdict['names_resent_batch']}. "
            f"{verdict['notes']}"
        )
    elif verdict:
        print(f"           judge FAILED: {verdict['error']}")


# ---------- main ----------


def build_parser() -> argparse.ArgumentParser:
    """Describe the command-line arguments."""
    parser = argparse.ArgumentParser(description="Score echo-check against seeded answer keys.")
    parser.add_argument("--seeds", type=int, nargs="+", default=DEFAULT_SEEDS)
    parser.add_argument("--summary", action="store_true", help="also check summaries (API calls)")
    parser.add_argument("--judge", action="store_true", help="also judge summaries (more API calls)")
    parser.add_argument("--json", action="store_true", help="save data/evals/scorecard.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the evals and return the exit code."""
    args = build_parser().parse_args(argv)
    run_summaries = args.summary or args.judge
    config = load_config(NACHA_CONFIG)
    EVALS_DIR.mkdir(parents=True, exist_ok=True)

    all_scores: list[RuleScore] = []
    results: dict[int, CheckResult] = {}
    print("Detection")
    for seed in args.seeds:
        path, answer_key = generate(seed)
        result = check_file(path, config, mask=True)
        results[seed] = result
        scores = score_rules(result, answer_key)
        all_scores += scores
        found = sum(score.tp for score in scores)
        expected = found + sum(score.fn for score in scores)
        print(f"  seed {seed}: {result.records_checked} records, {found}/{expected} planted pairs found")
    totals = add_scores(all_scores)
    print_scorecard(totals)
    passed = exact_rules_perfect(totals)
    print(f"\nExact rules at 100%: {'yes' if passed else 'NO'}")

    summary_outcomes: dict[int, dict[str, Any]] = {}
    if run_summaries:
        calls = len(args.seeds) * (2 if args.judge else 1)
        print(f"\nSummaries ({calls} API calls)")
        client = summary.make_client()
        for seed in args.seeds:
            outcome = eval_summary(results[seed], client, args.judge)
            summary_outcomes[seed] = outcome
            print_summary_outcome(seed, outcome)
        passed = passed and all(outcome["ok"] for outcome in summary_outcomes.values())

    if args.json:
        scorecard = {
            "seeds": args.seeds,
            "detection": [score.model_dump() for score in totals],
            "exact_rules_perfect": exact_rules_perfect(totals),
            "summaries": summary_outcomes,
        }
        out = EVALS_DIR / "scorecard.json"
        out.write_text(json.dumps(scorecard, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {out}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
