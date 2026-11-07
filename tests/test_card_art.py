"""Local card art (#58): the user's own card images, served from the data dir only.

Every image here is generated at test time; the real art never enters the repo."""

import os
import struct
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api, identity, storage
from finance_agent.api import card_art
from finance_agent.ledger import Ledger
from synthetic_lloyds import generate_histories, render_export


def png(width: int = 4, height: int = 3) -> bytes:
    """A tiny valid PNG, made here so no image file is committed."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + b"\x10\x80\x40" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def art_dir() -> Path:
    """The card-art folder beside the (temporary) test database."""
    d = storage.default_db_path().parent / "card-art"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def test_the_art_folder_sits_in_the_data_dir_not_the_repo(art_dir: Path) -> None:
    assert card_art.art_dir() == art_dir
    assert not art_dir.resolve().is_relative_to(Path(__file__).resolve().parents[1])


def test_an_allowlisted_name_serves_the_file(client: TestClient, art_dir: Path) -> None:
    body = png()
    (art_dir / "hsbc.png").write_bytes(body)

    r = client.get("/api/card-art/hsbc")

    assert r.status_code == 200
    assert r.content == body
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in r.headers["cache-control"]


def test_jpeg_and_webp_are_served_too(client: TestClient, art_dir: Path) -> None:
    (art_dir / "lloyds.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    (art_dir / "revolut.webp").write_bytes(
        b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 16
    )

    assert client.get("/api/card-art/lloyds").headers["content-type"] == "image/jpeg"
    assert client.get("/api/card-art/revolut").headers["content-type"] == "image/webp"


@pytest.mark.parametrize(
    "path",
    [
        "/api/card-art/..%2Fledger.db",
        "/api/card-art/../ledger.db",
        "/api/card-art/%2e%2e%2fledger.db",
        "/api/card-art/hsbc.png",
        "/api/card-art/HSBC",
        "/api/card-art/barclays",
        "/api/card-art/hsbc%00",
        "/api/card-art/",
    ],
)
def test_anything_off_the_allowlist_is_404(
    client: TestClient, art_dir: Path, path: str
) -> None:
    (art_dir / "hsbc.png").write_bytes(png())
    (art_dir.parent / "ledger.db").write_bytes(b"SQLite format 3\x00secret")

    r = client.get(path)

    assert r.status_code == 404
    assert b"secret" not in r.content


def test_a_non_image_is_refused(client: TestClient, art_dir: Path) -> None:
    (art_dir / "lloyds.png").write_bytes(b"<svg onload=alert(1)>not a png</svg>")

    r = client.get("/api/card-art/lloyds")

    assert r.status_code == 415
    assert b"svg" not in r.content


def test_a_file_over_the_size_limit_is_refused(
    client: TestClient, art_dir: Path
) -> None:
    (art_dir / "hsbc.png").write_bytes(png() + b"\x00" * card_art.MAX_BYTES)

    assert client.get("/api/card-art/hsbc").status_code == 413


def test_a_symlink_out_of_the_folder_is_refused(
    client: TestClient, art_dir: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(png())
    (art_dir / "revolut.png").symlink_to(outside)

    assert client.get("/api/card-art/revolut").status_code == 404


def test_a_missing_file_is_404_so_the_app_falls_back(client: TestClient) -> None:
    assert client.get("/api/card-art/revolut").status_code == 404


@pytest.mark.parametrize(
    ("bank", "key"),
    [
        ("Lloyds", "lloyds"),
        ("LLOYDS BANK", "lloyds"),
        ("Lloyds Bank plc", "lloyds"),
        ("HSBC UK", "hsbc"),
        ("hsbc", "hsbc"),
        ("Revolut", "revolut"),
        ("Revolut Ltd", "revolut"),
        ("  revolut  ", "revolut"),
        ("lloydsbank.com", "lloyds"),
        ("hsbc.co.uk", "hsbc"),
        ("Synthetic Bank", None),  # the SimpleFIN fixture's org.name
        ("org-synthetic-1", None),  # SimpleFIN falls back to org.id
        ("Halifax", None),
        ("", None),
    ],
)
def test_bank_names_map_to_art_keys(bank: str, key: str | None) -> None:
    assert card_art.art_key(bank) == key


def test_accounts_carry_their_art_key(
    client: TestClient, engine: Engine, write_export: Callable[[str], Path]
) -> None:
    current, _ = generate_histories(seed=8, days=20)
    path = write_export(render_export(current, 0, 20).text)
    with engine.begin() as conn:
        Ledger(conn, identity.current_user()).import_files([path])

    (account,) = client.get("/api/accounts").json()

    assert account["bank"] == "Lloyds"
    assert account["art"] == "lloyds"


# Verifier's adversarial suite (#58): encodings, link games, special files, TOCTOU.


@pytest.mark.parametrize(
    "path",
    [
        "/api/card-art/%252e%252e%252fledger.db",  # double-encoded
        "/api/card-art/..%5cledger.db",  # backslash
        "/api/card-art/hsbc%5c..%5c..%5cledger.db",
        "/api/card-art/%2Ftmp%2Fledger.db",  # absolute
        "/api/card-art/hsbc%00.png",
        "/api/card-art/h%D1%95bc",  # Cyrillic s
        "/api/card-art/%EF%BD%88sbc",  # fullwidth h
        "/api/card-art/hsbc%20",
        "/api/card-art/Hsbc",
        "/api/card-art/.hsbc",
    ],
)
def test_encoded_and_lookalike_keys_are_404(
    client: TestClient, art_dir: Path, path: str
) -> None:
    (art_dir / "hsbc.png").write_bytes(png())
    (art_dir.parent / "ledger.db").write_bytes(b"SQLite format 3\x00secret")

    r = client.get(path)

    assert r.status_code == 404
    assert b"secret" not in r.content


def test_a_hardlink_to_the_ledger_is_not_served(
    client: TestClient, art_dir: Path
) -> None:
    db = art_dir.parent / "ledger.db"
    db.write_bytes(b"SQLite format 3\x00secret")
    (art_dir / "hsbc.png").hardlink_to(db)

    r = client.get("/api/card-art/hsbc")

    assert r.status_code == 415
    assert b"secret" not in r.content


def test_a_symlink_inside_the_folder_is_refused(
    client: TestClient, art_dir: Path
) -> None:
    (art_dir / "real.png").write_bytes(png())
    (art_dir / "lloyds.png").symlink_to(art_dir / "real.png")

    assert client.get("/api/card-art/lloyds").status_code == 404


def test_a_directory_or_fifo_with_the_name_is_not_read(
    client: TestClient, art_dir: Path
) -> None:
    (art_dir / "hsbc.png").mkdir()
    os.mkfifo(art_dir / "lloyds.png")  # opening this for read would hang

    assert client.get("/api/card-art/hsbc").status_code == 404
    assert client.get("/api/card-art/lloyds").status_code == 404


def test_the_size_cap_holds_on_the_bytes_read_not_an_earlier_stat(
    client: TestClient, art_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that grows after it is checked (TOCTOU) is still refused."""
    (art_dir / "hsbc.png").write_bytes(png() + b"\x00" * card_art.MAX_BYTES)
    real_stat = Path.stat

    def small(self: Path, **kw: bool) -> os.stat_result:
        st = real_stat(self, **kw)
        return os.stat_result((*st[:6], 100, *st[7:]))  # st_size as checked earlier

    monkeypatch.setattr(Path, "stat", small)

    assert client.get("/api/card-art/hsbc").status_code == 413
