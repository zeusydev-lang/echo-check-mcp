"""The `dupcheck` command-line tool.

Usage:
  uv run dupcheck data/sample.ach --config configs/nacha.yaml
  uv run dupcheck data/sample.ach --config configs/nacha.yaml --out results.json

  uv run dupcheck data/sample.ach --config configs/nacha.yaml --summary

Without --out, the JSON result goes to stdout. With --out, it goes to the
file and a short summary goes to stdout.

--summary asks Claude for a short written summary (needs ANTHROPIC_API_KEY).
Claude only sees counts and masked samples.

Exit codes: 0 = the check ran (even if it found duplicates),
            1 = the check ran, but the summary failed,
            2 = a problem with the input (missing file, bad config, bad
                arguments, missing API key).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import anthropic

from echo_check import summary
from echo_check.config import ConfigError, load_config
from echo_check.engine import CheckResult, check_file

EXIT_OK = 0
EXIT_SUMMARY_ERROR = 1
EXIT_INPUT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    """Describe the command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="dupcheck",
        description="Find duplicate records in a fixed-width file.",
    )
    parser.add_argument("file", type=Path, help="the fixed-width file to check")
    parser.add_argument("--config", type=Path, required=True, help="the YAML layout config")
    parser.add_argument("--out", type=Path, help="write the JSON result to this file")
    parser.add_argument(
        "--unmasked",
        action="store_true",
        help="show sensitive values in full (for local investigation only)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="add a short summary written by Claude (needs ANTHROPIC_API_KEY)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the `dupcheck` command and return its exit code."""
    args = build_parser().parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return EXIT_INPUT_ERROR

    if not args.file.is_file():
        print(f"{args.file}: file not found", file=sys.stderr)
        return EXIT_INPUT_ERROR

    if args.summary and args.unmasked:
        print("--summary can't be used with --unmasked.", file=sys.stderr)
        return EXIT_INPUT_ERROR

    result = check_file(args.file, config, mask=not args.unmasked)
    output: dict[str, Any] = result.model_dump(mode="json")
    exit_code = EXIT_OK

    if args.summary:
        summary_output, exit_code = run_summary(result)
        if summary_output is not None:
            output["summary"] = summary_output

    text = json.dumps(output, indent=2)
    if args.out is None:
        print(text)
    else:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(summary_text(result))
        if "summary" in output:
            print(f"Summary: {output['summary']['summary']['headline']}")
        print(f"Wrote {args.out}")
    return exit_code


def run_summary(result: CheckResult) -> tuple[dict[str, Any] | None, int]:
    """Ask for the summary. Return (summary as JSON-ready dict or None, exit code)."""
    try:
        written = summary.summarize(result)
    except summary.MissingCredentialsError as error:
        print(error, file=sys.stderr)
        return None, EXIT_INPUT_ERROR
    except anthropic.AuthenticationError:
        print("Summary failed: the API key was rejected. Check ANTHROPIC_API_KEY.", file=sys.stderr)
        return None, EXIT_INPUT_ERROR
    except (anthropic.AnthropicError, summary.SummaryError) as error:
        print(f"Summary failed: {error}", file=sys.stderr)
        return None, EXIT_SUMMARY_ERROR
    return written.model_dump(mode="json"), EXIT_OK


def summary_text(result: CheckResult) -> str:
    """A few human-readable lines about the result."""
    first_line = (
        f"Checked {result.records_checked} records out of {result.total_lines} lines "
        f"in {result.file} (config {result.config}), "
        f"{result.stats.bad_line_count} bad line(s)."
    )
    lines = [first_line]
    for rule in result.rules:
        line_count = result.stats.lines_in_groups_per_rule[rule.name]
        lines.append(f"  {rule.name}: {len(rule.groups)} group(s), {line_count} lines")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
