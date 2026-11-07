"""#173: every path that lands new rows (`fa sync`, POST /api/sync, the watched
folder) categorises them and recognises subscriptions, as `fa import` does.
Synthetic rows; fake transport and in-memory Keychain."""

import dataclasses
import logging
import secrets
from collections.abc import Iterator
from pathlib import Path

import keyring
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.cli import LOCAL_USER, main, watch
from finance_agent.ingest import simplefin
from finance_agent.ledger import Ledger
from finance_agent.storage import keychain, open_db
from synthetic_lloyds import generate_histories, render_export
from synthetic_simplefin import response

SERVICE = keychain.SIMPLEFIN_ACCESS_SERVICE
_H = generate_histories(3, 30)[0]
# row 0 is a known service, row 1 a Merchant with a default Category
H = dataclasses.replace(
    _H,
    txns=[
        dataclasses.replace(_H.txns[0], description="NETFLIX.COM", amount_minor=-1099),
        dataclasses.replace(
            _H.txns[1], description="TESCO STORES 1234", amount_minor=-2500
        ),
        *_H.txns[2:],
    ],
)


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "db" / "ledger.db"))
    access = f"https://{secrets.token_hex(6)}:{secrets.token_hex(12)}@{simplefin.HOST}/simplefin"
    body = response(H, 0, 10)

    def transport(
        method: str, url: str, headers: dict[str, str], data: bytes | None
    ) -> tuple[int, bytes]:
        return (200, body)

    monkeypatch.setattr(simplefin, "transport", transport)
    eng = open_db()
    keychain.set_text(SERVICE, access)
    yield eng
    keyring.delete_password(SERVICE, keychain.ACCOUNT)
    eng.dispose()


def check(engine: Engine, caplog: pytest.LogCaptureFixture) -> None:
    with engine.begin() as conn:
        led = Ledger(conn, LOCAL_USER)
        subs = {s.merchant.upper(): s for s in led.subscriptions()}  # sync title-cases
        cats = {
            t.categorisation.merchant.upper(): t.categorisation.category
            for t in led.transactions()
            if t.categorisation and t.categorisation.merchant
        }
    assert subs["NETFLIX.COM"].status == "active"
    assert cats["TESCO"] == ("Food & Drink", "Groceries")
    for word in ("netflix", "tesco", "10.99", "25.00"):
        assert word not in caplog.text.lower()


def test_fa_sync_categorises_and_recognises(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        assert main(["sync"]) == 0
    check(engine, caplog)


def test_post_api_sync_categorises_and_recognises(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG), TestClient(api.create_app(engine)) as c:
        assert c.post("/api/sync").json()["added"] == 10
    check(engine, caplog)


def test_watched_folder_import_categorises_and_recognises(
    tmp_path: Path, engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    folder = tmp_path / "drop"
    folder.mkdir()
    (folder / "a.csv").write_text(render_export(H, 0, 10).text)
    with caplog.at_level(logging.DEBUG):
        assert watch.run(folder, lambda title, body: None) == 0
    check(engine, caplog)
