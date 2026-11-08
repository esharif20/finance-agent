"""Verifier's adversarial property for #254: random synthetic ledgers (gaps,
refunds, short histories, rows after today) through signals and render.
Shown implies the condition holds, every number on a card is a slot or fact
value, and signals ignore row order and never hold a float."""

import random
import re
from datetime import date, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent.engine import playbook
from finance_agent.engine.detectors import Row

BOOK = playbook.load()
NUMBER = re.compile(r"-?£?\d[\d,]*(?:\.\d+)?")
CATEGORIES = [
    ("Subscriptions", "Streaming"),
    ("Bills", "Rent or mortgage"),
    ("Food & Drink", "Groceries"),
    ("Food & Drink", "Eating out"),
    ("Transport", "Fuel"),
    ("Shopping", "Clothes"),
]
START = date(2025, 1, 1)

rows_st = st.lists(
    st.tuples(
        st.integers(0, 700),  # days after START, so gaps of months happen
        st.sampled_from(CATEGORIES),
        st.integers(-500_000, 50_000),  # spending, with some Refunds
    ),
    max_size=60,
)


@settings(max_examples=300, deadline=None)
@given(
    spec=rows_st,
    savings=st.one_of(st.none(), st.integers(-100_000, 5_000_000)),
    today_offset=st.integers(0, 760),
    order=st.randoms(use_true_random=False),
    ramp=st.lists(st.integers(-2_000, 20_000), max_size=6),
)
def test_random_ledgers_never_show_a_false_card_or_an_unslotted_number(
    spec: list[tuple[int, tuple[str, str], int]],
    savings: int | None,
    today_offset: int,
    order: random.Random,
    ramp: list[int],
):
    today = START + timedelta(days=today_offset)
    rows = [
        Row(i, START + timedelta(days=d), "FICTIONAL CO", cat, amt)
        for i, (d, cat, amt) in enumerate(spec, 1)
    ]
    # Subscriptions in the months just before today's, often rising
    y, m = divmod(today.year * 12 + today.month - 1 - len(ramp), 12)
    for k, pence in enumerate(ramp):
        mm = m + k
        on = date(y + mm // 12, mm % 12 + 1, 15)
        rows.append(Row(1000 + k, on, "FICTIONAL TV", CATEGORIES[0], -pence))
    sigs = playbook.signals(rows, savings, today)
    shuffled = rows[:]
    order.shuffle(shuffled)
    assert playbook.signals(shuffled, savings, today) == sigs
    assert all(v is None or type(v) in (int, Decimal) for v in sigs.values())

    for card in BOOK.cards:
        for tone in playbook.TONES:
            r = playbook.render(card, sigs, BOOK.facts, tone, today)
            if r is None:
                continue
            assert playbook.holds(card.when, sigs)
            # every figure shown is the Engine's value, formatted
            for name, shown in r.figures:
                value = sigs[name]
                assert value is not None
                assert shown == playbook._shown(playbook.SIGNALS[name], value)  # pyright: ignore[reportPrivateUsage]
            allowed = {v for _, v in r.figures} | {f.value for f in r.facts}
            for n in NUMBER.findall(f"{r.title} {r.body}"):
                assert n in allowed, (n, r.title, r.body)
