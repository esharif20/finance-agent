"""Subscriptions from the first charge (#310): platform split, wallet
prefixes, per-amount series, the likely state, and re-keying a Ledger
categorised before the split without losing a decision. Synthetic only."""

from datetime import date, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from finance_agent import categorise
from finance_agent.categorise import normalise, platform
from finance_agent.categorise.subscriptions import judge, subscription_costs
from finance_agent.ledger import Categorisation, Ledger, Transaction
from test_review_queue import ledger_with

AI = ("subscription", "AI tools")


@pytest.mark.parametrize(
    ("desc", "merchant", "via"),
    [
        ("GOOGLE *Google One", "Google One", "Google"),
        ("GOOGLE *Colab", "Google Colab", "Google"),
        ("GOOGLE *Gemini", "Google AI Pro", "Google"),
        ("GOOGLE *CLOUD Q12X", "GOOGLE CLOUD", "Google"),  # its own Merchant
        ("GOOGLE *Google Play", "Google Play", "Google"),
        ("GOOGLE *AB1234", "Google", "Google"),  # only a ref: the platform
        ("APPLE.COM/BILL", "Apple", "Apple"),
        ("WL *VUE*GOOG", "Vue Cinemas", None),
        ("CRV*SPOTIFY", "Spotify", None),
        ("CURVE NETFLIX.COM", "Netflix", None),
        ("SQ *SYNTH CAFE", "SYNTH CAFE", "Square"),
        ("SP *SYNTH STORE", "SYNTH STORE", "Shopify"),
        ("PAYPAL *NETFLIX", "Netflix", "PayPal"),
        ("WL", "WL", None),  # a lone token is a name, never stripped to nothing
    ],
)
def test_platform_split_and_wallet_prefixes(
    desc: str, merchant: str, via: str | None
) -> None:
    assert (normalise(desc), platform(desc)) == (merchant, via)


def test_a_refund_from_a_store_product_is_still_a_refund() -> None:
    c = categorise.classify("GOOGLE *CLOUD Q12X", 500, "DEB", transfer=False)
    assert c.kind == "Refund"


def d(n: int) -> date:
    return date(2026, 1, 3) + timedelta(days=30 * n)


def test_likely_at_one_charge_active_at_a_second_in_the_same_band() -> None:
    one = judge("Google One", ["GOOGLE *Google One"], [(d(0), 479)])
    assert one and (one.status, one.group, one.charge_count) == (
        "likely", "Cloud storage", None,
    )  # fmt: skip
    two = judge("Google One", ["GOOGLE *Google One"] * 2, [(d(0), 479), (d(1), 479)])
    assert two and (two.status, two.charge_count, two.interval) == (
        "active", 2, "monthly",
    )  # fmt: skip
    # a second charge at another price is a one-off, not the series: still likely
    other = judge("Google One", ["x"], [(d(0), 479), (d(1), 1999)])
    assert other and other.status == "likely"


def test_a_one_off_between_charges_never_breaks_the_series() -> None:
    charges = [(d(i), 299) for i in range(3)] + [(d(1) + timedelta(3), 79)]
    x = judge("Apple", ["APPLE.COM/BILL"] * 4, charges)
    assert x and (x.status, x.charge_count) == ("active", 3)


def test_ai_services_are_in_the_ai_group() -> None:
    for name in ("Google AI Pro", "Google Colab", "Anthropic", "Cursor", "Perplexity"):
        x = judge(name, [name], [(d(0), 1800)])
        assert x and (x.kind, x.group) == AI, name


@pytest.mark.parametrize(
    "desc", ["SYNTHFIT PREMIUM", "BRIGHTAPP PRO", "QUILLNOTE PLUS", "ACME MONTHLY"]
)
def test_plan_words_make_an_unknown_merchant_likely(desc: str) -> None:
    x = judge(normalise(desc), [desc], [(d(0), 499)])
    assert x and (x.status, x.reason) == ("likely", "pattern")


@pytest.mark.parametrize(
    "desc", ["PRO CYCLES LTD", "PREMIUM FUELS", "PLUS SIZE OUTLET", "SYNTH BOOKS"]
)
def test_adversarial_a_shop_named_with_a_plan_word_is_not_a_guess(desc: str) -> None:
    assert judge(normalise(desc), [desc], [(d(0), 4999), (d(1), 4999)]) is None


def test_a_trial_authorisation_then_a_charge_is_likely() -> None:
    x = judge(
        "SYNTHSTREAM", ["SYNTHSTREAM"] * 2, [(d(0), 0), (d(0) + timedelta(7), 899)]
    )
    assert x and x.status == "likely"
    # an authorisation alone, or a charge long after it, is no trial
    assert judge("SYNTHSTREAM", ["SYNTHSTREAM"], [(d(0), 0)]) is None
    assert judge("SYNTHSTREAM", ["SYNTHSTREAM"] * 2, [(d(0), 100), (d(3), 899)]) is None


def test_one_service_paid_from_two_cards_still_activates() -> None:
    a, b = (
        [(d(i), 1099) for i in range(2)],
        [(d(i) + timedelta(3), 1099) for i in range(2)],
    )
    x = judge("Netflix", ["NETFLIX.COM"], a + b)
    assert x and x.status == "likely"  # interleaved: no cadence across both
    x = judge("Netflix", ["NETFLIX.COM"], a + b, accounts=[a, b])
    assert x and x.status == "active"


