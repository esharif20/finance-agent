# Threat model

Status: draft for review. Grows with each milestone.

## Assets

1. Transactions and balances (the Ledger).
2. Statement Imports, which contain full sort codes and account numbers.
3. Memories and feedback: what the user values, regrets and plans.
4. Credentials: SQLCipher key, restic password, cloud API keys, later Open Banking tokens.

## Trust boundaries

| # | Boundary | Crosses it | Rule |
| --- | --- | --- | --- |
| B1 | File system → `ingest` | Bank CSVs | Untrusted. Parsed strictly; malformed rows rejected with line numbers; files read only from paths the user passes. |
| B2 | `ingest` → rest of core | Transactions | Full account numbers never cross. Only the masked form leaves `ingest`. |
| B3 | Core → `assistant` | Tool results | Read-only, allowlisted tools; aggregates, at most 20 rows, evidence IDs; descriptions marked as data and truncated. |
| B4 | `assistant` → model | Prompt | Local Ollama on localhost only, unless the user opts in per conversation. |
| B5 | `gateway` → cloud | Placeholder payload | Allowlist only: intent, generic Category names, placeholders, size buckets, months. Never merchants, amounts, exact dates, account details or Memory text (ADR 0011). |
| B6 | Mac → backup storage | restic chunks | Encrypted on the Mac before upload. |
| B7 | Repo → GitHub (public) | Code, fixtures, issues | Synthetic data only. Enforced by gitleaks and the fixture guard in CI. |

## Threats and mitigations

| Threat | Mitigation | Residual risk |
| --- | --- | --- |
| Laptop stolen or disk imaged | FileVault, plus SQLCipher with the key in Keychain before any real data (ADR 0002) | An unlocked, logged-in Mac exposes everything |
| Real data committed to the public repo | `.gitignore`, fixture guard (CSV and SQLite only in `tests/fixtures/synthetic/` with a `# synthetic` header), gitleaks, data directory outside the repo | Data pasted into an issue or PR comment; covered by rule only |
| Prompt injection via merchant descriptors or CSV cells | Descriptions are data fields, truncated to 60 characters, instruction-like text stripped; the Assistant has no write tools apart from Proposals that need a click; CI injection suite | A misleading explanation; caught by the grounding check if it involves figures |
| Prompt injection via chat | Same tool limits; router cannot reach tools outside its sub-graph | As above |
| LLM invents figures | Grounding check: every figure must match a tool result in that turn (ADR 0007) | Wrong words around correct figures |
| Data leaves via tracing or telemetry | LangSmith and external tracing disabled; CI egress test allows only localhost Ollama | Third-party library adding telemetry in an update; the egress test catches it in CI |
| Cloud provider sees personal data | Placeholders, allowlist, opt-in, payload log (ADR 0011) | Patterns inferable from generic Category names and month-level timing |
| Runaway cloud spend | Cost meter with hard £7 cap (ADR 0012) | None beyond the cap |
| Arbitrary file access through the app | No tool reads files; `ingest` reads only paths given on the CLI; the API has no file-path parameters; the app refuses to read data from inside the repo | |
| Card art endpoint used to read other files | `/api/card-art/{key}` takes only allowlisted keys (`lloyds`, `hsbc`, `revolut`), resolves inside `card-art/` in the data directory, opens once with no symlinks followed and reads at most 2 MB from that handle (no TOCTOU), refuses FIFOs and devices, sniffs PNG/JPEG/WebP magic bytes (else 415), caps at 2 MB (413), sends `no-store` and `nosniff` (#58) | Anyone who can write to the data directory can change the picture |
| Local network attacker | Server binds to `127.0.0.1` only | Other local processes on the same Mac can reach the port; acceptable for one user |
| Logs leak data | Structured logging with a redaction filter: amounts, descriptions and account fields are never logged; a test asserts it | |
| Unauthorised financial actions | No payment or transfer capability exists anywhere; Open Banking access is read-only (AIS) | |
| Model memorises data | No fine-tuning on real data (ADR 0009) | |
| Bad Memory steers advice | Memories only via confirmed Proposals with provenance; financial facts never stored as Memory | User confirms something wrong; visible and deletable |

## Not in scope (yet)

Multi-user isolation, authentication against remote attackers and regulatory compliance are out of scope while the app holds one user's data locally (ADR 0003). The public demo holds only synthetic data.
