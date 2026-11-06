---
area: backend
status: accepted
---
# A Transaction's identity includes the running balance

Bank CSV exports have no transaction ID, and Lloyds caps each export at about 150 rows, so overlapping exports are normal. The importer identifies a Transaction by (Account, date, description, amount, running balance). The running balance is what tells two identical same-day purchases apart. A row matching an existing Transaction is recorded as also present in the new Statement Import and is never imported twice. A Bank whose export lacks a balance column needs its own decision. Rows within one file are always distinct Transactions: a row only matches an existing Transaction not already linked to the same Statement Import, so a purchase, a refund and the same purchase again on one day (same key, same balance) stay two Transactions.

## Amendment (#34): rows are placed by sequence alignment

Matching on the key alone, with "not already linked to this import", keeps per key only the largest count seen in any one file. That drops a genuine Transaction when twins are split: export A ends with the first twin and the refund, export B starts with the refund and the second twin, and B's twin is taken as A's.

So position decides identity, not the key alone. Per Account, the stored Transactions are read as runs in Bank order (each Statement Import's rows, joined where imports share Transactions). A new file's rows, also in Bank order, are aligned against each run: only offsets where every overlapping row has the same key count, and the longest such overlap wins. B's refund then lines up with A's refund, so B's twin falls outside the overlap and is new.

Why this is sound: an export is a contiguous window of the Bank's history, so two exports share a contiguous block of rows, and that block lines up row for row. A row of the stored run outside the overlap is not in the new file. The running balance makes a false overlap rare: it needs a block of rows (same days, descriptions, amounts and balances) that repeats, which only happens when the rows between nets to zero on the same day, like purchase, refund, purchase. Preferring the longest overlap is the usual rule for joining fragments; across runs, a longer overlap takes priority over a shorter one for the same rows.

Identity uses only the raw Bank fields as exported (date, description, amount, running balance), read before any Merchant normalising, Category, Kind or rule runs. A hash over derived values breaks dedupe when a rule or normaliser changes (Firefly III issue #12246). Once categorisation lands, its tests must show that changing rules, the normaliser or the taxonomy never changes which rows match.

What is still ambiguous, and how it is handled:

- When two offsets give the same longest overlap (a file that is just one twin, or `[P R]` against a stored `P R P R`), the earliest offset is used and the rows are counted in `ImportReport.ambiguous`, which `fa import` prints. The issue (#34) first asked for such rows to be kept as new. That is wrong under the window model: whichever offset is right, the file's rows are already stored, so keeping them adds a duplicate twin. Picking one offset gives the right count and only risks linking the file to the wrong one of two identical twins. The count is still shown to the user, so nothing is merged silently.
- A block that repeats can fool the longest-overlap rule, and no method using the rows alone can do better. If the history has purchase, refund twice over (`P R P R P`), an export ending `P R P` and one starting `P R P x` fit with an overlap of three rows or of one, and both histories give the same files. The longest overlap is chosen, so one purchase and one refund are lost. Only a Bank transaction ID (or a statement balance) settles it. A single purchase, refund, purchase (`P R P`) is always placed correctly, because any wrong placement can only pair one twin with the other, and for a file of two or more rows that is always shorter than the true overlap.
- Two windows with a gap between them can line up by mistake on a twin at their edges if the rows between them came only in a third, unconnected import. The runs make this unlikely, because any import that covers the gap joins the runs.
- The balance chain (each row's balance minus its amount is the previous row's balance) is not used. It cannot tell twins apart, because twins have the same balance and the same amount.
