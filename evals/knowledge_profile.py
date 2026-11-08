"""The L0 profile eval (#327 part 1, docs/ML_EVALUATIONS.md), written first.

Dataset: the demo's synthetic Personas (`scripts/build_demo_db.py`, fictional
Merchants), PERSONAS of them with MONTHS months of history ending on TODAY,
built through the real import and categorise pipeline in a temp directory.

Metrics, per Persona and overall:

- `fact_accuracy`: every fact in the profile re-checked against the Engine
  and the Ledger by code written here, not the profile's own: balances and
  as-of dates, income and payday, bills and their monthly cost, category
  medians, trends, unusual payments, goals and Memories, the history window.
  It is checked again after a Correction, so a stale profile counts as wrong.
- `render_grounded`: the share of rendered profiles whose every figure passes
  the grounding check (ADR 0007) against the profile's own tool values.
- `max_tokens`, `mean_tokens`: rendered size at `context.CHARS_PER_TOKEN`.
  Target 800 or fewer. Baseline: the same profile as JSON
  (`json_tokens`), what a tool result dump would put in the prompt.

Build times (first scan, refresh after a Correction) go to stderr only, as
they are not reproducible; the run fails when they miss the targets (30 s and
1 s). Nothing here opens the real data directory or the Keychain once the
Personas are built.
"""

import hashlib
import sys
import tempfile
import time
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from statistics import median
from typing import Any

import keyring
import keyring.backend

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gate  # noqa: E402
from build_demo_db import build as build_personas  # noqa: E402
from finance_agent.assistant import context  # noqa: E402
from finance_agent.assistant.grounding import check_grounding  # noqa: E402
from finance_agent.assistant.values import tool_values  # noqa: E402
from finance_agent.categorise.subscriptions import costs, display  # noqa: E402
from finance_agent.demo.session import plain_engine  # noqa: E402
from finance_agent.engine import forecast  # noqa: E402
from finance_agent.engine import profile as pf  # noqa: E402
from finance_agent.identity import current_user  # noqa: E402
from finance_agent.ledger import Ledger  # noqa: E402
from finance_agent.ledger.proposals import Proposals  # noqa: E402

RESULTS = Path(__file__).parent / "results" / "knowledge_profile.json"
DATASET_VERSION = 1
PERSONAS = 3
MONTHS = 13  # 12 complete months before TODAY's
TODAY = date(2026, 11, 8)
MAX_TOKENS = 800
FIRST_SCAN_S = 30
REFRESH_S = 1


def _minor(x: Any) -> int:
    return int(x.scaleb(2))


def _half_even(f: Fraction) -> int:
    q, r = divmod(f.numerator, f.denominator)
    twice = 2 * r
    return q + (twice > f.denominator or (twice == f.denominator and q % 2))


