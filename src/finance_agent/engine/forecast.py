"""Forecasting (#149, docs/AI_DESIGN.md, ADR 0015): ETS per Category for
discretionary spending, recurring payments projected from their schedules,
combined into cash flow.

Floats are allowed in this module only (docs/ARCHITECTURE.md): the model fits
in float, and every output is rounded to integer pence before it leaves.

The model is ETS(A,N,A) on log(1 + monthly spend): multiplicative seasonality,
deterministic analytic intervals, and a median point forecast that never goes
below zero. The error variance is divided by its degrees of freedom (residuals
less estimated parameters) and the interval uses Student's t, so short
histories get honestly wide bands. `evals/forecast.py` backtests it against
seasonal naive.

Gates (ADR 0015, docs/ML_EVALUATIONS.md): ETS from 24 months, seasonal naive
from 12, and "insufficient" before.
"""

# statsmodels ships no type information
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeStubs=false

import calendar
import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy.stats import t as student_t
from statsmodels.tsa.exponential_smoothing.ets import ETSModel

from finance_agent.categorise.recurring import CADENCES, Recurrence, detect_recurring
from finance_agent.ledger import Ledger

LEVEL = 0.8  # the prediction interval's coverage
SEASON = 12
# ADR 0015's starting gate was 13 months; the backtest tuned it to two seasons:
# below 24 the initial seasonal states are one noisy year, and ETS lost to
# seasonal naive there on every seed tried (docs/ML_EVALUATIONS.md)
MIN_MONTHS = 2 * SEASON
SEASONAL_NAIVE_MONTHS = 12
PARAMS = 2 + 1 + SEASON - 1  # alpha, gamma, the level and 11 free seasonal states
MAX_LOG = 700.0  # exp(700) is about 1e304, still a finite float

Method = Literal["ets", "seasonal_naive", "insufficient"]


@dataclass(frozen=True)
class Band:
    """One month's forecast, in pence: lower <= point <= upper, never negative."""

    point: int
    lower: int
    upper: int


@dataclass(frozen=True)
class CategoryForecast:
    method: Method
    months: tuple[Band, ...]  # empty when insufficient


@dataclass(frozen=True)
class RecurringPayment:
    merchant: str
    interval: str  # weekly, monthly or annual
    months: tuple[int, ...]  # pence due in each forecast month


@dataclass(frozen=True)
class CashFlowMonth:
    month: str  # YYYY-MM
    recurring: int  # pence, from schedules
    total: Band  # recurring plus every forecast Category


@dataclass(frozen=True)
class CashFlow:
    """Money out over the coming months. Categories marked insufficient are
    left out of the totals; the UI says why (ADR 0015)."""

    months: tuple[CashFlowMonth, ...]
    recurring: tuple[RecurringPayment, ...]
    categories: dict[tuple[str, str], CategoryForecast]


def cash_flow(led: Ledger, as_of: date, horizon: int = 3) -> CashFlow:
    """Forecast money out from `as_of`'s month for `horizon` months, using only
    complete months before it. Read-only: the Ledger and registry are only read.

    A Merchant with a recurring schedule (`categorise.recurring`) is projected
    from it while it is still running, unless the user said it is not a
    subscription; its charges, and a cancelled one's, are not discretionary.
    Everything else is Spending per Category, forecast by `forecast_category`."""
    start = as_of.replace(day=1)
    months = [_add_months(start, k) for k in range(horizon + 1)]
    registry = {e.merchant_id: e.status for e in led.subscriptions()}
    shares: list[tuple[int | None, date, tuple[str, str] | None, int]] = []
    names: dict[int, str] = {}
    by_merchant: dict[int, list[tuple[date, int]]] = {}
    for t in led.transactions():
        c = t.categorisation
        if c is None or c.kind != "Spending" or t.posted_on >= start:
            continue
        pence = int(-t.amount * 100)
        parts = [(p.category, -p.amount_minor) for p in c.parts] or [
            (c.category, pence)
        ]
        shares += [(c.merchant_id, t.posted_on, cat, p) for cat, p in parts]
        if c.merchant_id is not None:
            names[c.merchant_id] = c.merchant or ""
            by_merchant.setdefault(c.merchant_id, []).append((t.posted_on, pence))
    recurring: list[RecurringPayment] = []
    scheduled: set[int] = set()
    for merchant_id, charges in sorted(by_merchant.items()):
        status = registry.get(merchant_id)
        if status == "not_subscription":
            continue
        rec = detect_recurring(charges)
        if rec is None and status != "user_cancelled":
            continue
        scheduled.add(merchant_id)
        if rec is None or status == "user_cancelled":
            continue
        due = _project(rec, months)
        if any(due):
            recurring.append(RecurringPayment(names[merchant_id], rec.interval, due))
    monthly: dict[tuple[str, str], dict[date, int]] = {}
    for merchant_id, on, cat, pence in shares:
        if cat is not None and merchant_id not in scheduled:
            spend = monthly.setdefault(cat, {})
            spend[on.replace(day=1)] = spend.get(on.replace(day=1), 0) + pence
    categories: dict[tuple[str, str], CategoryForecast] = {}
    for cat, spend in sorted(monthly.items()):
        first, history = min(spend), []
        while first < start:
            history.append(spend.get(first, 0))
            first = _add_months(first, 1)
        categories[cat] = forecast_category(history, horizon)
    forecasts = [f.months for f in categories.values() if f.months]
    out: list[CashFlowMonth] = []
    for i, month in enumerate(months[:-1]):
        fixed = sum(r.months[i] for r in recurring)
        bands = [f[i] for f in forecasts]
        # shortcut: bounds add as if Categories moved together (wider than an
        # independent sum); model the joint error if the band proves too wide
        total = Band(
            fixed + sum(b.point for b in bands),
            fixed + sum(b.lower for b in bands),
            fixed + sum(b.upper for b in bands),
        )
        out.append(CashFlowMonth(f"{month:%Y-%m}", fixed, total))
    return CashFlow(tuple(out), tuple(recurring), categories)


