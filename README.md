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

## Local model (optional, one-time setup)

Category suggestions use a local embedding model through
[Ollama](https://ollama.com), on `localhost:11434` only. The app never
installs or downloads it; run this once yourself:

```
brew install ollama
ollama pull nomic-embed-text
ollama pull qwen3.5:9b-q4_K_M   # the Assistant (#144), about 6.7 GB while loaded
```

Without it the app keeps to Corrections and rules, and `/api/local-model`
reports `off`. Ollama loads the model when it is asked and unloads it about a
minute later. `uv run fa forget-embeddings` deletes every stored Merchant
vector; they are rebuilt from your Transactions when needed.

## Importing

```
uv run fa import export1.csv export2.csv
```

A file with any bad row is refused whole and listed with its line numbers; the
other files still import. A file that no Importer recognises stops the whole
batch, and the error names it. Exit status:

| Code | Meaning |
| --- | --- |
| 0 | Every file imported (or was already known) |
| 1 | Nothing imported |
| 2 | Some files imported, others refused |
