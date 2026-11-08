"""The Review Queue (#110): every decision the app needs from the user, one
typed item each, user-scoped and ordered so each answer fixes the most.

Items are derived on every read from their producers, never stored; only the
user's answers and skips are (`review_decision`, append-only). A type is an
`ItemType` in `ITEM_TYPES`: it lists its items, applies an answer and undoes
it. Later features (splits, "Always ask") add a type there and nothing else.

An answer whose effect resolves the item (a Correction, a registry decision)
removes it through that effect; an answer that is only a record ("keep both")
hides the item while its inputs hash to the answered version. A skip sends the
item to the end until its inputs change, and changes no figure, so it never
bumps the ledger version (the answer cache's key, #140). Nothing here changes
an amount, so queued items count in every figure.
"""

import hashlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import Connection

from finance_agent.ledger import (
    CATEGORISED_KINDS,
    UNCATEGORISED,
    Ledger,
    Transaction,
    UnknownTarget,
)
from finance_agent.storage import Scoped, schema


class StaleItem(ValueError):
    """The item's inputs changed since it was shown; show it again first."""


class InvalidAnswer(ValueError):
    """An answer this item's type does not take."""


class AlreadyReverted(Exception):
    """Undo found the answer's effect already reverted elsewhere."""


@dataclass(frozen=True)
class Item:
    type: str
    key: str  # stable within the type: a Merchant id, "t<id>", or row ids
    version: str  # hash of the inputs; a decision holds while it matches
    weight: int  # Transactions an answer touches: all the Merchant's rows
    title: str = ""  # the Merchant's name, if any
    merchant_id: int | None = None
    transaction_ids: tuple[int, ...] = ()
    choices: tuple[str, ...] = ()  # empty: the answer is a Category
    # up to 3 Categories a local model proposes (#114); only ever buttons, and
    # not part of `version`, so a model refresh never invalidates a decision
    suggestions: tuple[tuple[str, str], ...] = ()
    skipped: bool = False

    @property
    def id(self) -> str:
        return f"{self.type}:{self.key}"


@dataclass(frozen=True)
class Answer:
    category: tuple[str, str] | None = None
    transaction_id: int | None = None  # "just this one"; else the whole Merchant
    choice: str | None = None


class ItemType(Protocol):
    name: str

    def items(self, led: Ledger, txns: Sequence[Transaction]) -> Iterable[Item]: ...

    def answer(self, led: Ledger, item: Item, answer: Answer) -> int | None:
        """Apply the answer; returns the id of what it wrote, if anything."""
        ...

    def undo(self, led: Ledger, effect_id: int) -> None: ...


def _version(*parts: object) -> str:
    return hashlib.sha256(repr(parts).encode()).hexdigest()


class CategoryItems:
    """A Merchant whose Spending or Refunds are still Uncategorised, or one
    such Transaction with no Merchant (keyed "t<id>")."""

    name = "category"

    def items(self, led: Ledger, txns: Sequence[Transaction]) -> Iterable[Item]:
        rows: dict[int, list[int]] = {}
        names: dict[int, str] = {}
        counts: dict[int, int] = {}  # every row of the Merchant, for `weight`
        for t in txns:
            c = t.categorisation
            if c is not None and c.merchant_id is not None:
                counts[c.merchant_id] = counts.get(c.merchant_id, 0) + 1
            if (
                c is not None
                and c.kind in CATEGORISED_KINDS
                and c.category == (UNCATEGORISED, UNCATEGORISED)
                # the user chose Uncategorised, or split it (its own items)
                and c.source not in ("correction", "split", "split_refund")
            ):
                if c.merchant_id is None:
                    yield Item(
                        self.name, f"t{t.id}", _version(t.id), 1, "", None, (t.id,)
                    )
                    continue
                rows.setdefault(c.merchant_id, []).append(t.id)
                names[c.merchant_id] = c.merchant or ""
        for m, ids in rows.items():
            ids.sort()
            yield Item(
                self.name, str(m), _version(ids), counts[m], names[m], m, tuple(ids)
            )

    def answer(self, led: Ledger, item: Item, answer: Answer) -> int | None:
        if answer.category is None or answer.choice is not None:
            raise InvalidAnswer("a Category item takes a Category")
        cat = (answer.category[0], answer.category[1])
        if answer.transaction_id is None and item.merchant_id is not None:
            return led.correct_merchant(item.merchant_id, cat).id
        txn = answer.transaction_id
        if txn is None and item.merchant_id is None:
            (txn,) = item.transaction_ids  # a row with no Merchant: just this one
        if txn not in item.transaction_ids:
            raise InvalidAnswer("not one of this item's Transactions")
        return led.correct_transaction(txn, cat).id

    def undo(self, led: Ledger, effect_id: int) -> None:
        try:
            led.revert_correction(effect_id)
        except UnknownTarget:
            # already the state undo wants, but the user should know why
            raise AlreadyReverted from None


