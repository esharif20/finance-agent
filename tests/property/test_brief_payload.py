# ruff: noqa: S311  (seeded decoys, not secrets)
"""What the daily briefing sends (#309, ADR 0016): over random synthetic
ledgers with every suggested topic ticked, the request body carries no
ledger figure, no description and no Merchant text that is not a public
catalogue service; only topic names and decoys leave."""

import json
import random
import tempfile
from datetime import date, timedelta
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from finance_agent import categorise
from finance_agent.categorise.subscriptions import KNOWN_SERVICES
from finance_agent.cli import brief as cli_brief
from finance_agent.engine import topics
from finance_agent.gateway import brief
from finance_agent.gateway.cloud import CloudChatModel, FakeCloud
from finance_agent.ledger import Ledger
from finance_agent.storage import open_db
from finance_agent.storage.briefing import BriefingStore

TODAY = date(2026, 11, 8)
KEY = "fake-" + "x" * 8
# consonant-only words can never spell a catalogue service or a topic
word = st.text(alphabet="BCDFGHJKLMNPQRSTVWXZ", min_size=5, max_size=9)
SERVICES = ["NETFLIX.COM", "SPOTIFY", "OPENAI CHATGPT", "AUDIBLE UK", "ZOOM.US"]
row = st.tuples(
    st.integers(0, 85),  # days before TODAY: inside the 90-day window
    st.one_of(
        st.lists(word, min_size=1, max_size=3).map(" ".join),
        st.sampled_from(SERVICES),
        st.tuples(word, st.integers(1000, 99999)).map(lambda t: f"{t[0]} REF{t[1]}"),
    ),
    st.integers(1, 250_000),  # pence out
)
VOCAB = {*topics.SUBCATEGORY_TOPIC.values(), *KNOWN_SERVICES, *brief.DECOYS}


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(rows=st.lists(row, min_size=1, max_size=12), seed=st.integers(0, 2**16))
def test_no_ledger_figure_or_description_reaches_the_payload(
    rows: list[tuple[int, str, int]], seed: int
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "ledger.db"
        engine = open_db(db)
        try:
            csv = "".join(
                f"{TODAY - timedelta(days=d):%d/%m/%Y},{desc},-{p / 100:.2f}\n"
                for d, desc, p in rows
            )
            (Path(tmp) / "x.csv").write_text(csv)
            with engine.begin() as conn:
                Ledger(conn, 1).import_files([Path(tmp) / "x.csv"])
                categorise.run(conn, 1)
                suggested = topics.for_ledger(Ledger(conn, 1), TODAY)
            store = BriefingStore.beside(db)
            for t in suggested:
                store.set_topic(1, t.name, "on")  # the worst case: all ticked
            fake = FakeCloud(text="[]")
            cloud = CloudChatModel(KEY, transport=fake)
            cli_brief.refresh(1, engine, db, cloud, TODAY, random.Random(seed))
        finally:
            engine.dispose()
    if not suggested:
        assert fake.calls == []
        return
    ((_, _, body),) = fake.calls
    sent = json.loads(body)
    user_text = sent["contents"][0]["parts"][0]["text"]
    system = sent["systemInstruction"]["parts"][0]["text"]
    assert set(json.loads(user_text)) == {"topics"}
    names = json.loads(user_text)["topics"]
    assert set(names) <= VOCAB
    assert not any(ch.isdigit() for ch in user_text + system)  # no figure, any form
    whole = body.decode()
    for _, desc, p in rows:
        for w in desc.split():
            if not any(w.upper() in s for s in SERVICES):
                assert w not in whole and w.lower() not in whole.lower()
        assert f"{p // 100}.{p % 100:02d}" not in whole
