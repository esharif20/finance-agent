"""Purpose notes (#124): at most 140 characters on a Transaction or a Split part.
Edits keep their history; a delete purges every version of that target; notes
are searchable and never logged."""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, event

from finance_agent.ledger import NOTE_MAX, InvalidNote, Ledger, UnknownTarget
from finance_agent.storage import schema
from test_review_queue import KNOWN, ledger_with

GROCERIES = ("Food & Drink", "Groceries")
GIFTS = ("Shopping", "Online")


def test_a_note_is_set_edited_and_its_history_kept() -> None:
    with ledger_with([(KNOWN, -1200)]) as (_, led):
        tid = led.transactions()[0].id
        assert led.note(tid) is None
        led.set_note(tid, "school shoes")
        led.set_note(tid, "school shoes and socks")
        live = led.note(tid)
        assert live is not None and live.text == "school shoes and socks"
        assert live.superseded_at is None
        history = led.note_history(tid)
        assert [n.text for n in history] == ["school shoes", "school shoes and socks"]
        assert history[0].superseded_at is not None


def test_a_delete_purges_every_version_of_that_target_only() -> None:
    with ledger_with([(KNOWN, -1200), (KNOWN, -300)]) as (conn, led):
        a, b = (t.id for t in led.transactions())
        led.set_note(a, "first")
        led.set_note(a, "second")
        led.set_note(b, "keep me")
        assert led.delete_note(a) == 2
        assert led.note(a) is None and led.note_history(a) == []
        rows = conn.execute(schema.note.select()).all()
        assert [r.text for r in rows] == ["keep me"]
        with pytest.raises(UnknownTarget):
            led.delete_note(a)


def test_a_split_part_has_its_own_note() -> None:
    with ledger_with([(KNOWN, -1200)]) as (_, led):
        tid = led.transactions()[0].id
        with pytest.raises(UnknownTarget):  # no Split yet
            led.set_note(tid, "gift", part=1)
        led.split_transaction(tid, [(-700, GROCERIES), (None, GIFTS)])
        led.set_note(tid, "the whole shop")
        led.set_note(tid, "birthday present", part=1)
        with pytest.raises(UnknownTarget):  # only two parts
            led.set_note(tid, "nope", part=2)
        part = led.note(tid, part=1)
        whole = led.note(tid)
        assert part is not None and part.text == "birthday present"
        assert whole is not None and whole.text == "the whole shop"
        led.delete_note(tid, part=1)
        assert led.note(tid, part=1) is None
        assert led.note(tid) == whole


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "x" * (NOTE_MAX + 1),
        "tab\there",
        "new\nline",
        "nul\x00byte",
        "bell\x07",
        "del\x7f",
        "c1\x85control",
        "bidi‮override",
        "line separator",
    ],
)
def test_bad_text_is_refused_and_never_logged(
    text: str, caplog: pytest.LogCaptureFixture
) -> None:
    secret = f"SYNTHSECRET{text}"[: NOTE_MAX + 1] if text.strip() else text
    with ledger_with([(KNOWN, -1200)]) as (_, led):
        tid = led.transactions()[0].id
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(InvalidNote) as e:
                led.set_note(tid, secret)
        assert led.note_history(tid) == []
    if secret.strip():
        assert secret not in str(e.value)
        assert "SYNTHSECRET" not in caplog.text


def test_exactly_the_limit_is_kept_and_emoji_count_as_characters() -> None:
    with ledger_with([(KNOWN, -1200)]) as (_, led):
        tid = led.transactions()[0].id
        led.set_note(tid, "é" * NOTE_MAX)
        led.set_note(tid, "🎁" * NOTE_MAX)
        live = led.note(tid)
        assert live is not None and live.text == "🎁" * NOTE_MAX


