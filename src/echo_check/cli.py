"""The `dupcheck` command-line tool.

Usage:
  uv run dupcheck data/sample.ach --config configs/nacha.yaml
  uv run dupcheck data/sample.ach --config configs/nacha.yaml --out results.json

Without --out, the JSON result goes to stdout. With --out, it goes to the
file and a short summary goes to stdout.

Exit codes: 0 = the check ran (even if it found duplicates),
            2 = a problem with the input (missing file, bad config, bad arguments).
"""

import argparse
import sys
from pathlib import Path

from echo_check.config import ConfigError, load_config
from echo_check.engine import CheckResult, check_file

EXIT_OK = 0
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

    result = check_file(args.file, config, mask=not args.unmasked)
    output = result.model_dump_json(indent=2)

    if args.out is None:
        print(output)
    else:
        args.out.write_text(output + "\n", encoding="utf-8")
        print(summary_text(result))
        print(f"Wrote {args.out}")
    return EXIT_OK


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
