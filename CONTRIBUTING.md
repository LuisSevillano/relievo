# Contributing

## Local checks

```bash
pip install -e ".[dev]"
pre-commit install
pre-commit run --all-files
pytest tests/ -q
```

## Commit style

- Use a single, descriptive subject line.
- Keep message tone consistent and sentence-cased.
- Avoid prefixes like `feat:`, `fix:`, `chore:`.

## Versioning

This project uses semantic versioning:

- `0.0.x` patch-level corrections.
- `0.x.0` feature milestones before stability.
- `1.x.y` stable public behavior.

When changing behavior or packaging metadata, update `CHANGELOG.md` in the same branch.