def check(led: Ledger, props: Proposals, p: pf.Profile) -> tuple[int, int]:
    """(facts right, facts checked) for one profile, re-derived here."""
    ok = total = 0

    def fact(good: bool) -> None:
        nonlocal ok, total
        total += 1
        ok += bool(good)

    txns = led.transactions()
    months = sorted(led.months())
    fact(p.first_month == (months[0] if months else None))
    fact(p.last_month == (months[-1] if months else None))
    fact(p.months == len(months))

    balances = {a.id: a for a in led.accounts()}
    last_on: dict[int, date] = {}
    for t in txns:
        last_on[t.account_id] = max(last_on.get(t.account_id, t.posted_on), t.posted_on)
    for a in p.accounts:
        b = balances.get(a.account_id)
        raw = (
            None
            if b is None
            else (b.bank_balance if b.bank_balance is not None else b.ledger_balance)
        )
        want = None if raw is None else _minor(raw)
        fact(b is not None and (a.balance.minor if a.balance else None) == want)
        fact(a.as_of == last_on.get(a.account_id))
        fact(a.overdrawn == (want is not None and want < 0))
    fact(len(p.accounts) == min(len(balances), pf.MAX_ACCOUNTS))

    flow = forecast.cash_flow(led, p.as_of, 1)
    income = {r.merchant: r for r in flow.income}
    for i in p.income:
        r = income.get(i.name)
        fact(
            r is not None and i.amount.minor == r.months[0] and i.interval == r.interval
        )
        fact(
            r is not None
            and i.next_on == next((d for d in r.dates if d >= p.as_of), None)
        )

    summary = costs(led, p.as_of)
    fact(
        {t.name: t.monthly.minor for t in p.totals}
        == {k: display(v) for k, v in summary.kind_totals.items()}
    )
    items = {c.entry.merchant_id: c for c in summary.items}
    for bill in p.bills:
        c = items.get(bill.merchant_id)
        fact(
            c is not None
            and c.monthly is not None
            and bill.monthly.minor == display(c.monthly)
        )
        fact(c is not None and bill.status == c.status and bill.kind == c.entry.kind)

    start = p.as_of.year * 12 + p.as_of.month - 1
    window = [f"{m // 12}-{m % 12 + 1:02d}" for m in range(start - 12, start)]
    window = [m for m in window if months and m >= months[0]]
    spend: dict[str, list[int]] = {}
    for i, m in enumerate(window):
        for s in led.spending_by_category(m):
            spend.setdefault(s.name, [0] * len(window))[i] = _minor(s.amount)
    for c in p.categories:
        series = spend.get(c.name, [])
        fact(
            bool(series) and c.median_12m.minor == _half_even(Fraction(median(series)))
        )
        fact(
            bool(series)
            and c.median_3m.minor == _half_even(Fraction(median(series[-3:])))
        )
    for t in p.trends:
        series = spend.get(t.name, [])
        m3 = _half_even(Fraction(median(series[-3:]))) if series else None
        m12 = _half_even(Fraction(median(series))) if series else None
        fact(m3 == t.median_3m.minor and m12 == t.median_12m.minor)
        if m3 is not None and m12 is not None:
            rising = m3 - m12 >= pf.TREND_MIN and 4 * m3 >= 5 * m12
            falling = m12 - m3 >= pf.TREND_MIN and 4 * m3 <= 3 * m12
            fact(
                (t.direction == "rising" and rising)
                or (t.direction == "falling" and falling)
            )

    by_id = {t.id: t for t in forecast.in_gbp(led)}
    for u in p.unusual:
        t = by_id.get(u.transaction_id)
        fact(
            t is not None
            and t.posted_on == u.posted_on
            and -_minor(t.amount) == u.amount.minor
        )
        fact(
            t is not None
            and p.as_of - timedelta(days=pf.RECENT_DAYS) < t.posted_on <= p.as_of
        )
        c = t.categorisation if t else None
        earlier = [
            -_minor(o.amount)
            for o in by_id.values()
            if c is not None
            and o.categorisation is not None
            and o.categorisation.kind == "Spending"
            and o.categorisation.merchant_id == c.merchant_id
            and (o.posted_on, o.id) < (t.posted_on, t.id)  # type: ignore[union-attr]
        ]
        pence = u.amount.minor
        fact(
            (
                pence >= pf.UNUSUAL_FLOOR
                and pence >= pf.UNUSUAL_TIMES * _half_even(Fraction(median(earlier)))
            )
            if earlier
            else pence >= pf.UNUSUAL_FIRST
        )

    mems = {m.id: m for m in props.memories()}
    for g in (*p.goals, *p.memories):
        m = mems.get(g.memory_id)
        fact(
            m is not None
            and m.type == g.type
            and g.label == (", ".join(m.tags) or m.type)
        )
    fact(
        all(g.type == "goal" for g in p.goals)
        and all(m.type != "goal" for m in p.memories)
    )
    return ok, total


