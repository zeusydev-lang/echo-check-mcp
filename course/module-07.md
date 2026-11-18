# Module 7: Ship it

## Goal

By the end of this module, a stranger can install echo-check in two minutes,
every push is tested automatically, this course is a website, and there's a
`v1.0.0` release.

## The concept

**The README is the front door.** Most people read the first screen and leave.
Put the quick start first, with a command that works on a fresh clone, and
show the output they should see. Then the details: config guide, privacy, setup.

**CI is a promise.** A green badge says "the tests passed on a clean machine",
not just on yours. Our CI runs four checks with no secrets:

1. **Lint** (`ruff`) catches unused imports and risky patterns.
2. **Tests** (`pytest`) need no API key, because the summary tests use a fake client.
3. **Detection evals** (`run_evals.py`) exit with 1 if an exact rule drops
   below 100%, which fails the build.
4. **Docs build** (`mkdocs build --strict`) fails on a broken link.

**Pin your actions.** A workflow line like
`uses: actions/checkout@3d3c42e…  # v7.0.1` points at one exact commit. A tag
like `@v7` can be moved by whoever controls the action, so a pinned SHA
protects you from a tampered release.

**Docs from the same repo.** The course lives in `course/` as Markdown.
MkDocs Material turns it into a site, and a workflow publishes it to GitHub Pages
whenever `course/` changes. The docs can't drift from the code, because they
change in the same pull request.

**Versions mean something.** `1.0.0` says "the config format and the tool
names are stable". After that, a breaking config change means `2.0.0`. A tag
marks the exact commit, and a GitHub release adds notes for humans.

## Code walkthrough

1. **`README.md`**: quick start, config table, privacy, MCP setup, summary, evals.
2. **`.github/workflows/ci.yml`**: one job on Ubuntu. It runs `uv sync --locked`
   (fails if `uv.lock` is out of date), then the four checks.
3. **`.github/workflows/docs.yml`**: a `build` job (`mkdocs build --strict`, then
   upload the site) and a `deploy` job. Only the deploy job gets
   `pages: write`. Every other step is read-only.
4. **`mkdocs.yml`**: the site name, the theme, and the `nav` list of modules.
   Links to repo files such as `src/` are allowed to point outside the site.
5. **`pyproject.toml`**: `version = "1.0.0"`, plus a `docs` dependency group, so
   the site tools aren't installed for normal use.

Try it:

```powershell
uv run --group docs mkdocs serve      # preview at http://127.0.0.1:8000
uv run --group docs mkdocs build --strict
```

The one-time GitHub setup: **Settings → Pages → Source: GitHub Actions**. Then:

```powershell
git tag v1.0.0
git push origin v1.0.0
gh release create v1.0.0 --title "v1.0.0" --notes "First stable release."
```

## Exercise

1. Break a link in `course/module-03.md`, then run
   `uv run --group docs mkdocs build --strict`. What does it say?
2. In a branch, change `same_payment`'s keys so an exact rule drops below 100%.
   Push it and open a PR. Which CI step fails?
3. Write a second config for a different fixed-width format, such as a made-up
   80-character payroll file. How much code did you change?

## What can go wrong

- **"Works on my machine."** Windows line endings, a missing file in Git, or an
  unpinned dependency. CI on a clean Ubuntu runner catches all three.
- **Secrets in CI.** You don't need any here. If you add live summary evals
  later, store the key as a repository secret and run them by hand, not on
  every PR from a fork.
- **Pages not enabled.** The deploy job fails with a "not found" error until
  Pages is set to "GitHub Actions" once.
- **Docs that lie.** Example output in a README goes stale. Copy it from a real
  run, and re-run it before each release.
- **Tagging the wrong commit.** Tag `main` after the merge, not your feature branch.
