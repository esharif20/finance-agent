---
area: backend
status: accepted
---
# A Transaction's identity includes the running balance

Bank CSV exports have no transaction ID, and Lloyds caps each export at about 150 rows, so overlapping exports are normal. The importer identifies a Transaction by (Account, date, description, amount, running balance). The running balance is what tells two identical same-day purchases apart. A row matching an existing Transaction is recorded as also present in the new Statement Import and is never imported twice. A Bank whose export lacks a balance column needs its own decision.