def _sha(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for path in paths:
        eng = plain_engine(path)
        with eng.begin() as conn:
            for t in Ledger(conn, current_user()).transactions():
                h.update(
                    f"{t.posted_on}|{t.description}|{t.amount}|{t.account_id}\n".encode()
                )
        eng.dispose()
    return h.hexdigest()


class _NoKeychain(keyring.backend.KeyringBackend):
    """Once the Personas exist, any Keychain read fails the eval."""

    priority = 1  # pyright: ignore[reportAssignmentType]

    def get_password(self, service: str, username: str) -> str | None:
        raise RuntimeError("the profile read the Keychain")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise RuntimeError("the profile wrote the Keychain")

    def delete_password(self, service: str, username: str) -> None:
        raise RuntimeError("the profile wrote the Keychain")


def evaluate() -> dict[str, Any]:
    held = keyring.get_keyring()
    with tempfile.TemporaryDirectory() as tmp:
        paths = build_personas(
            Path(tmp), histories=PERSONAS, months=MONTHS, today=TODAY
        )
        keyring.set_keyring(_NoKeychain())
        try:
            return _measure(paths)
        finally:
            keyring.set_keyring(held)


def _measure(paths: list[Path]) -> dict[str, Any]:
    ok = total = grounded = 0
    tokens: list[int] = []
    json_tokens: list[int] = []
    scans: list[float] = []
    refreshes: list[float] = []
    per: dict[str, Any] = {}
    uid = current_user()
    for n, path in enumerate(paths):
        eng = plain_engine(path)
        try:
            pf.forget(uid)
            with eng.begin() as conn:
                t0 = time.perf_counter()
                text, p = pf.profile_context(conn, uid, TODAY)
                scans.append(time.perf_counter() - t0)
                led, props = Ledger(conn, uid), Proposals(conn, uid)
                a, b = check(led, props, p)
                g = check_grounding(
                    text, tool_values("profile", p), today=TODAY
                ).grounded
                # a Correction: the latest Spending row moves Category
                last = max(
                    (
                        t
                        for t in led.transactions()
                        if t.categorisation and t.categorisation.kind == "Spending"
                    ),
                    key=lambda t: (t.posted_on, t.id),
                )
                led.correct_transaction(last.id, ("Shopping", "Online"))
                t0 = time.perf_counter()
                text2, p2 = pf.profile_context(conn, uid, TODAY)
                refreshes.append(time.perf_counter() - t0)
                a2, b2 = check(led, props, p2)
                g2 = check_grounding(
                    text2, tool_values("profile", p2), today=TODAY
                ).grounded
                fresh = p2.evidence_id != p.evidence_id
        finally:
            eng.dispose()
        ok, total = ok + a + a2 + fresh, total + b + b2 + 1
        grounded += g + g2
        size = [-(-len(x) // context.CHARS_PER_TOKEN) for x in (text, text2)]
        tokens += size
        json_tokens += [
            -(-len(x.model_dump_json()) // context.CHARS_PER_TOKEN) for x in (p, p2)
        ]
        per[str(n)] = {
            "facts": b + b2 + 1,
            "fact_accuracy": round((a + a2 + fresh) / (b + b2 + 1), 6),
            "tokens": size[0],
            "accounts": len(p.accounts),
            "bills": len(p.bills),
            "categories": len(p.categories),
            "trends": len(p.trends),
            "unusual": len(p.unusual),
        }
    print(
        f"profile eval: first scan max {max(scans):.3f} s, refresh max "
        f"{max(refreshes):.3f} s, tokens max {max(tokens)}",
        file=sys.stderr,
    )
    if max(scans) > FIRST_SCAN_S or max(refreshes) > REFRESH_S:
        raise SystemExit("profile eval: a build missed its time target")
    if max(tokens) > MAX_TOKENS:
        raise SystemExit("profile eval: the profile is over its token budget")
    return {
        "harness": "knowledge_profile",
        "dataset": "demo_personas",
        "dataset_version": DATASET_VERSION,
        "dataset_sha256": _sha(paths),
        "personas": len(paths),
        "months": MONTHS,
        "today": TODAY.isoformat(),
        "per_persona": per,
        "models": {
            "profile": {
                "model": "engine.profile",
                "model_version": str(pf.VERSION),
                "metrics": {
                    "fact_accuracy": round(ok / total, 6),
                    "render_grounded": round(grounded / (2 * len(paths)), 6),
                    "max_tokens": max(tokens),
                    "mean_tokens": round(sum(tokens) / len(tokens), 1),
                },
            },
            "json_baseline": {
                "model": "profile.model_dump_json",
                "model_version": "1",
                "metrics": {
                    "max_tokens": max(json_tokens),
                    "mean_tokens": round(sum(json_tokens) / len(json_tokens), 1),
                },
            },
        },
    }


def lower_is_better(name: str) -> bool:
    return name.endswith("tokens")


if __name__ == "__main__":
    sys.exit(
        gate.run("knowledge_profile", evaluate, RESULTS, lower_is_better, sys.argv[1:])
    )
