# Contributing

## Local checks

```bash
pip install -e ".[dev]"
pre-commit install
pre-commit run --all-files
pytest tests/ -q
```

## Versioning

This project uses semantic versioning:

- `0.0.x` patch-level corrections.
- `0.x.0` feature milestones before stability.
- `1.x.y` stable public behavior.

When changing behavior or packaging metadata, update `CHANGELOG.md` in the same branch.