def test_another_users_transaction_is_not_found() -> None:
    with ledger_with([(KNOWN, -1200)]) as (conn, led):
        tid = led.transactions()[0].id
        led.set_note(tid, "mine")
        conn.execute(schema.user.insert().values(id=2, created_at=datetime.now(UTC)))
        theirs = Ledger(conn, 2)
        with pytest.raises(UnknownTarget):
            theirs.set_note(tid, "theirs")
        assert theirs.note(tid) is None and theirs.note_history(tid) == []
        with pytest.raises(UnknownTarget):
            theirs.delete_note(tid)
        assert theirs.search_transactions(query="mine").total == 0
        assert led.note_history(tid)[0].text == "mine"


def test_search_finds_a_transaction_by_its_live_note_only() -> None:
    with ledger_with([(KNOWN, -1200), (KNOWN, -300)]) as (_, led):
        a, b = (t.id for t in led.transactions())
        led.set_note(a, "Old Draft")
        led.set_note(a, "Football Boots")
        led.split_transaction(b, [(-100, GROCERIES), (None, GIFTS)])
        led.set_note(b, "boots polish", part=0)
        assert {t.id for t in led.search_transactions(query="boots").items} == {a, b}
        assert led.search_transactions(query="draft").total == 0
        led.delete_note(a)
        assert [t.id for t in led.search_transactions(query="boots").items] == [b]


@settings(max_examples=25, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.sampled_from([None, 0, 1]),
            st.text(
                st.characters(codec="utf-8", categories=["L", "N", "P", "Zs"]),
                min_size=1,
                max_size=NOTE_MAX,
            ).filter(str.strip),
        ),
        min_size=1,
        max_size=12,
    )
)
def test_the_latest_version_is_shown_and_history_is_ordered(
    edits: list[tuple[int | None, str]],
) -> None:
    with ledger_with([(KNOWN, -1200)]) as (_, led):
        tid = led.transactions()[0].id
        led.split_transaction(tid, [(-700, GROCERIES), (None, GIFTS)])
        for part, text in edits:
            led.set_note(tid, text, part=part)
        for part in (None, 0, 1):
            texts = [t for p, t in edits if p == part]
            history = led.note_history(tid, part=part)
            assert [n.text for n in history] == texts
            assert all(n.superseded_at is not None for n in history[:-1])
            stamps = [n.created_at for n in history]
            assert stamps == sorted(stamps)
            live = led.note(tid, part=part)
            assert (live and live.text) == (texts[-1] if texts else None)


def test_open_db_zeroes_deleted_content() -> None:
    """SQLCipher pages are encrypted on disk, but a freed page still holds the
    old text once decrypted (and the backup API copies free pages). SQLCipher
    builds with secure_delete on; this guards that a purge zeroes the page."""
    with ledger_with([]) as (conn, _):
        assert conn.exec_driver_sql("PRAGMA secure_delete").scalar() == 1


def test_a_purged_note_leaves_no_bytes_in_the_file(tmp_path: Path) -> None:
    """The same purge on a plaintext SQLite file with secure_delete on, as
    open_db has it: the text is gone from the file without a VACUUM. (The real
    ledger is SQLCipher, whose file never holds plaintext to search.)"""
    with ledger_with([(KNOWN, -1200)]) as (src, _):
        path = tmp_path / "plain.db"
        plain = create_engine(f"sqlite:///{path}")

        def secure(dbapi_conn: Any, _record: Any) -> None:
            dbapi_conn.execute("PRAGMA secure_delete = ON")

        event.listen(plain, "connect", secure)
        schema.metadata.create_all(plain)
        with plain.begin() as dst:
            for table in schema.metadata.sorted_tables:
                rows = [dict(m) for m in src.execute(table.select()).mappings()]
                if rows:
                    dst.execute(table.insert(), rows)
            led = Ledger(dst, 1)
            tid = led.transactions()[0].id
            for i in range(3):
                led.set_note(tid, f"SYNTHPURGE version {i}")
        assert b"SYNTHPURGE" in path.read_bytes()
        with plain.begin() as dst:
            Ledger(dst, 1).delete_note(tid)
        plain.dispose()
        assert b"SYNTHPURGE" not in path.read_bytes()
