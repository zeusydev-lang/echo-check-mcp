"""The generator must keep making the same sample file for seed 42."""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GENERATOR = PROJECT_ROOT / "generator" / "generate_nacha.py"
SAMPLE_DIR = PROJECT_ROOT / "data"


def run_generator(out_prefix: Path, seed: int) -> None:
    """Run the generator script and fail the test if it exits with an error."""
    command = [sys.executable, str(GENERATOR), "--out", str(out_prefix), "--seed", str(seed)]
    subprocess.run(command, check=True, capture_output=True)


def test_seed_42_matches_committed_sample(tmp_path: Path) -> None:
    """Seed 42 recreates data/sample.ach and data/sample.answers.json byte for byte."""
    run_generator(tmp_path / "sample", seed=42)

    for name in ["sample.ach", "sample.answers.json"]:
        fresh = (tmp_path / name).read_bytes()
        committed = (SAMPLE_DIR / name).read_bytes()
        assert fresh == committed, f"{name} changed. Did the generator change?"


def test_every_record_is_94_characters(tmp_path: Path) -> None:
    """Every line the generator writes is exactly 94 characters long."""
    run_generator(tmp_path / "other", seed=7)

    lines = (tmp_path / "other.ach").read_text(encoding="ascii").splitlines()
    for number, line in enumerate(lines, start=1):
        assert len(line) == 94, f"line {number} is {len(line)} characters"
