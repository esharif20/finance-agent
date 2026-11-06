# Finance Agent

A privacy-first, local-first personal finance platform. Design in progress.

This repo contains no real financial data. Development uses synthetic fixtures only.

## Run the checks locally

Requires [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run ruff check .
uv run pyright
uv run lint-imports
uv run pytest
uv run python scripts/fixture_guard.py
gitleaks git . --no-banner --redact   # if gitleaks is installed
```

CI (`.github/workflows/ci.yml`, job `checks`) runs the same commands on every PR.
