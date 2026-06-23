# Contributing to saidso

## How it works

`main` is protected — no one pushes directly, including the owner. All changes
arrive via pull request and must pass CI before merging. The owner reviews and
merges every PR.

```
your fork → feature branch → PR → CI → owner review → merge to main
```

---

## Quick start

```bash
# 1. Fork the repo on GitHub, then clone your fork.
git clone https://github.com/<your-username>/saidso.git
cd saidso

# 2. Create a branch — use one of the prefixes below.
git checkout -b fix/spoken-date-normalization

# 3. Install dev dependencies.
pip install -e ".[dev]"

# 4. Make your changes, then run the full gate.
python -m ruff check saidso tests
python -m mypy saidso
python -m bandit -r saidso -c pyproject.toml -q
python -m pytest -q            # must stay ≥ 90% coverage

# 5. Push and open a PR against main.
git push origin fix/spoken-date-normalization
```

GitHub will automatically request a review from the maintainer via CODEOWNERS.

---

## Branch naming

| Prefix | Use for |
|--------|---------|
| `fix/` | Bug fixes |
| `feat/` | New features or policy additions |
| `docs/` | Documentation-only changes |
| `test/` | Test-only changes |
| `chore/` | Dependency bumps, CI, tooling |

---

## Quality gates

All four must pass — CI enforces them and the PR cannot merge if any fail.

| Gate | Command |
|------|---------|
| Lint | `ruff check saidso tests` |
| Types | `mypy saidso` |
| Security | `bandit -r saidso -c pyproject.toml -q` |
| Tests + coverage | `pytest -q` (≥ 90% required) |

---

## Hard constraints

**Zero required dependencies.** Do not add anything to `[project.dependencies]`
in `pyproject.toml`. Optional extras go in `[project.optional-dependencies]`.

**Fail-closed.** Any change to grounding or provenance logic must default to
blocking, not passing, when the outcome is ambiguous or an error occurs.

**No public API surface creep.** New symbols in `saidso/__init__.py` need a
clear use-case and a corresponding entry in `docs/ARCHITECTURE.md`.

---

## Reporting bugs

Open an issue using the **Bug report** template. The template asks for a minimal
reproducer — a few lines showing the transcript, the decorated function, and the
call is almost always enough.

## Requesting features

Open an issue using the **Feature request** template. Check `docs/ROADMAP.md`
first; if your idea is already listed there, comment on the roadmap item instead
of filing a new issue.

## Security vulnerabilities

Do **not** open a public issue. See [`SECURITY.md`](../SECURITY.md).

---

## Releases (owner only)

Releases are cut by the owner and publish automatically to PyPI via the
`release.yml` CI workflow.

```bash
# 1. Bump __version__ in saidso/__init__.py.
# 2. Update docs/CHANGELOG.md and saidso/_docs/changelog.md.
# 3. Commit, then tag.
git tag v0.6.0
git push origin v0.6.0
# CI picks up the tag, runs all gates, builds, and uploads to PyPI.
```

The `PYPI_TOKEN` GitHub secret must be set in the repo settings
(Settings → Secrets and variables → Actions) before the first release.
