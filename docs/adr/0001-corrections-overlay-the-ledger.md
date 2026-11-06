# Corrections overlay the Ledger; Transactions are never edited

Statement Imports and the Transactions derived from them are immutable. Every user change (recategorising, linking a transfer) is stored as a separate, timestamped Correction applied on top. This gives an audit trail, makes every change reversible, and turns the user's Corrections into labelled training data for categorisation. A mutable transactions table would be simpler but would destroy all three.