class SubscriptionItems:
    """ "Is this a subscription?": a detection the user has not decided."""

    name = "subscription"

    def items(self, led: Ledger, txns: Sequence[Transaction]) -> Iterable[Item]:
        counts: dict[int, int] = {}
        for t in txns:
            if t.categorisation and t.categorisation.merchant_id is not None:
                m = t.categorisation.merchant_id
                counts[m] = counts.get(m, 0) + 1
        for e in led.subscriptions():
            if e.status == "suggested" and e.source == "detection":
                yield Item(
                    self.name,
                    str(e.merchant_id),
                    _version(e.kind, e.group, e.changed_at.isoformat()),
                    counts.get(e.merchant_id, 0),
                    e.merchant,
                    e.merchant_id,
                    choices=("yes", "no"),
                )

    def answer(self, led: Ledger, item: Item, answer: Answer) -> int | None:
        if (
            item.merchant_id is None
            or answer.transaction_id is not None
            or (answer.category is not None)
        ):
            raise InvalidAnswer("a subscription item takes yes or no")
        if answer.choice == "yes":
            return led.confirm_subscription(item.merchant_id)
        if answer.choice == "no":
            return led.not_a_subscription(item.merchant_id)
        raise InvalidAnswer("a subscription item takes yes or no")

    def undo(self, led: Ledger, effect_id: int) -> None:
        led.revert_subscription_decision(effect_id)


class DuplicateItems:
    """A Possible Duplicate (ADR 0014). "keep_both" is recorded; merging needs
    the ADR's duplicate Correction, a later ticket."""

    name = "possible_duplicate"

    def items(self, led: Ledger, txns: Sequence[Transaction]) -> Iterable[Item]:
        groups = {tuple(sorted(g)) for g in led.possible_duplicates()}
        for ids in groups:
            key = ",".join(map(str, ids))
            yield Item(
                self.name,
                key,
                _version(ids),
                len(ids),
                transaction_ids=ids,
                choices=("keep_both",),
            )

    def answer(self, led: Ledger, item: Item, answer: Answer) -> int | None:
        if answer != Answer(choice="keep_both"):
            raise InvalidAnswer("a Possible Duplicate takes keep_both")
        return None

    def undo(self, led: Ledger, effect_id: int) -> None:
        pass  # keep_both writes nothing


def _merchants(txns: Sequence[Transaction]) -> dict[int, tuple[int | None, str]]:
    return {
        t.id: (c.merchant_id, c.merchant or "")
        for t in txns
        if (c := t.categorisation) is not None
    }


class SplitRecheckItems:
    """A Split whose Transaction amount changed, so its parts no longer add up
    (#123). It counts whole under its largest part until re-split (through the
    Splits API) or reverted here; parts are never rescaled."""

    name = "split_recheck"

    def items(self, led: Ledger, txns: Sequence[Transaction]) -> Iterable[Item]:
        names = _merchants(txns)
        for s in led.splits():
            if s.needs_recheck:
                merchant_id, title = names.get(s.transaction_id, (None, ""))
                yield Item(
                    self.name,
                    str(s.id),
                    _version(s.id, s.amount_minor),
                    1,
                    title,
                    merchant_id,
                    (s.transaction_id,),
                    choices=("revert",),
                )

    def answer(self, led: Ledger, item: Item, answer: Answer) -> int | None:
        if answer != Answer(choice="revert"):
            raise InvalidAnswer("a re-check item takes revert; re-split instead")
        return led.revert_split(item.transaction_ids[0])

    def undo(self, led: Ledger, effect_id: int) -> None:
        led.restore_split(effect_id)


