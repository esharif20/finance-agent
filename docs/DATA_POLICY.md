# Data policy

Status: draft for review.

## Where data lives

- Real data: `~/Library/Application Support/FinanceAgent/` only, in the SQLCipher database. The app refuses to open a database inside the repository.
- Development and CI: synthetic data only, in `tests/fixtures/synthetic/` and `evals/datasets/`.
- No real data in Git, issues, PRs, fixtures, logs, telemetry or third-party tracing. Ever.
- Off the Mac, real data reaches only the SimpleFIN aggregator the user connects (which already relays it from the Banks) and encrypted backup chunks (ADR 0013).

## Retention

| Data | Kept | Deletion |
| --- | --- | --- |
| Statement Imports (encrypted originals) | Until deleted by the user | Deleting one removes its Transactions unless they also appear in another import |
| Transactions and Corrections | Until deleted | Cascades to derived tables |
| Derived data (categorisations, Insights, forecasts, caches) | Recomputed on demand | Rebuilt from the Ledger, so never the only copy of anything |
| Memories | Until changed or deleted; preferences re-asked after 6 months; constraints do not expire | Hard delete, including from caches and summaries |
| Conversation | Current session only | Discarded at session end |
| Session summaries | 90 days | Automatic |
| Feedback | Kept (it is the learning signal) | User can wipe it |
| Assistant traces | 30 days, redacted | Automatic |
| Gateway payload log | 90 days | Automatic |
| Backups | 7 daily, 4 weekly, 12 monthly restic snapshots | Deleting data locally ages out of backups on that schedule |

A `fa forget` command lists and deletes Memories, summaries and feedback. Deletion is real: no model is trained in a way that would keep a copy (ADR 0009).

## Secrets

SQLCipher key, restic password, the SimpleFIN access token and API keys live in the macOS Keychain, never in files or environment variables committed anywhere. `.env` files are git-ignored.

## Backups

restic to Backblaze B2, daily via `launchd`. Each run takes a consistent snapshot with SQLite's backup API; restic chunks, encrypts on the Mac and uploads only changed chunks. `fa restore` is exercised in CI against a synthetic database. Arrives with SQLCipher, before real data.

## Synthetic data

A generator produces realistic Lloyds, HSBC and Revolut CSVs (messy descriptors, overlaps, refunds, transfers, pending rows, foreign currency, injection attempts). Every synthetic file starts with a `# synthetic` header. A cloud model may help generate synthetic data; it never sees real data.
