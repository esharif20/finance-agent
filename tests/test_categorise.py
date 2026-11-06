"""Kind, Merchant and rule-based Category (ADR 0004, ADR 0005)."""

import pytest

from finance_agent import categorise


@pytest.mark.parametrize(
    ("descriptor", "merchant"),
    [
        ("TESCO STORES 3021", "Tesco"),
        ("TESCO EXP 0042", "Tesco"),
        ("TESCO STORES 3214 LONDON GB", "Tesco"),
        ("AMZNMKTPLACE*2K4LL9", "Amazon"),
        ("AMAZON.CO.UK*RT55X", "Amazon"),
        ("AMZ*MKTPLACE 7HD2", "Amazon"),
        ("PAYPAL *SPOTIFY", "Spotify"),
        ("PRET A MANGER LONDON", "Pret A Manger"),
        ("TFL.GOV.UK/CP", "TfL"),
        ("DELIVEROO.CO.UK", "Deliveroo"),
        ("UNHEARD-OF SHOP 99", None),
    ],
)
def test_messy_descriptors_map_to_one_merchant(
    descriptor: str, merchant: str | None
) -> None:
    assert categorise.merchant_for(descriptor) == merchant


def test_taxonomy_is_two_levels_with_an_uncategorised_bucket() -> None:
    assert 8 <= len(categorise.TAXONOMY) <= 12
    assert all(1 <= len(subs) <= 8 for subs in categorise.TAXONOMY.values())
    assert "Uncategorised" in categorise.TAXONOMY
    for _, category in categorise.MERCHANTS.values():
        assert category[1] in categorise.TAXONOMY[category[0]]


def c(
    description: str, amount_minor: int, bank_type: str = "DEB", transfer: bool = False
) -> categorise.Classification:
    return categorise.classify(description, amount_minor, bank_type, transfer=transfer)


def test_merchant_default_category_comes_first() -> None:
    got = c("TESCO STORES 3021", -1250)
    assert (got.kind, got.merchant, got.category) == (
        "Spending",
        "Tesco",
        ("Food & Drink", "Groceries"),
    )
    assert got.source == "merchant:Tesco"


def test_descriptor_rules_apply_when_no_merchant_matches() -> None:
    got = c("NCP CAR PARKING 0231", -800)
    assert (got.kind, got.merchant, got.category) == (
        "Spending",
        None,
        ("Transport", "Parking"),
    )
    assert got.source == "rule:parking"


def test_otherwise_spending_is_uncategorised() -> None:
    got = c("UNHEARD-OF SHOP 99", -800)
    assert (got.category, got.source) == (("Uncategorised", "Uncategorised"), "none")


@pytest.mark.parametrize(
    ("description", "amount", "bank_type", "transfer", "kind"),
    [
        ("TESCO STORES 3021", -100, "DEB", False, "Spending"),
        ("TESCO REFUND", 100, "DEB", False, "Refund"),
        ("UNHEARD-OF SHOP REFUND", 100, "DEB", False, "Refund"),
        ("ACME LTD SALARY", 250000, "FPI", False, "Income"),
        ("TO SYNTH SAVER 00000002", -500, "TFR", True, "Transfer"),
        ("TO A FRIEND", -500, "TFR", False, "Spending"),
        ("ARRANGED OVERDRAFT FEE", -500, "FEE", False, "Fee"),
        ("DEBIT INTEREST", -120, "CHG", False, "Fee"),
    ],
)
def test_every_transaction_gets_a_kind(
    description: str, amount: int, bank_type: str, transfer: bool, kind: str
) -> None:
    assert c(description, amount, bank_type, transfer).kind == kind


def test_only_spending_and_refunds_carry_merchant_and_category() -> None:
    for got in (
        c("ACME LTD SALARY", 250000, "FPI"),
        c("TO SYNTH SAVER 00000002", -500, "TFR", True),
        c("ARRANGED OVERDRAFT FEE", -500, "FEE"),
    ):
        assert (got.merchant, got.category) == (None, None)
    refund = c("AMAZON.CO.UK REFUND", 1999)
    assert (refund.merchant, refund.category) == ("Amazon", ("Shopping", "Online"))
