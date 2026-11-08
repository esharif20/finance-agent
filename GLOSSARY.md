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

**Conversion**:
A foreign-currency figure counted in GBP through exactly one Rate, which records its source and date. Derived on every read, never stored on the Transaction, which stays in its own currency.
_Avoid_: FX amount, converted transaction

**Rate**:
GBP per unit of a foreign currency, as a Decimal. Taken in order from the Bank's own GBP amount on the Transaction (`bank`), the user's own currency exchanges (`exchange`), or one the user entered (`user`); never fetched. With none, the figure is "rate needed": left out of every total and shown as such, never guessed.
_Avoid_: Exchange rate feed, FX quote

**Pending Transaction**:
A Transaction the Bank has not yet completed. Shown as provisional and excluded from every total until it completes; a reverted one is recorded but never counted.
_Avoid_: Uncleared, authorisation

**Reconciliation Gap**:
A difference between the balance a Bank reports for an Account and the balance the Ledger implies; the Bank's figure is treated as truth.
_Avoid_: Discrepancy, drift

**Possible Duplicate**:
A group of Transactions that may record the same movement of money, such as a CSV row and a synced row that cannot be paired with certainty. Each stays its own Transaction until the user decides. A merge drops a synced copy from every total as a reversible overlay (ADR 0014, #158); nothing is deleted.
_Avoid_: Conflict, clash

**Correction**:
A timestamped change the user makes on top of a Transaction (a new Category, a transfer link) that never alters the Transaction itself.
_Avoid_: Edit, override, fix

**Split**:
A Correction that divides one Transaction into 2–10 parts, each with its own amount and Category, adding up to the Transaction exactly. The Transaction itself is unchanged; a Split whose Transaction amount later changes needs re-checking and is never rescaled.
_Avoid_: Allocation, itemisation, sub-transaction

**Note**:
A purpose note of at most 140 characters on one Transaction or one part of its Split, saying what the payment was for. Edits keep the earlier versions; deleting a Note purges every version. It never sets a Category, is searchable, local only and never logged.
_Avoid_: Memo, comment, description (the Bank's text)

## Categorisation

**Category**:
A node in a small two-level spending taxonomy (such as Food & Drink → Groceries) that a Transaction belongs to; the unit charts and budgets are built on.
_Avoid_: Tag, label, bucket

**Merchant**:
The normalised party a Transaction is with (raw "TESCO STORES 3021" → Tesco). Never a Category; a Merchant has a default Category that a Correction can override per Transaction. Transfers between the user's own Accounts have no Merchant.
_Avoid_: Payee, vendor, shop, counterparty

**"Always ask"**:
A Merchant flag, set only by the user, for shops where a guessed Category is usually wrong. The categoriser never assigns a Category to its payments and never learns from them; each one is a "What was this?" Review Queue item and counts under **To review** until answered per payment (a Correction, a Split or both). Turning it on switches off that Merchant's Merchant-wide Correction; turning it off restores nothing. The app suggests it once a Merchant has been corrected to two or more different Categories.
_Avoid_: Ask mode, manual Merchant

**To review**:
The bucket in Category totals and the donut holding "Always ask" payments not yet answered. Not a Category; money-out still includes them.
_Avoid_: Pending, Uncategorised

**Review Queue**:
Every decision the app is waiting on the user for, one typed item each: a Merchant whose Category could not be assigned with enough confidence (or one such payment with no Merchant), a Possible Duplicate, "is this a subscription?", or "What was this?" for an "Always ask" payment.
_Avoid_: Inbox, uncategorised

**Registry entry**:
A Merchant's place in the subscription registry (#103): its kind (Subscription or Bill), group, status, reason and the evidence it rests on. The registry is append-only: each change adds a row, the latest row per Merchant is the entry, and the earlier rows are its history. A decision by the user always beats detection.
_Avoid_: Subscription record, listing

**Reason** (of a registry entry):
Why a Merchant is listed: **known_service** or **pattern** (detected), **user_added** (the user listed it by hand) or **user_confirmed** (the user said yes to a suggestion). Confirming an entry the user added keeps `user_added`: confirming changes the status, not the origin.
_Avoid_: Source, cause

**Negative entry** ("not a subscription"):
A registry entry recording that the user rejected a Merchant, by dismissing a suggestion or removing a listed one, including a Merchant that was never listed. It has no kind, group or reason, it is never deleted, and detection never suggests that Merchant again. The user can undo it.
_Avoid_: Blocklist, ignore

**Subscription status**:
Derived each time from a registry entry and its Merchant's last charge (#105): **active**; **overdue**, past the next expected charge plus a grace of 2 days (weekly), 5 (monthly) or 15 (annual); **lapsed**, two expected charges missed; **cancelled**, set only by the user; or **suggested**, still awaiting "is this a subscription?". Only active and overdue entries count in the monthly totals.
_Avoid_: Expired, inactive

**Monthly cost**:
A Subscription's or Bill's last charge in GBP as a monthly equivalent, in exact pence (weekly × 52 ÷ 12, annual ÷ 12), rounded half-even to the penny only when shown. Totals per group and per kind are the exact sums, rounded once. A foreign-currency charge is in GBP through the Ledger's one FX rate; without a rate it has no Monthly cost ("rate needed") and stays out of the totals, never guessed.

**Waste flag**:
A mark on a Subscription or Bill that may be wasting money (#106): **overlap**, two or more active Subscriptions in one group (not the catch-all Other subscriptions); **duplicate**, one service charged from two or more Accounts within a cadence gap; **charged after cancel**, a charge after the day the user cancelled it (a later regroup keeps that day); **trial converted**, a Free trial followed by a full charge; **price rise**, the latest price above the previous settled one (two or more charges, so a one-off is ignored) by more than ±5% (Bills too). Price rises and conversions older than a year are no longer flagged. Only "charged after cancel" fires on a cancelled item; no flag fires on a lapsed one.
_Avoid_: Warning, alert

**Free trial**:
A small first charge (£1 or less) from a known service that runs trials, with an expected conversion date from that service's usual trial length. Partial by design: read from posted charges only, so a trial that starts free stays unseen until the Mail signals (#80).
_Avoid_: Intro offer

## Hosted demo

**Demo**:
The public, hosted copy of the app (ADR 0016): a separate process that runs on synthetic data only and never touches the user's real Ledger.
_Avoid_: Sandbox, staging

**Visitor**:
Someone using the Demo. Anonymous by default, identified only by a signed cookie holding a visitor id and a Persona; each Visitor has their own ephemeral synthetic Ledger.
_Avoid_: User (the user is the owner of the real Ledger), guest

**Persona**:
A named synthetic financial profile a Visitor picks in the Demo, which decides the Accounts and Transactions generated for them.
_Avoid_: Character, template, role

## Intelligence

**Insight**:
A typed finding produced by the Intelligence Engine (a price increase, a spending spike, an upcoming renewal), carrying a £ impact, a confidence and links to its evidence.
_Avoid_: Tip, alert, suggestion

**Intelligence Engine**:
The deterministic, non-LLM part of the system that detects patterns, forecasts and ranks Insights.
_Avoid_: AI, brain, model

**Playbook card**:
A short guidance card written as repo data: a topic, principles, gentle and blunt wording, and a "when this applies" condition the Intelligence Engine evaluates on the user's data. Its figures fill named slots from Engine results or a Fact, never typed into the card. It shows only while its condition holds.
_Avoid_: Tip, article, knowledge base

**Fact**:
A statutory figure, such as the ISA allowance, kept in one facts file in the repo with its source and a "checked on" date that is always shown. Never fetched. One checked before the current tax year began shows "may be out of date".
_Avoid_: Rule, constant

**Assistant**:
The conversational part of the system: it interprets questions, calls read-only tools and explains results. It never computes a financial figure itself.
_Avoid_: Agent, bot, chatbot, AI

**Memory**:
A user-confirmed preference, goal or constraint the Assistant may rely on, with provenance and a review date. Never a financial fact.
_Avoid_: Profile, belief, fact

**Proposal**:
A change the Assistant suggests (a Memory or a Correction) that has no effect until the user confirms it.
_Avoid_: Draft, pending change
