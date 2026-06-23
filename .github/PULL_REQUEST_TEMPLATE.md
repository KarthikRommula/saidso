## What does this PR do?

<!-- One or two sentences. Link the issue it closes if applicable: "Closes #123" -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor / cleanup
- [ ] Docs / tests only

## Checklist

- [ ] `ruff check saidso tests` passes
- [ ] `mypy saidso` passes
- [ ] `bandit -r saidso -c pyproject.toml -q` passes
- [ ] `pytest -q` passes (coverage ≥ 90%)
- [ ] New behaviour is covered by tests
- [ ] Zero required dependencies constraint is preserved (nothing added to `[project.dependencies]`)
- [ ] Public API changes are reflected in `docs/ARCHITECTURE.md`

## Notes for the reviewer

<!-- Anything non-obvious, trade-offs made, or context that helps review. -->
