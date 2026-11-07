# Finance Agent

A personal finance platform that keeps an auditable record of one person's money across their banks and turns it into evidence-backed insight.

## Ledger

**Account**:
Anything that holds a balance and can be the source or destination of a transfer: a current account, savings account, credit card, or a Revolut Pot.
_Avoid_: Wallet, pocket, bank (a Bank is the institution, not the Account)

**Bank**:
The institution that provides one or more Accounts, such as Lloyds, HSBC or Revolut.
_Avoid_: Provider, institution

**Statement Import**:
One file of rows exactly as a Bank exported it, kept unchanged as the evidence behind the Ledger.
_Avoid_: Upload, raw data, CSV

**Transaction**:
A single movement of money in or out of one Account, in its original amount and currency, derived from a Statement Import.
_Avoid_: Payment, entry, record

**Ledger**:
The complete set of Transactions across all Accounts; the single source of financial truth.
_Avoid_: Database, history

**Kind**:
What sort of money movement a Transaction is: Spending, Income, Transfer, Refund or Fee. Only Spending and Refund carry a Category.
_Avoid_: Type (the bank's own "Transaction Type" column is a different thing)

**Transfer**:
A Transaction moving money between two of the user's own Accounts; it is neither spending nor income.
_Avoid_: Internal payment, top-up

**Pending Transaction**:
A Transaction the Bank has not yet completed. Shown as provisional and excluded from every total until it completes; a reverted one is recorded but never counted.
_Avoid_: Uncleared, authorisation

**Reconciliation Gap**:
A difference between the balance a Bank reports for an Account and the balance the Ledger implies; the Bank's figure is treated as truth.
_Avoid_: Discrepancy, drift

**Possible Duplicate**:
A group of Transactions that may record the same movement of money, such as a CSV row and a synced row that cannot be paired with certainty. Each stays its own Transaction until the user decides.
_Avoid_: Conflict, clash

**Correction**:
A timestamped change the user makes on top of a Transaction (a new Category, a transfer link) that never alters the Transaction itself.
_Avoid_: Edit, override, fix

**Split**:
A Correction that divides one Transaction into 2–10 parts, each with its own amount and Category, adding up to the Transaction exactly. The Transaction itself is unchanged; a Split whose Transaction amount later changes needs re-checking and is never rescaled.
_Avoid_: Allocation, itemisation, sub-transaction

## Categorisation

**Category**:
A node in a small two-level spending taxonomy (such as Food & Drink → Groceries) that a Transaction belongs to; the unit charts and budgets are built on.
_Avoid_: Tag, label, bucket

**Merchant**:
The normalised party a Transaction is with (raw "TESCO STORES 3021" → Tesco). Never a Category; a Merchant has a default Category that a Correction can override per Transaction. Transfers between the user's own Accounts have no Merchant.
_Avoid_: Payee, vendor, shop, counterparty

**Review Queue**:
Every decision the app is waiting on the user for, one typed item each: a Merchant whose Category could not be assigned with enough confidence, a Possible Duplicate, or "is this a subscription?".
_Avoid_: Inbox, uncategorised

## Intelligence

**Insight**:
A typed finding produced by the Intelligence Engine (a price increase, a spending spike, an upcoming renewal), carrying a £ impact, a confidence and links to its evidence.
_Avoid_: Tip, alert, suggestion

**Intelligence Engine**:
The deterministic, non-LLM part of the system that detects patterns, forecasts and ranks Insights.
_Avoid_: AI, brain, model

**Assistant**:
The conversational part of the system: it interprets questions, calls read-only tools and explains results. It never computes a financial figure itself.
_Avoid_: Agent, bot, chatbot, AI

**Memory**:
A user-confirmed preference, goal or constraint the Assistant may rely on, with provenance and a review date. Never a financial fact.
_Avoid_: Profile, belief, fact

**Proposal**:
A change the Assistant suggests (a Memory or a Correction) that has no effect until the user confirms it.
_Avoid_: Draft, pending change
