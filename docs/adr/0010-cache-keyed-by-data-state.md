---
area: backend
status: accepted
---
# Answers are cached by intent, tool calls and Ledger version, not by text similarity

The Assistant's answer cache is keyed by (intent, tool calls with arguments, Ledger version) and stored in the encrypted SQLite database. Any import or Correction bumps the Ledger version, so a stale answer cannot be served. A text-similarity cache was rejected: 'spend in October' and 'spend in November' embed almost identically, and returning one for the other is an invented figure. Embedding similarity is used only to match wording to an intent. Redis was rejected because one user does not need a second server.
