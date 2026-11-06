# Roadmap

Status: draft for review. M0–M2 are detailed; later milestones get their detail from `/to-spec` when they start. Every milestone ends with a working app.

## M0: Foundations

Stories:
- As the developer, I can run `uv run pytest` and CI on every PR runs ruff, pyright, pytest (with Hypothesis), gitleaks and the fixture guard.
- As the developer, I cannot merge to `main` unless CI passes.

Acceptance:
- `uv` project with `src/finance_agent/` layout; ruff, pyright, pytest, Hypothesis configured.
- GitHub Actions workflow; branch protection on `main` requiring it.
- Fixture guard rejects CSV/SQLite outside `tests/fixtures/synthetic/` and files without `# synthetic`.
- Import-linter contract for module dependencies.
- Synthetic Lloyds CSV generator (seeded), producing overlaps and edge cases.

## M1: Ledger and CLI

Stories:
- As the user, I run `fa import lloyds.csv` and my Transactions are stored once, however many times or overlapping I import.
- As the user, I run `fa summary 2026-10` and see money in, money out and net.
- As the user, I am told when the Ledger disagrees with the Bank's balance.

Acceptance (from Q21):
1. A synthetic Lloyds export imports.
2. Importing the same file twice changes nothing (property test).
3. Overlapping exports produce no duplicates (property test).
4. Sum of Transactions equals the bank-reported balance change, or a Reconciliation Gap is reported (property test).
5. Every amount is `Decimal`/minor units (property test and lint rule).
6. A malformed row is rejected with its line number, never dropped silently.
7. `fa summary` prints correct figures.
8. Account numbers are masked outside `ingest`; Statement Imports are stored unchanged.
9. Connection factory, `user_id` on every table, Alembic migrations, data-access layer scoping by user.

## M2: Categories and first UI

Stories:
- As the user, each Transaction has a Kind and, for spending, a Merchant and Category.
- As the user, I open `fa` and see the Overview, drill into a Category, and change a Transaction's Category inline.
- As the user, I press `H` to blur all amounts.

Acceptance:
- Default two-level taxonomy (~10 top level, cap 8 sub each), versioned.
- Merchant normalisation and deterministic rules; Corrections override; every categorisation records its source.
- Refunds count in the month received (ADR 0004).
- FastAPI endpoints, `current_user`, OpenAPI types generated and checked in CI.
- Overview, Category drill-down, Transactions screens per `docs/design/DIRECTIONS.md`.
- `fa` starts the server if needed and opens the browser; `fa stop`.
- Vitest component tests; one Playwright smoke test matching UI totals to the API.

## M3: Real data readiness and learning categories

SQLCipher with Keychain key (gate for real data, ADR 0002); restic backups and `fa restore`; HSBC and Revolut importers; FX conversion with provenance; Transfer detection; Review Queue; ML classifier if it beats the rules baseline; model registry and shadow mode; Enable Banking prototype.

## M4: Assistant

Ollama adapter; model benchmark on the synthetic golden set; LangGraph graph with Ask, Plan, Memory, Correct; read-only tools; grounding check; answer cache; Proposals with confirm cards; injection suite; egress test.

## M5: Intelligence

Personal baselines; detectors; Insight ranking with Beta relevance; subscription audit; regret review; what-if simulator; ETS forecasting; weekly briefing; "How well I know you" page.

## M6: Planning and deeper learning

Money-values conversation; Allocation Plans (linear program vs 50/30/20); knowledge base with citations; pairwise preferences; contextual bandit; change-point detection.

## M7: Public demo and cloud

Landing page with scroll story; demo at `/app` with per-visitor synthetic data; login; domain; privacy gateway, cost meter and cloud model benchmark; cloud-generated synthetic data and cloud judge.

## Later

Open Banking daily sync (after the prototype), email-based subscription signals, native mobile.