def _add_months(d: date, k: int) -> date:
    y, m = divmod(d.month - 1 + k, 12)
    day = min(d.day, calendar.monthrange(d.year + y, m + 1)[1])
    return date(d.year + y, m + 1, day)


def _project(rec: Recurrence, months: Sequence[date]) -> tuple[int, ...]:
    """Pence due in each month of `months` (consecutive month starts, the last
    one exclusive) from a series' schedule; nothing once it has lapsed."""
    start, end = months[0], months[-1]
    if (start - rec.last_on).days > CADENCES[rec.interval][1]:
        return (0,) * (len(months) - 1)  # missed a whole cycle: it has stopped
    due: list[date] = []
    k = 1
    while (d := _next(rec, k)) < end:
        if d >= start:
            due.append(d.replace(day=1))
        k += 1
    return tuple(due.count(m) * rec.typical_minor for m in months[:-1])


def _next(rec: Recurrence, k: int) -> date:
    if rec.interval == "weekly":
        return rec.last_on + timedelta(weeks=k)
    return _add_months(rec.last_on, k * (12 if rec.interval == "annual" else 1))


def forecast_category(history: Sequence[int], horizon: int) -> CategoryForecast:
    """The next `horizon` months of spend in a Category, from its complete
    monthly totals so far (pence, oldest first; negative months count as 0)."""
    y = [max(0, v) for v in history]
    if len(y) < SEASONAL_NAIVE_MONTHS or horizon < 1:
        return CategoryForecast("insufficient", ())
    if len(y) < MIN_MONTHS or len(set(y)) == 1:
        # a constant series has no error to model: seasonal naive repeats it
        return CategoryForecast("seasonal_naive", _seasonal_naive(y, horizon))
    bands = _ets(y, horizon)
    if bands is None:
        return CategoryForecast("seasonal_naive", _seasonal_naive(y, horizon))
    return CategoryForecast("ets", bands)


def _seasonal_naive(y: Sequence[int], horizon: int) -> tuple[Band, ...]:
    """The same month a year before; the interval is the nearest-rank LEVEL
    quantile of past same-lag errors (lag 1 when there is no year to compare)."""
    lag = SEASON if len(y) > SEASON else 1
    errors = sorted(abs(a - b) for a, b in zip(y[lag:], y, strict=False))
    q = errors[math.ceil(LEVEL * len(errors)) - 1]
    n = len(y)
    points = [y[n - SEASON + (h - 1) % SEASON] for h in range(1, horizon + 1)]
    return tuple(Band(p, max(0, p - q), p + q) for p in points)


def _ets(y: Sequence[int], horizon: int) -> tuple[Band, ...] | None:
    z = np.log1p(np.asarray(y, dtype=np.float64))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # convergence chatter; checked below
            fit: Any = ETSModel(
                pd.Series(z),
                error="add",
                seasonal="add",
                seasonal_periods=SEASON,
                initialization_method="estimated",
            ).fit(disp=False)
    except (ValueError, np.linalg.LinAlgError):
        return None
    mean = np.asarray(fit.forecast(horizon), dtype=np.float64)
    resid = np.asarray(fit.resid, dtype=np.float64)
    dof = max(1, len(resid) - PARAMS)
    sigma = math.sqrt(float((resid**2).sum()) / dof)
    q = float(student_t.ppf(0.5 + LEVEL / 2, dof))
    alpha, gamma = float(fit.smoothing_level), float(fit.smoothing_seasonal)
    if not all(map(math.isfinite, [*mean, sigma, q, alpha, gamma])):
        return None
    bands: list[Band] = []
    variance = 1.0  # h-step variance of ETS(A,N,A), in units of sigma^2
    for h, m in enumerate(mean, start=1):
        if h > 1:
            variance += (alpha + (gamma if (h - 1) % SEASON == 0 else 0.0)) ** 2
        width = q * sigma * math.sqrt(variance)
        point = _pence(m)
        bands.append(
            Band(point, min(point, _pence(m - width)), max(point, _pence(m + width)))
        )
    return tuple(bands)


def _pence(log_value: float) -> int:
    return max(0, round(math.expm1(min(float(log_value), MAX_LOG))))
