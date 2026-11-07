# Data policy

Status: draft for review.

## Where data lives

- Real data: `~/Library/Application Support/FinanceAgent/` only, in the SQLCipher database. The app refuses to open a database inside the repository.
- Development and CI: synthetic data only, in `tests/fixtures/synthetic/` and `evals/datasets/`.
- No real data in Git, issues, PRs, fixtures, logs, telemetry or third-party tracing. Ever.
- Off the Mac, real data reaches only the SimpleFIN aggregator the user connects (which already relays it from the Banks) (ADR 0013). Backups go to a local external drive the user registers, still SQLCipher-encrypted; nothing is backed up to the cloud. The SimpleFIN provider is Lunch Flow, which relays through GoCardless Bank Account Data, a regulated provider. Bank consent lasts 90 days and then needs renewing (`fa summary` and the dashboard warn 14 days ahead). Lunch Flow keeps the data it relays for 90 days; the app's own encrypted copy has no such limit, so deleting it at Lunch Flow does not delete it here, and the reverse.

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
| Backups (external drive) | The newest copy of each of the 7 latest days, plus the newest copy of each of the 4 ISO weeks before those | Deleting data locally reaches the backups within 4 weeks, as older copies are pruned |

A `fa forget` command lists and deletes Memories, summaries and feedback. Deletion is real: no model is trained in a way that would keep a copy (ADR 0009).

## Secrets

SQLCipher key, the account-reference HMAC key, the SimpleFIN access token and API keys live in the macOS Keychain, never in files or environment variables committed anywhere. `.env` files are git-ignored. The backup recovery key (the SQLCipher and HMAC keys together) is shown once by `fa backup setup` for the user's password manager; it is never written to disk, logs or argv, and `fa restore --recovery-key` reads it from stdin.

## Backups

Local, light and free: no restic, no cloud, no new dependency (#95; this replaces the earlier plan of restic to Backblaze B2).

- **The drive.** `fa backup setup` registers one external volume by its Volume UUID (never its name) and refuses the internal disk. The config in the data directory holds only that UUID and a folder name. A different drive with the same name is not the backup drive.
- **When.** After each successful scheduled sync (the existing `launchd` job), and when a drive is mounted if the last good copy is over 24 hours old (a `StartOnMount` agent). The drive normally stays connected through a dock, so the first is the usual path.
- **A copy.** One zip in the dedicated folder: the database taken with SQLite's backup API (still encrypted with the same key), the card art and the backup config. It is written to a temp file, fsynced, renamed, then read back and checked with `cipher_integrity_check`. If the drive is missing or read-only the run skips and records it; it never writes anywhere else. A failed write leaves no partial copy, and the next run tries once more, never in a loop.
- **Deletion.** The folder's manifest lists the copies `fa backup` made. It prunes only by that manifest and deletes nothing else. Copies listed but missing are warned about, never silently "repaired".
- **Restore.** `fa restore [<copy>]` defaults to the newest copy, checks that it opens and passes the integrity check, refuses a schema newer than the app and migrates an older one. The current ledger is kept as `ledger.db.pre-restore`; restore deletes nothing.
- **Status.** The app shows when the last backup ran and warns when a registered drive has had no copy for 7 days.
- **Same place as the Mac.** The drive sits next to the Mac, so theft, fire or a power surge can take both. The mitigation is an occasional offline copy: now and then, copy the newest zip to a second drive kept somewhere else. There is no code for it.

Backup and restore are tested in CI against synthetic databases, temp directories and a fake volume resolver only.

## Synthetic data

A generator produces realistic Lloyds, HSBC and Revolut CSVs (messy descriptors, overlaps, refunds, transfers, pending rows, foreign currency, injection attempts). Every synthetic file starts with a `# synthetic` header. A cloud model may help generate synthetic data; it never sees real data.
