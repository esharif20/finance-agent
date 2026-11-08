# Threat model

Status: draft for review. Grows with each milestone.

## Assets

1. Transactions and balances (the Ledger).
2. Statement Imports, which contain full sort codes and account numbers.
3. Memories and feedback: what the user values, regrets and plans.
4. Credentials: SQLCipher key, the account-reference HMAC key, the backup recovery key (both keys together), the SimpleFIN access token, cloud API keys.

## Trust boundaries

| # | Boundary | Crosses it | Rule |
| --- | --- | --- | --- |
| B1 | File system → `ingest` | Bank CSVs | Untrusted. Parsed strictly; malformed rows rejected with line numbers; files read only from paths the user passes. |
| B2 | `ingest` → rest of core | Transactions | Full account numbers never cross. Only the masked form leaves `ingest`. |
| B3 | Core → `assistant` | Tool results | Read-only, allowlisted tools; aggregates, at most 20 rows, evidence IDs; descriptions marked as data and truncated. |
| B4 | `assistant` → model | Prompt | Local Ollama on localhost only, unless the user opts in per conversation. |
| B4a | `categorise.embedding` → Ollama | Merchant names (normalised descriptors) | `http://localhost:11434` only, the same guard as B4: any other host, port, scheme, credentials or path is refused, redirects are not followed, no proxy env vars. Replies are validated (shape, finite numbers, at most 4096 dimensions); errors and logs carry counts only (#113). |
| B5 | `gateway` → cloud | Placeholder payload | Allowlist only: intent, generic Category names, placeholders, size buckets, months. Never merchants, amounts, exact dates, account details or Memory text (ADR 0011). One pinned host, `generativelanguage.googleapis.com` (Gemini), HTTPS, only from `gateway.cloud`; no redirects, no proxy env vars, no SDK retries or telemetry beyond what the pinned client sets, and the SDK's own logging off. The key is in the Keychain, and the gateway starts only for a billing-enabled (paid) key (ADR 0016). |
| B6 | Mac → registered external drive | Backup copies (zip of the SQLCipher database, card art, backup config) | Still SQLCipher-encrypted. Drive matched by Volume UUID only; the internal disk is refused; nothing is written elsewhere. `fa backup` deletes only files in its own manifest (#95). |
| B7 | Repo → GitHub (public) | Code, fixtures, issues | Synthetic data only. Enforced by gitleaks and the fixture guard in CI. |
| B8 | `ingest` ↔ SimpleFIN aggregator (Lunch Flow, via GoCardless Bank Account Data) | Access token out; Accounts, balances, Transactions in | One pinned host, HTTPS, read-only; token in Keychain; responses untrusted and parsed strictly like CSVs (ADR 0013). |
| B9 | Visitor → hosted demo | HTTP requests, the signed cookie, OAuth callback | Untrusted. Signed cookie (visitor id and Persona only), Origin check on every write, one SQLite file per visitor inside `FA_DEMO_DIR`, size and count caps on files, requests and sessions (ADR 0016). |

## Threats and mitigations

| Threat | Mitigation | Residual risk |
| --- | --- | --- |
| Laptop stolen or disk imaged | FileVault, plus SQLCipher with the key in Keychain before any real data (ADR 0002) | An unlocked, logged-in Mac exposes everything |
| Real data committed to the public repo | `.gitignore`, fixture guard (CSV and SQLite only in `tests/fixtures/synthetic/` with a `# synthetic` header), gitleaks, data directory outside the repo | Data pasted into an issue or PR comment; covered by rule only |
| Prompt injection via merchant descriptors or CSV cells | Descriptions are data fields, truncated to 60 characters, instruction-like text stripped; the Assistant has no write tools apart from Proposals that need a click; CI injection suite | A misleading explanation; caught by the grounding check if it involves figures |
| Prompt injection via chat | Same tool limits; router cannot reach tools outside its sub-graph | As above |
| LLM invents figures | Grounding check: every figure must match a tool result in that turn (ADR 0007) | Wrong words around correct figures |
| Data leaves via tracing or telemetry | LangSmith and external tracing disabled: importing `finance_agent.assistant` sets the `LANGSMITH_*`/`LANGCHAIN_*` tracing env vars to `false` for the whole process and calls `langsmith.configure(enabled=False)`, which holds even if langgraph was imported first (deliberate: nothing in the app may trace, ADR 0008). `OllamaChatModel` dials only a loopback host (`localhost`, `127.0.0.1`, `::1`) on port 11434. CI egress test allows only localhost Ollama and the pinned SimpleFIN host from `ingest`, and a subprocess probe runs graphs with tracing switched on in the env and waits for delayed telemetry | Third-party library adding telemetry in an update; the egress test catches it in CI. A process that never imports the assistant gets no such override |
| Cloud provider sees personal data | Placeholders, allowlist, opt-in, payload log (ADR 0011) | Patterns inferable from generic Category names and month-level timing |
| Aggregator sees or leaks the user's bank data | One read-only SimpleFIN provider (Lunch Flow, relaying through the regulated GoCardless Bank Account Data; bank consent expires every 90 days and Lunch Flow retains data for 90 days) on a pinned host; consent age and sync failures are shown by `fa summary` with error class and counts only; token in Keychain and revocable with `fa disconnect`; provider's retention and deletion terms checked in the spike before connecting (ADR 0013) | The provider holds a copy of transactions under its own terms; a breach there is outside the app's control |
| Backup drive lost or stolen | Copies hold only the SQLCipher-encrypted database; the key stays in the Keychain (#95) | Card art and the backup config (a volume UUID) are not encrypted; the recovery key, if leaked from the password manager, opens every copy |
| Recovery key leaks | Treated as a credential: shown once on screen by `fa backup setup`, never written to disk, logs or argv; `fa restore --recovery-key` reads it from stdin and stores it in the Keychain only after the copy opens with it | Anyone holding both the key and a copy can read the Ledger |
| Something on the Mac deletes or alters the always-mounted backups | Dedicated folder; `fa backup` deletes only manifest-listed names and refuses a tampered manifest; missing copies are warned about, not repaired; the app warns when no copy is newer than 7 days | Malware or a mistake on the Mac can still delete the folder |
| Theft, fire or a power surge takes the Mac and the drive together | An occasional offline copy to a second drive kept elsewhere (documented, no code) | Without that copy, a local disaster loses everything since the last offline copy |
| Runaway paid spend | One cost meter with a hard £9 monthly cap: aggregator ≤ £5, pay-as-you-go ≤ £4 (ADR 0013) | None beyond the cap |
| Demo abuse (disk fill, CPU) | Per-visitor file with a size cap, a cap on visitors and on requests per visitor, expiry of idle ledgers, and no import, upload or sync path in the demo | Short outages of the free tier under a flood; nothing persists |
| Cookie forgery or replay in the demo | The cookie is signed with a server-side secret, holds only a visitor id and Persona, and is checked on every request; a forged or unknown id gets a fresh session, never another visitor's file | A stolen cookie opens that visitor's synthetic ledger only |
| OAuth CSRF on the demo's GitHub login | A random `state` bound to the session and checked on the callback, Origin check, exact redirect URI, and login stays optional | A visitor logged in as someone else sees only synthetic data |
| The demo opens a real database | `FA_DEMO=1` opens only paths under `FA_DEMO_DIR`, refuses SQLCipher and Keychain paths, and the real data directory is never mounted or deployed; a test starts the demo against a decoy real path and expects a refusal | Operator misconfiguration; the deploy contains no real data to open |
| Arbitrary file access through the app | No tool reads files; `ingest` reads only paths given on the CLI; the API has no file-path parameters; the app refuses to read data from inside the repo | |
| Card art endpoint used to read other files | `/api/card-art/{key}` takes only allowlisted keys (`lloyds`, `hsbc`, `revolut`), resolves inside `card-art/` in the data directory, opens once with no symlinks followed and reads at most 2 MB from that handle (no TOCTOU), refuses FIFOs and devices, sniffs PNG/JPEG/WebP magic bytes (else 415), caps at 2 MB (413), sends `no-store` and `nosniff` (#58) | Anyone who can write to the data directory can change the picture |
| Local network attacker | Server binds to `127.0.0.1` only | Other local processes on the same Mac can reach the port; acceptable for one user |
| Logs leak data | Structured logging with a redaction filter: amounts, descriptions and account fields are never logged; a test asserts it | |
| Unauthorised financial actions | No payment or transfer capability exists anywhere; Open Banking access is read-only (AIS) | |
| Model memorises data | No fine-tuning on real data (ADR 0009) | |
| Bad Memory steers advice | Memories only via confirmed Proposals with provenance; financial facts never stored as Memory | User confirms something wrong; visible and deletable |

## Not in scope (yet)

Multi-user isolation, authentication against remote attackers and regulatory compliance are out of scope while the app holds one user's data locally (ADR 0003). The hosted demo's authentication (signed cookie, optional GitHub login) is not a defence for real data: it holds only synthetic data and its ledgers are ephemeral (ADR 0016).
