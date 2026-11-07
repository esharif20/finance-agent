---
area: backend
status: accepted
---
# Corrections overlay the Ledger; Transactions are never edited

Statement Imports and the Transactions derived from them are immutable. Every user change (recategorising, linking a transfer) is stored as a separate, timestamped Correction applied on top. This gives an audit trail, makes every change reversible, and turns the user's Corrections into labelled training data for categorisation. A mutable transactions table would be simpler but would destroy all three.

## Amendment (#123): a Split is a multi-part Correction

A Correction can now divide one Transaction into parts instead of giving it one Category. A Split Correction (kind `split`) holds 2–10 parts, each an integer amount in minor units and a Category. When it is made, the parts are non-zero, share the Transaction's sign and add up to it exactly; the last part may be left out and plain code computes it as the remainder (ADR 0007). The Transaction itself still never changes.

- **Undo and history.** A Split is reverted as a whole. Re-splitting reverts the live Split and writes a new one, so every earlier version stays in the history. An undo from the Review Queue writes a copy of the reverted Split rather than editing it.
- **Precedence.** A live Split beats any Category Correction on its Transaction. That Correction is kept and applies again once the Split is reverted.
- **Totals.** Category totals and the donut count each part in its own Category. Money in and money out read the Transaction amounts only, so no Split changes them.
- **Changed amounts.** If a Transaction's amount no longer equals the sum of its parts, the Split is `needs_recheck`. It counts whole under its largest part's Category and raises a Review Queue item. The parts are never rescaled: moving money between Categories is the user's decision.
- **Refunds (ADR 0004).** A Refund of a split purchase counts under the largest part's Category, still in the month it is received, until the user picks a part. That answer is an ordinary Category Correction on the Refund. Until a Refund link exists, the purchase is the latest split Spending of the same Merchant and Account on or before the Refund that is no smaller than it.
- **First-version limits.** Transfer-linked Transactions cannot be split, and parts never create Subscription registry entries.

## Amendment (#156): FX conversions are derived, never written onto a Transaction

A foreign-currency Transaction keeps its own amount and currency. Its GBP figure in totals is derived on every read from exactly one Rate: the Bank's own GBP amount on that Transaction, else the rate implied by the user's own exchanges that day (EXCHANGE legs linked as a Transfer; a day exchanging GBP with two currencies implies none), else the latest rate the user entered on or before its date. Rates are never fetched. With none, the Transaction is "rate needed": left out of every total and reported, never converted at a guess. Entered rates live in their own table and can be replaced; changing one bumps the ledger version. Split parts are converted by share with the remainder on the last, so Categories still add up to Money out exactly.
