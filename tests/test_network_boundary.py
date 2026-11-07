"""ADR 0013: only `ingest.simplefin` may import a network client, so no other
module can reach the network by accident. An AST scan of every source file."""

import ast
from pathlib import Path

NETWORK = {"urllib", "http", "socket", "ssl", "requests", "httpx", "aiohttp", "urllib3"}
# this checkout's source, not whichever `finance_agent` sys.path finds first
SRC = Path(__file__).resolve().parents[1] / "src" / "finance_agent"
ALLOWED = {
    "ingest/simplefin.py": "the one aggregator client (ADR 0013)",
    "cli/launcher.py": "probes our own server's /api/health on 127.0.0.1 only",
    "assistant/ollama.py": "the local model on localhost:11434 only (#135)",
    "categorise/embedding.py": "local embeddings on localhost:11434 only (#113)",
}


DYNAMIC = {"__import__", "import_module"}


def _callee(call: ast.Call) -> str | None:
    f = call.func
    return (
        f.id
        if isinstance(f, ast.Name)
        else f.attr
        if isinstance(f, ast.Attribute)
        else None
    )


def network_imports(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        elif isinstance(node, ast.Call) and _callee(node) in DYNAMIC:
            arg = node.args[0] if node.args else None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names = [arg.value]
            else:
                names = ["socket"]  # a computed name cannot be checked: refuse it
        else:
            continue
        found |= {n.split(".")[0] for n in names} & NETWORK
    return found


def test_only_the_simplefin_client_imports_network_modules() -> None:
    offenders = {
        rel: mods
        for p in SRC.rglob("*.py")
        if (rel := p.relative_to(SRC).as_posix()) not in ALLOWED
        and (mods := network_imports(p.read_text()))
    }
    assert offenders == {}


def test_the_launcher_probe_only_talks_to_localhost() -> None:
    text = (SRC / "cli/launcher.py").read_text()
    assert network_imports(text) == {"urllib"}
    assert 'f"http://{HOST}:{port}/api/health"' in text


def test_the_ollama_adapter_only_talks_to_localhost() -> None:
    text = (SRC / "assistant/ollama.py").read_text()
    assert network_imports(text) == {"http", "urllib"}  # urllib only for urlsplit
    assert "from urllib.parse import urlsplit" in text
    assert text.count("http.client.HTTPConnection(") == 1
    assert "HTTPSConnection" not in text and "urllib.request" not in text


def test_the_embedding_adapter_only_talks_to_localhost() -> None:
    text = (SRC / "categorise/embedding.py").read_text()
    assert network_imports(text) == {"http", "urllib"}  # urllib only for urlsplit
    assert "from urllib.parse import urlsplit" in text
    assert text.count("http.client.HTTPConnection(") == 1
    assert "HTTPSConnection" not in text and "urllib.request" not in text


def test_the_scan_catches_every_import_form() -> None:
    for src in (
        "import socket",
        "import urllib.request as r",
        "from http.client import HTTPSConnection",
        "from ssl import create_default_context",
        "def f():\n    import requests",
        'importlib.import_module("socket")',
        'import_module("http.client")',
        '__import__("ssl")',
        "__import__(name)",  # a computed name cannot be checked: refused
    ):
        assert network_imports(src), src
    assert network_imports("from . import socket") == set()  # a sibling module


def test_the_scan_covers_this_checkouts_source_not_the_installed_package() -> None:
    # another worktree's package on sys.path must not stand in for this one's
    assert SRC == Path(__file__).resolve().parents[1] / "src" / "finance_agent"
    assert (SRC / "ingest" / "simplefin.py").is_file()
