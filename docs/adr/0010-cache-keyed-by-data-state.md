---
area: backend
status: accepted
---
# Answers are cached by intent, tool calls and Ledger version, not by text similarity

The Assistant's answer cache is keyed by (intent, tool calls with arguments, Ledger version) and stored in the encrypted SQLite database. Any import or Correction bumps the Ledger version, so a stale answer cannot be served. A text-similarity cache was rejected: 'spend in October' and 'spend in November' embed almost identically, and returning one for the other is an invented figure. Embedding similarity is used only to match wording to an intent. Redis was rejected because one user does not need a second server.

**Amended (#206): the key also holds the question.** Two different questions that make the same tool calls used to share one cached reply: the figures were right, but the prose might answer another question, or carry wording that text injected into one question had steered. The key now hashes the normalised question too (case-folded, whitespace collapsed); only the key's SHA-256 is stored, never the question. This lowers the hit rate to repeats of the same question, which is the case the cache is for. Caching only tool results and always regenerating the prose was rejected for now: every hit would then still wait on the model. Rows written under the old key simply never match again and are dropped by the first store after the next Ledger version change.
