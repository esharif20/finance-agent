"""The cost meter (#295, ADR 0013): a bucket's spend plus open reservations
never passes its cap, whatever the interleaving, across two store handles on
one file (two processes)."""

import tempfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent.gateway import cost
from finance_agent.gateway.cloud import Usage
from finance_agent.storage.spend import NotOpen, SpendStore

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)
FLASH = "gemini-flash-latest"

# (kind, bucket or held index, max_in or settle percent, max_out)
op = st.tuples(
    st.sampled_from(["reserve", "settle", "cancel"]),
    st.integers(0, 50),
    st.integers(0, 60_000),
    st.integers(0, 30_000),
)
BUCKETS = sorted(cost.CAPS)


def committed(store: SpendStore) -> dict[str, int]:
    return {s.bucket: s.spent_pence + s.reserved_pence for s in store.month_status(NOW)}


@settings(max_examples=60, deadline=None)
@given(ops=st.lists(st.tuples(st.booleans(), op), max_size=40))
def test_spend_plus_open_never_exceeds_cap(
    ops: list[tuple[bool, tuple[str, int, int, int]]],
) -> None:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "spend.sqlite"
        stores = SpendStore(path), SpendStore(path)
        held: list[tuple[cost.Reservation, int, int]] = []
        for which, (kind, n, a, b) in ops:
            s = stores[which]
            if kind == "reserve":
                r = s.reserve(BUCKETS[n % 2], FLASH, a, b, now=NOW)
                if not isinstance(r, cost.Denied):
                    held.append((r.reservation, a, b))
            elif held:
                r, max_in, max_out = held.pop(n % len(held))
                if kind == "settle":
                    pct = a % 101
                    # usage within the reservation: in, out and thinking split max_out
                    out = max_out * pct // 100
                    u = Usage(max_in * pct // 100, out // 2, out - out // 2, "x")
                    s.settle(r, u, now=NOW)
                    with pytest.raises(NotOpen):
                        s.settle(r, u, now=NOW)  # never twice
                else:
                    s.cancel(r)
            for b, c in committed(s).items():
                assert c <= cost.CAPS[b]


def test_overspend_is_recorded_and_the_next_reserve_is_denied(tmp_path: Path) -> None:
    s = SpendStore(tmp_path / "spend.sqlite")
    r = s.reserve("gateway_chat", FLASH, 100, 100, now=NOW)
    assert isinstance(r, cost.Allowed) and r.reservation.pence == 1
    # a reply far past its reservation (thinking tokens): 200k thinking ~ £1.28
    paid = s.settle(r.reservation, Usage(100, 1, 200_000, "gemini-3.8-flash"), NOW)
    assert paid > cost.CAPS["gateway_chat"]
    [chat] = [b for b in s.month_status(NOW) if b.bucket == "gateway_chat"]
    assert chat.spent_pence == paid and chat.state == "capped"
    assert isinstance(s.reserve("gateway_chat", FLASH, 1, 1, now=NOW), cost.Denied)
    # the other bucket is untouched
    assert isinstance(s.reserve("synthetic_judge", FLASH, 1, 1, now=NOW), cost.Allowed)


def test_month_rolls_over_on_the_first_in_utc(tmp_path: Path) -> None:
    s = SpendStore(tmp_path / "spend.sqlite")
    late = datetime(2026, 11, 30, 23, 59, tzinfo=UTC)
    r = s.reserve("gateway_chat", FLASH, 0, 130_000, now=late)
    assert isinstance(r, cost.Warn)  # 83p of £1: past 80% is a warning
    s.settle(r.reservation, Usage(0, 0, 160_000, "x"), now=late)
    assert isinstance(s.reserve("gateway_chat", FLASH, 1, 1, now=late), cost.Denied)
    # 00:30 on 1 Dec in UTC+1 is still 30 Nov in UTC
    plus1 = datetime(2026, 12, 1, 0, 30, tzinfo=timezone(timedelta(hours=1)))
    assert isinstance(s.reserve("gateway_chat", FLASH, 1, 1, now=plus1), cost.Denied)
    dec = datetime(2026, 12, 1, 0, 0, tzinfo=UTC)
    assert isinstance(s.reserve("gateway_chat", FLASH, 1, 1, now=dec), cost.Allowed)


def test_unknown_models_and_oversized_pro_prompts_are_refused(tmp_path: Path) -> None:
    s = SpendStore(tmp_path / "spend.sqlite")
    with pytest.raises(cost.UnknownModel):
        s.reserve("gateway_chat", "gemini-9-ultra", 1, 1, now=NOW)
    with pytest.raises(cost.UnknownModel):
        s.reserve("synthetic_judge", "gemini-pro-latest", 200_001, 1, now=NOW)
    with pytest.raises(ValueError):
        s.reserve("demo_domain", FLASH, 1, 1, now=NOW)
    assert all(b.spent_pence == b.reserved_pence == 0 for b in s.month_status(NOW))


def test_prices_round_up_and_bill_thinking_as_output() -> None:
    assert cost.pence(FLASH, 0, 0) == 0
    assert cost.pence(FLASH, 1, 0) == 1  # any use is at least a penny
    # 1M output tokens on Flash: $7.50 * 0.85 = £6.375 -> 638p
    assert cost.pence(FLASH, 0, 1_000_000) == 638
    r = cost.Reservation("gateway_chat", FLASH, "2026-11", 1)
    assert cost.settle(r, Usage(0, 0, 1_000_000, "x")) == 638
    # a pricier modelVersion than the alias is charged at the higher price
    assert cost.settle(r, Usage(0, 1_000_000, 0, "gemini-3.1-pro-preview")) == 1020
