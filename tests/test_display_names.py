"""Readable display names for raw Merchant text (#210 follow-up). Display only:
stored descriptions and Merchant names never change. Synthetic strings only."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance_agent.categorise.display_names import CATALOGUE_NAMES, display_name


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        # payee and description joined, the second a repeat (SimpleFIN _text)
        ("ANTHROPIC CLAUDE SUB ANTHROPIC CLAUDE", "Claude (Anthropic)"),
        ("OPENAI CHATGPT SUBSCR OPENAI CHATGPT SU", "ChatGPT (OpenAI)"),
        ("SYNTHCO STREAM SUBSCR SYNTHCO STREAM SU", "Synthco Stream"),
        ("SYNTH TOOLS SUB SYNTH TOOLS", "Synth Tools"),
        # a cut-off last word
        ("SYNTHCO UNDERWRITIN", "Synthco"),
        ("SYNTH WIDGETS SYNTH WIDG", "Synth Widgets"),
        # filler, references and acronyms
        ("SYNTH WIDGETS LTD PAYMENT 48293017", "Synth Widgets"),
        ("JD SYNTH UK LIMITED", "JD Synth UK"),
        ("HMRC SELF ASSESSMENT", "HMRC Self Assessment"),
        ("SYNTH GYM MEMBERSHIP", "Synth Gym Membership"),
        # the catalogue's own names win
        ("NETFLIX.COM", "Netflix"),
        ("1PASSWORD", "1Password"),
        ("EE LIMITED", "EE"),
        ("DISNEY PLUS", "Disney Plus"),
        # already readable text is left alone
        ("Synth Bakery", "Synth Bakery"),
        ("iSynth", "iSynth"),
        # all filler: kept rather than emptied
        ("SUB", "Sub"),
    ],
)
def test_examples(raw: str, shown: str) -> None:
    assert display_name(raw) == shown


raw_names = st.lists(
    st.one_of(
        st.sampled_from(
            ["SUB", "SUBSCR", "LTD", "PAYMENT", "CARD", "SU", "UK", "ANTHROPIC",
             "CLAUDE", "OPENAI", "CHATGPT", "NETFLIX.COM", "SYNTH", "SYNTHCO",
             "48293017", "AB12CD", "Synth", "365", "O2"]
        ),
        st.text(min_size=1, max_size=8).filter(lambda s: s.strip() == s and s != ""),
    ),
    min_size=1,
    max_size=8,
).map(" ".join).filter(lambda s: s.strip() != "")  # fmt: skip


@given(raw_names)
def test_never_empty_never_longer_and_stable(raw: str) -> None:
    shown = display_name(raw)
    assert shown.strip()
    # a catalogue name may be longer than the raw text ("ANTHROPIC" is
    # "Claude (Anthropic)"); anything else only loses characters
    assert len(shown) <= len(raw) or shown in CATALOGUE_NAMES
    assert display_name(shown) == shown
