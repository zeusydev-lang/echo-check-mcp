"""Time the engine on a big generated NACHA file.

Usage:
  uv run python evals/benchmark.py                 # about 960k entry lines
  uv run python evals/benchmark.py --memory        # also measure peak memory (slower)

The file goes to data/bench.ach, which Git ignores. It is only generated
once. Use --regenerate to make it again.
"""

import argparse
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

from echo_check.config import load_config
from echo_check.engine import CheckResult, check_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GENERATOR = PROJECT_ROOT / "generator" / "generate_nacha.py"
NACHA_CONFIG = PROJECT_ROOT / "configs" / "nacha.yaml"
BENCH_PREFIX = PROJECT_ROOT / "data" / "bench"


def generate(batches: int, entries: int) -> None:
    """Write data/bench.ach with the generator."""
    command = [
        sys.executable, str(GENERATOR), "--out", str(BENCH_PREFIX), "--seed", "1",
        "--batches", str(batches), "--entries", str(entries), "--dupes", "100",
    ]
    started = time.perf_counter()
    subprocess.run(command, check=True)
    print(f"Generated in {time.perf_counter() - started:.1f} s")


def timed_check(path: Path) -> tuple[CheckResult, float]:
    """Run one check and return the result and the wall-clock seconds."""
    config = load_config(NACHA_CONFIG)
    started = time.perf_counter()
    result = check_file(path, config)
    return result, time.perf_counter() - started


def peak_memory_mb(path: Path) -> float:
    """Run one check under tracemalloc and return the peak in MB."""
    config = load_config(NACHA_CONFIG)
    tracemalloc.start()
    check_file(path, config)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak / 1_000_000


def main() -> None:
    parser = argparse.ArgumentParser(description="Time the engine on a big NACHA file.")
    # The generator gives every new entry a unique ID from EMP000000 to EMP999999,
    # so it can make at most 1M new entries, and slows down close to that limit.
    # 95 x 10,000 new entries plus a resent batch stays safely below it.
    parser.add_argument("--batches", type=int, default=95, help="batches in the file")
    parser.add_argument("--entries", type=int, default=10_000, help="entries per batch")
    parser.add_argument("--regenerate", action="store_true", help="make the file again")
    parser.add_argument("--memory", action="store_true", help="also measure peak memory")
    args = parser.parse_args()

    path = BENCH_PREFIX.with_suffix(".ach")
    if args.regenerate or not path.exists():
        generate(args.batches, args.entries)

    result, seconds = timed_check(path)
    per_second = result.total_lines / seconds
    print(f"File:     {path} ({path.stat().st_size / 1_000_000:.0f} MB)")
    print(f"Lines:    {result.total_lines:,} total, {result.records_checked:,} entry records")
    print(f"Time:     {seconds:.1f} s ({per_second:,.0f} lines per second)")
    print(f"Groups:   {result.stats.groups_per_rule}")
    if args.memory:
        print(f"Peak mem: {peak_memory_mb(path):,.0f} MB (Python objects, via tracemalloc)")


if __name__ == "__main__":
    main()
