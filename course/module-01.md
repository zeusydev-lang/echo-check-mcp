# Module 1: Set up the project

## Goal

By the end of this module you have a Python project that:

- installs a `dupcheck` command,
- runs tests with one command,
- can make the same synthetic NACHA file every time.

## The concept

**uv runs everything.** `uv` manages Python, the virtual environment and the
dependencies. You never call `python` or `pip` directly. You type `uv run ...`
and uv makes sure the right environment is used.

**The `src/` layout.** The code lives in `src/echo_check/`, not in the project
root. This means tests can only import the package once it is properly
installed. If packaging breaks, the tests catch it right away.

**Entry points.** One line in `pyproject.toml` turns a Python function into a
command:

```toml
[project.scripts]
dupcheck = "echo_check.cli:main"
```

When uv installs the project, it creates a `dupcheck` command that calls
`main()` in `echo_check/cli.py`.

**Synthetic data with a seed.** The generator makes fake NACHA files with
duplicates planted on purpose. It also writes an answer key that lists where
each duplicate is. The same seed always gives the same file, so `data/sample.ach`
(seed 42) is a fixed test case.

## Code walkthrough

1. `pyproject.toml` lists the dependencies, the `dupcheck` entry point and
   the build backend (`uv_build`). The build backend is what makes the
   package installable.
2. `src/echo_check/__init__.py` marks the folder as a package and holds the
   version number.
3. `src/echo_check/cli.py` has a stub `main()`. For now it only prints a message.
4. `tests/test_smoke.py` checks that the package imports and that the
   `dupcheck` command runs.
5. `tests/test_generator.py` runs the generator with seed 42 in a temporary
   folder and compares the result to the committed sample byte for byte.
6. `.gitignore` ignores everything under `data/` except the two sample files,
   so generated data never ends up in Git by accident.

Try it:

```powershell
uv sync
uv run pytest
uv run dupcheck
uv run python generator/generate_nacha.py --out data/sample --seed 42
```

## Exercise

Generate a bigger file with a different seed:

```powershell
uv run python generator/generate_nacha.py --out data/big --seed 7 --batches 20 --entries 50
```

Open `data/big.answers.json` and find one `near_amount` duplicate. Look up both
lines in `data/big.ach`. How far apart are the two amounts?
Then run `git status`. Why doesn't `data/big.ach` show up?

## What can go wrong

- **`dupcheck` is not found.** The project isn't installed. Run `uv sync`, and
  check that `[build-system]` is in `pyproject.toml`.
- **A warning says `VIRTUAL_ENV` doesn't match.** Your terminal has a different
  project's environment active. uv ignores it and uses this project's `.venv`,
  so you can ignore the warning, or run `deactivate` first.
- **The generator test fails.** Someone changed the generator, so seed 42 no
  longer gives the committed sample. If the change was on purpose, regenerate
  `data/sample.*` and commit it together with the change.
- **pytest says "no tests ran" (exit code 5).** pytest treats zero tests as a
  failure. That's why this module starts with smoke tests.