class SplitRefundItems:
    """ "Which part?": a Refund of a split purchase (#123), counted under the
    largest part until answered with one of the parts' Categories, which
    becomes a Correction on the Refund."""

    name = "split_refund"

    def items(self, led: Ledger, txns: Sequence[Transaction]) -> Iterable[Item]:
        for t in txns:
            c = t.categorisation
            if c is not None and c.refund_of is not None:
                split = led.split(c.refund_of)
                yield Item(
                    self.name,
                    str(t.id),
                    _version(c.refund_of, split.id if split else None),
                    1,
                    c.merchant or "",
                    c.merchant_id,
                    (t.id, c.refund_of),  # the Refund, then the split purchase
                )

    def answer(self, led: Ledger, item: Item, answer: Answer) -> int | None:
        refund, purchase = item.transaction_ids
        split = led.split(purchase)
        parts = {p.category for p in (split.parts if split else ())}
        if (
            answer.category is None
            or answer.choice is not None
            or answer.transaction_id not in (None, refund)
        ):
            raise InvalidAnswer("a Which part? item takes one part's Category")
        cat = (answer.category[0], answer.category[1])
        if cat not in parts:
            raise InvalidAnswer("not one of the split's parts")
        return led.correct_transaction(refund, cat).id

    def undo(self, led: Ledger, effect_id: int) -> None:
        CategoryItems().undo(led, effect_id)


class AlwaysAskItems:
    """ "What was this?": one payment at an "Always ask" Merchant (#125), which
    counts under To review until answered with a Category for that payment
    only (a per-Transaction Correction), or split or corrected elsewhere."""

    name = "always_ask"

    def items(self, led: Ledger, txns: Sequence[Transaction]) -> Iterable[Item]:
        for t in txns:
            c = t.categorisation
            if c is not None and c.source == "always_ask":
                yield Item(
                    self.name,
                    str(t.id),
                    _version(t.id, c.merchant_id),
                    1,
                    c.merchant or "",
                    c.merchant_id,
                    (t.id,),
                )

    def answer(self, led: Ledger, item: Item, answer: Answer) -> int | None:
        (txn,) = item.transaction_ids
        if (
            answer.category is None
            or answer.choice is not None
            or answer.transaction_id not in (None, txn)
        ):
            raise InvalidAnswer("a What was this? item takes this payment's Category")
        return led.correct_transaction(txn, (answer.category[0], answer.category[1])).id

    def undo(self, led: Ledger, effect_id: int) -> None:
        CategoryItems().undo(led, effect_id)


ITEM_TYPES: dict[str, ItemType] = {
    t.name: t
    for t in (
        CategoryItems(),
        SubscriptionItems(),
        DuplicateItems(),
        SplitRecheckItems(),
        SplitRefundItems(),
        AlwaysAskItems(),
    )
}

# (item key -> Merchant id, Merchant id -> its corrected Categories) -> item key
# -> suggested Categories. Injected by the API so the Ledger never imports
# `categorise`.
Suggester = Callable[
    [dict[str, int], dict[int, set[tuple[str, str]]]], dict[str, list[tuple[str, str]]]
]

# money modules take no float: a later scorer (information gain) returns a Decimal
Scorer = Callable[[Item], int | Decimal]


def by_frequency(item: Item) -> int:
    """Until the categoriser exists (ADR 0015): the most Transactions first."""
    return item.weight


def order(items: Iterable[Item], scorer: Scorer = by_frequency) -> list[Item]:
    """Unskipped first, highest score first; ties by type and key, so the
    order never depends on how the producers listed them."""
    return sorted(items, key=lambda i: (i.skipped, -scorer(i), i.type, i.key))


