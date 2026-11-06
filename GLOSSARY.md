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

**Correction**:
A timestamped change the user makes on top of a Transaction (a new Category, a transfer link) that never alters the Transaction itself.
_Avoid_: Edit, override, fix