@given(st.lists(st.tuples(st.integers(0, 400), st.integers(0, 5000)), max_size=12))
def test_judging_never_depends_on_charge_order(rows: list[tuple[int, int]]) -> None:
    charges = [(date(2026, 1, 1) + timedelta(n), a) for n, a in rows]
    shuffled = list(reversed(charges))
    for name in ("Netflix", "SYNTH CO"):
        assert judge(name, ["X"], charges) == judge(name, ["X"], shuffled)


def test_dismissing_a_likely_guess_is_never_re_suggested() -> None:
    rows = [("GOOGLE *Google One", -479)]
    with ledger_with(rows) as (conn, led):
        (e,) = led.subscriptions()
        assert (e.merchant, e.status) == ("Google One", "likely")
        led.not_a_subscription(e.merchant_id)
        categorise.run(conn, 1)
        (e,) = led.subscriptions()
        assert e.status == "not_subscription"


def test_likely_costs_have_a_next_date_and_their_own_total() -> None:
    rows = [("SYNTHFIT PREMIUM", -499)]
    with ledger_with(rows) as (_, led):
        (e,) = led.subscriptions()
        assert e.status == "likely"
        t = led.transactions()[0]
        from finance_agent.categorise.subscriptions import Charge

        s = subscription_costs(
            [e], [Charge(e.merchant_id, t.posted_on, 499)], t.posted_on
        )
    (x,) = s.items
    assert (x.status, x.counted, x.interval, x.interval_source) == (
        "likely", False, "monthly", "estimated",
    )  # fmt: skip
    assert x.next_due_on is not None and x.next_due_on > t.posted_on
    assert s.likely_monthly == 499 and s.kind_totals == {}
    # a guess never charged again lapses rather than staying "new"
    later = subscription_costs(
        [e], [Charge(e.merchant_id, t.posted_on, 499)], t.posted_on + timedelta(90)
    )
    assert later.items[0].status == "lapsed" and later.likely_monthly == 0


# --- re-keying a Ledger categorised before the split -------------------------

SPLIT_ROWS = [
    ("GOOGLE *Google One", -479),
    ("GOOGLE *Colab", -999),
    ("GOOGLE *CLOUD Q12X", -120),
    ("GOOGLE *Google One", -479),
    ("WL *VUE*GOOG", -1450),
    ("SQ *SYNTH CAFE", -350),
    ("CRV*SPOTIFY", -1199),
]


def pre_split() -> pytest.MonkeyPatch:
    """The normaliser before #310: no prefix stripped, no store product."""

    def bare(words: list[str]) -> list[str]:
        return words

    def product(squashed: str) -> str | None:
        return None

    mp = pytest.MonkeyPatch()
    mp.setattr(categorise, "_bare", bare)
    mp.setattr(categorise, "_product", product)
    return mp


def by_txn(led: Ledger) -> list[tuple[Transaction, Categorisation]]:
    return [(t, t.categorisation) for t in led.transactions() if t.categorisation]


def by_id(led: Ledger) -> dict[int, Categorisation]:
    out: dict[int, Categorisation] = {}
    for t in led.transactions():
        assert t.categorisation is not None
        out[t.id] = t.categorisation
    return out


DECISIONS = st.lists(
    st.tuples(
        st.sampled_from(["merchant", "txn", "sub", "not"]),
        st.integers(0, len(SPLIT_ROWS) - 1),
    ),
    max_size=4,
)


@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(DECISIONS)
def test_the_split_never_drops_a_correction_or_decision(
    decisions: list[tuple[str, int]],
) -> None:
    old_normaliser = pre_split()
    with ledger_with(SPLIT_ROWS) as (conn, led):
        assert "GOOGLE" in {m.name.upper() for m in led.merchants()}  # one Merchant
        txns = sorted(led.transactions(), key=lambda t: t.id)
        for what, i in decisions:
            c = txns[i].categorisation
            assert c is not None and c.merchant_id is not None
            m = c.merchant_id
            if what == "merchant":
                led.correct_merchant(m, ("Bills", "Other bills"))
            elif what == "txn":
                led.correct_transaction(txns[i].id, ("Shopping", "Online"))
            elif what == "sub" and led.subscription(m) is None:
                led.add_subscription(m, "subscription", "Software")
            elif what == "not":
                led.not_a_subscription(m)
        categorise.run(conn, 1)
        old = by_id(led)
        entries = {e.merchant_id: e for e in led.subscriptions() if e.source == "user"}
        corrections = len(led.corrections())
        old_normaliser.undo()  # the new normaliser
        categorise.run(conn, 1)
        new = by_id(led)
        for tid, was in old.items():
            if was.source == "correction":  # a Correction still decides it
                assert (new[tid].category, new[tid].source) == (
                    was.category,
                    "correction",
                )
        assert len(led.corrections()) == corrections
        now = {e.merchant_id: e for e in led.subscriptions()}
        for m, e in entries.items():  # user registry decisions keep their Merchant
            assert now[m].status == e.status and now[m].source == "user"
        assert len({x.name for x in led.merchants()}) == len(led.merchants())
        categorise.run(conn, 1)
        assert by_id(led) == new  # re-running changes nothing


def test_a_fresh_ledger_splits_google() -> None:
    with ledger_with(SPLIT_ROWS) as (_, led):
        names = {t.description: c.merchant for t, c in by_txn(led)}
    assert names["GOOGLE *Google One"] == "Google One"
    assert names["WL *VUE*GOOG"] == "Vue Cinemas"
    assert names["GOOGLE *Colab"] == "Google Colab"