class ReviewQueue:
    def __init__(
        self,
        conn: Connection,
        user_id: int,
        scorer: Scorer = by_frequency,
        suggester: Suggester | None = None,
    ) -> None:
        self._led = Ledger(conn, user_id)
        self._db = Scoped(conn, user_id)
        self._scorer = scorer
        self._suggester = suggester

    def items(self) -> list[Item]:
        txns = self._led.transactions()
        held = self._decisions()
        out: list[Item] = []
        for t in ITEM_TYPES.values():
            for i in t.items(self._led, txns):
                action = held.get((i.type, i.key, i.version))
                if action == "answer":
                    continue
                out.append(replace(i, skipped=action == "skip"))
        return order(self._suggest(out, txns), self._scorer)

    def _suggest(self, items: list[Item], txns: Sequence[Transaction]) -> list[Item]:
        """Fill `suggestions` on Category items; a Correction is the only thing
        that ever assigns, so this only reads."""
        if self._suggester is None:
            return items
        targets = {
            i.key: i.merchant_id
            for i in items
            if i.type == CategoryItems.name and i.merchant_id is not None
        }
        if not targets:
            return items
        labelled: dict[int, set[tuple[str, str]]] = {}
        for t in txns:
            c = t.categorisation
            if (
                c is not None
                and c.merchant_id is not None
                and c.source == "correction"
                and c.category is not None
                and c.category != (UNCATEGORISED, UNCATEGORISED)
            ):
                cat = (c.category[0], c.category[1])
                labelled.setdefault(c.merchant_id, set()).add(cat)
        found = self._suggester(targets, labelled)
        return [
            replace(i, suggestions=tuple(found[i.key]))
            if i.type == CategoryItems.name and i.key in found
            else i
            for i in items
        ]

    def version(self) -> int:
        return self._led.ledger_version()

    def answer(self, item_id: str, version: str, answer: Answer) -> None:
        item = self._current(item_id, version)
        effect = ITEM_TYPES[item.type].answer(self._led, item, answer)
        self._record(item, "answer", effect)

    def skip(self, item_id: str, version: str) -> None:
        self._record(self._current(item_id, version), "skip", None)

    def undo(self) -> str | None:
        """Undo the latest answer or skip that is not undone yet. Returns a
        message when the answer's Correction was already reverted elsewhere."""
        d = schema.review_decision
        r = self._db.execute(
            self._db.select(d, d.c.id, d.c.item_type, d.c.action, d.c.effect_id)
            .where(d.c.undone_at.is_(None))
            .order_by(d.c.id.desc())
            .limit(1)
        ).first()
        if r is None:
            raise UnknownTarget("nothing to undo")
        message = None
        if r.action == "answer" and r.effect_id is not None:
            try:
                ITEM_TYPES[r.item_type].undo(self._led, r.effect_id)
            except AlreadyReverted:
                message = "That answer was already undone elsewhere; nothing changed."
        self._db.execute(
            self._db.update(d).where(d.c.id == r.id).values(undone_at=datetime.now(UTC))
        )
        if r.action == "answer":
            self._db.bump_ledger_version()
        return message

    def _current(self, item_id: str, version: str) -> Item:
        item = next((i for i in self.items() if i.id == item_id), None)
        if item is None:
            raise UnknownTarget("no such item in the queue")
        if item.version != version:
            raise StaleItem("the item changed since it was shown")
        return item

    def _decisions(self) -> dict[tuple[str, str, str], str]:
        """(type, key, version) -> the latest decision still in force. An
        answer with an effect resolves through that effect instead, so one
        reverted elsewhere brings its item back."""
        d = schema.review_decision
        rows = self._db.execute(
            self._db.select(
                d,
                d.c.item_type,
                d.c.item_key,
                d.c.item_version,
                d.c.action,
                d.c.effect_id,
            )  # fmt: skip
            .where(d.c.undone_at.is_(None))
            .order_by(d.c.id)
        )
        out: dict[tuple[str, str, str], str] = {}
        for r in rows:
            if r.action == "skip" or r.effect_id is None:
                out[(r.item_type, r.item_key, r.item_version)] = r.action
        return out

    def _record(self, item: Item, action: str, effect: int | None) -> None:
        self._db.insert(
            schema.review_decision,
            item_type=item.type,
            item_key=item.key,
            item_version=item.version,
            action=action,
            effect_id=effect,
            created_at=datetime.now(UTC),
        )
        if action == "answer":
            self._db.bump_ledger_version()
