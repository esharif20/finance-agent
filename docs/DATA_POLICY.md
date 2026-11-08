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
| Purpose notes (on a Transaction or a Split part, at most 140 characters) | Until deleted; an edit keeps every earlier version | Deleting a note purges every version of it, and the freed pages are zeroed (SQLCipher's `secure_delete`). Backups made before the delete still hold it until they age out of the backup retention below: about a month (7 daily copies, then 4 weekly ones), and restoring one of them brings the note back. A part note belongs to the Split version it was written on: a re-split carries it only to an unchanged part, and the others stay with the replaced Split, hidden until that Split is restored. Notes are never logged and never sent through the privacy gateway unless the user opts in for one question |
| Possible Duplicate merges (#158) | Until the Transaction goes; never deleted otherwise, only reverted, like a Correction | One row per merge naming the dropped synced copy (no amount, no description). Deleted with its Transaction (cascade, so with an Import or Account deletion, and when matching folds the synced copy into its CSV row). Never logged beyond a count |
| Subscription alerts sent (#108) | Until wiped | Merchant, alert type and the date it is about: never an amount. Deleted with the Merchant (cascade) and by `fa forget-alerts`; an alert still due is then sent again. Notifications name the Merchant and the event only. Logs carry counts |
| Derived data (categorisations, Insights, forecasts, caches) | Recomputed on demand | Rebuilt from the Ledger, so never the only copy of anything |
| Answer cache (#140) | Until the Ledger version changes (any import or Correction): older rows are dropped on the next store | In the encrypted database only. A confirmed or deleted Memory clears all of the user's cached answers; freed pages are zeroed (`secure_delete`). Never logged |
| Merchant embeddings (local-model vectors of a Merchant name, #113) | Until their source goes | One per Merchant, in the encrypted database only. Deleted with the Merchant, with the last Transaction naming it (so with an Import or Account deletion), and all at once by `fa forget-embeddings`; freed pages are zeroed (`secure_delete`). Pretrained model, inference only: no weights hold a copy (ADR 0009). Never logged |
| Subscription registry (entries, reasons, negative entries and their history, #103, #132) | Until their Merchant goes. Append-only: every change adds a row and the earlier rows stay as history (a revert only marks one) | Deleted with the Merchant, history included, by a database trigger (migration 0021), so whichever path removes a Merchant takes its whole history with it (today no path removes a Merchant: deleting an Import or Account removes Transactions, not Merchants, so the registry outlives them); freed pages are zeroed (`secure_delete`). No other path deletes it: dismissing or removing an entry writes a negative entry rather than deleting. Never logged |
| Memories | Until changed or deleted; preferences and goals re-asked after 6 months (`review_on`); constraints do not expire. Each holds its provenance: the turn, the user's own words and the date (#138) | Hard delete of the Memory and the Proposal it came from (`DELETE /api/memories/{id}`), including from caches and summaries; freed pages are zeroed (`secure_delete`). Never logged |
| Proposals (#138) | The proposed Memory or Correction is held only while pending | Confirming or rejecting clears the payload, so a confirmed one lives on only as the Memory, Correction or note it wrote, and a rejected one leaves no trace of what it said (only a feedback signal). Freed pages are zeroed (`secure_delete`). Never logged |
| Conversation (the graph's checkpoints, #139) | Current session only: the messages, replies and route of one thread, in the encrypted database (never tool results) | Deleted when the user ends the conversation (`DELETE /api/chat/{thread}`) and purged after a day idle (on the next chat turn); freed pages are zeroed (`secure_delete`). Never logged |
| Session summaries | 90 days | Automatic |
| Feedback | Kept (it is the learning signal). A rejected Proposal is recorded by its kind only, never its text (#138) | User can wipe it all (`DELETE /api/feedback`): with the rejected Proposals it points at, the Insight ratings and dismissals (#150) and the regret-review answers (#152). Clearing the Insight ones resets relevance to Beta(1, 1); `DELETE /api/insights/feedback` wipes only those |
| Regret-review answers ("worth it?" on a discretionary Transaction, #152) | Kept (feedback, never a Memory), with the week it was asked in | User can wipe them (`DELETE /api/briefing/regret`, or with all feedback by `DELETE /api/feedback`); an answer also goes with its Transaction. Never logged |
| Weekly briefing (#152) | Not stored: computed on request from the Ledger, the Insights and the forecast | Shown in the app only, never sent anywhere (no email or push) |
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

- Official figures in `facts.json` are refreshed by a monthly GitHub Actions job (`scripts/facts_check.py`) that fetches an allowlist of gov.uk and FSCS pages and opens a PR; the app itself never fetches (ADR 0013).
