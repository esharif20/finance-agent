"""Allocation Plans (#260, spec #251 decision 3): split a month's expected
income into fixed bills, goal lines and discretionary Categories.

Pure: the caller builds an `AllocationInput` (income from `engine.forecast`,
bills, goals and caps from confirmed Memories); nothing here reads the Ledger.
Advice only: a plan never schedules, moves or pays anything.

The linear program (`scipy.optimize.linprog`, HiGHS) minimises the weighted
absolute deviation from the recent spending pattern (goals: from their monthly
target), subject to the hard limits: bills are fixed, goal floors and Category
floors and caps hold, and the lines sum to income. Spare income left after
every line is on its pattern goes to an "unallocated" line. The LP works in
float (#252); its answer is rounded to pence by largest remainder and every
hard constraint is re-checked in integers. A failed re-check is `Infeasible`.

`fifty_thirty_twenty` is the baseline the plan is compared with.
"""

# scipy ships partial type information
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeStubs=false

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from scipy.optimize import linprog

MAX_MINOR = 10**15  # £10 trillion: far beyond any household, exact in a float
BUFFER = "unallocated"
TIE_BREAK = 1e-6  # makes the optimum unique, so the plan ignores input order


class Kind(StrEnum):
    BILL = "bill"
    GOAL = "goal"
    CATEGORY = "category"
    BUFFER = "buffer"


class Reason(StrEnum):
    """Why a line has its amount, for the explanation."""

    FIXED_BILL = "fixed_bill"
    ON_PATTERN = "on_pattern"  # equal to recent spending (or the goal's target)
    GOAL_FLOOR = "goal_floor"  # held at the goal's floor
    CONSTRAINT = "constraint"  # held at a confirmed floor or cap
    PRIORITY = "priority"  # moved off its pattern: a lower-priority trade-off
    LEFTOVER = "leftover"  # spare income after every other line
    BASELINE = "baseline"  # a 50/30/20 line


@dataclass(frozen=True)
class Bill:
    name: str
    amount_minor: int


@dataclass(frozen=True)
class Goal:
    name: str
    target_minor: int  # the month's contribution that keeps the goal on its date
    floor_minor: int = 0
    cap_minor: int | None = None
    weight: float = 1.0  # from Memory priority; `with_strengths` refines it


@dataclass(frozen=True)
class Category:
    name: str
    recent_minor: int  # recent monthly spending
    floor_minor: int = 0
    cap_minor: int | None = None
    weight: float = 1.0
    essential: bool = False  # a need, for the 50/30/20 baseline only


@dataclass(frozen=True)
class AllocationInput:
    income_minor: int
    bills: tuple[Bill, ...] = ()
    goals: tuple[Goal, ...] = ()
    categories: tuple[Category, ...] = ()
    buffer_weight: float = 0.5  # below the default, so spare money is left spare


@dataclass(frozen=True)
class Line:
    kind: Kind
    name: str
    amount_minor: int
    reference_minor: int  # recent spending, goal target or bill amount
    reason: Reason


@dataclass(frozen=True)
class Plan:
    income_minor: int
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class Infeasible:
    # bills_exceed_income, floors_exceed_income, floor_above_cap, solver, recheck
    reason: str


@dataclass(frozen=True)
class _Flex:
    kind: Kind
    name: str
    ref: int
    lo: int
    hi: int | None
    weight: float
    essential: bool = False


MAX_STRENGTH = 5.0  # exp(5) is ~150x: a strong view, never an unbounded one


def with_strengths(
    inp: AllocationInput, fitted: Mapping[str, float]
) -> AllocationInput:
    """Weights from Bradley-Terry strengths (#269): a goal or Category named in
    `fitted` gets weight exp(strength). Fit the strengths with the Memory
    priority as the prior mean (log weight), so the two combine. Only weights
    change, so floors, caps and the income sum are untouched; names not in
    `fitted` keep their weight, and the buffer weight drops to half the lowest
    line weight if that is lower. Strengths are clamped, and a
    non-finite one is rejected."""
    if not all(math.isfinite(v) for v in fitted.values()):
        raise ValueError("strengths must be finite")

    def w(name: str, old: float) -> float:
        if name not in fitted:
            return old
        return math.exp(max(-MAX_STRENGTH, min(MAX_STRENGTH, fitted[name])))

    goals = tuple(replace(g, weight=w(g.name, g.weight)) for g in inp.goals)
    cats = tuple(replace(c, weight=w(c.name, c.weight)) for c in inp.categories)
    # the buffer stays cheaper than every line, or spare money would be pushed
    # into lines above their pattern instead of left spare
    lowest = min((x.weight for x in (*goals, *cats)), default=math.inf)
    return replace(
        inp,
        goals=goals,
        categories=cats,
        buffer_weight=min(inp.buffer_weight, lowest / 2),
    )


def _validate(inp: AllocationInput) -> None:
    amounts = [inp.income_minor]
    amounts += [b.amount_minor for b in inp.bills]
    for g in inp.goals:
        amounts += [g.target_minor, g.floor_minor, g.cap_minor or 0]
    for c in inp.categories:
        amounts += [c.recent_minor, c.floor_minor, c.cap_minor or 0]
    if any(type(a) is not int or not 0 <= a <= MAX_MINOR for a in amounts):
        raise ValueError(f"amounts must be integer pence in [0, {MAX_MINOR}]")
    weights = [inp.buffer_weight] + [x.weight for x in (*inp.goals, *inp.categories)]
    if not all(math.isfinite(w) and w > 0 for w in weights):
        raise ValueError("weights must be positive and finite")
    for kind, names in (
        ("bill", [b.name for b in inp.bills]),
        ("goal", [g.name for g in inp.goals]),
        ("category", [c.name for c in inp.categories]),
    ):
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate {kind} name")


def _flex(inp: AllocationInput) -> list[_Flex]:
    """Goal, Category and buffer lines in a canonical order."""
    goals = sorted(inp.goals, key=lambda g: g.name)
    cats = sorted(inp.categories, key=lambda c: c.name)
    return (
        [
            _Flex(
                Kind.GOAL, g.name, g.target_minor, g.floor_minor, g.cap_minor, g.weight
            )
            for g in goals
        ]
        + [
            _Flex(
                Kind.CATEGORY,
                c.name,
                c.recent_minor,
                c.floor_minor,
                c.cap_minor,
                c.weight,
                c.essential,
            )
            for c in cats
        ]
        + [_Flex(Kind.BUFFER, BUFFER, 0, 0, None, inp.buffer_weight)]
    )


def _solve(flex: Sequence[_Flex], spare: int) -> list[float] | None:
    """min sum w_i |x_i - r_i|  s.t.  sum x_i = spare, lo_i <= x_i <= hi_i."""
    n = len(flex)
    scale = float(max(spare, *(f.ref for f in flex), 1))
    w = [f.weight * (1 + TIE_BREAK * i) for i, f in enumerate(flex)]
    c = [0.0] * n + w + w  # x, then d+ and d- with x - r = d+ - d-
    a_eq = [[1.0] * n + [0.0] * (2 * n)]
    b_eq = [spare / scale]
    for i, f in enumerate(flex):
        row = [0.0] * (3 * n)
        row[i], row[n + i], row[2 * n + i] = 1.0, -1.0, 1.0
        a_eq.append(row)
        b_eq.append(f.ref / scale)
    bounds = [(f.lo / scale, None if f.hi is None else f.hi / scale) for f in flex] + [
        (0.0, None)
    ] * (2 * n)
    res = linprog(c, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if res.status != 0:
        return None
    return [float(v) * scale for v in res.x[:n]]


def _round(xs: Sequence[float], flex: Sequence[_Flex], spare: int) -> list[int] | None:
    """Largest remainder to pence, within each line's bounds."""
    clipped = [
        min(max(x, f.lo), f.hi if f.hi is not None else math.inf)
        for x, f in zip(xs, flex, strict=True)
    ]
    out = [math.floor(x) for x in clipped]
    frac = [x - o for x, o in zip(clipped, out, strict=True)]
    k = spare - sum(out)
    order = sorted(range(len(out)), key=lambda i: (-frac[i], i))
    for i in order if k > 0 else reversed(order):
        if k == 0:
            break
        f = flex[i]
        if k > 0 and (f.hi is None or out[i] < f.hi):
            out[i], k = out[i] + 1, k - 1
        elif k < 0 and out[i] > f.lo:
            out[i], k = out[i] - 1, k + 1
    return out if k == 0 else None


def violations(inp: AllocationInput, plan: Plan) -> list[str]:
    """Every hard constraint `plan` breaks, re-checked in integers."""
    got = {(ln.kind, ln.name): ln.amount_minor for ln in plan.lines}
    want = {(Kind.BILL, b.name) for b in inp.bills} | {
        (f.kind, f.name) for f in _flex(inp)
    }
    out: list[str] = []
    if set(got) != want or len(got) != len(plan.lines):
        out.append("lines do not match the input")
    if sum(ln.amount_minor for ln in plan.lines) != inp.income_minor:
        out.append("lines do not sum to income")
    out += [f"{ln.name} negative" for ln in plan.lines if ln.amount_minor < 0]
    for b in inp.bills:
        if got.get((Kind.BILL, b.name)) != b.amount_minor:
            out.append(f"bill {b.name} not covered")
    for f in _flex(inp):
        a = got.get((f.kind, f.name))
        if a is None:
            continue
        if a < f.lo:
            out.append(f"{f.kind} {f.name} below its floor")
        if f.hi is not None and a > f.hi:
            out.append(f"{f.kind} {f.name} above its cap")
    return out


def _reason(f: _Flex, a: int) -> Reason:
    if f.kind is Kind.BUFFER:
        return Reason.LEFTOVER
    if abs(a - f.ref) <= 1:  # a penny of rounding
        return Reason.ON_PATTERN
    if a == f.lo and f.lo > 0:
        return Reason.GOAL_FLOOR if f.kind is Kind.GOAL else Reason.CONSTRAINT
    if f.hi is not None and a == f.hi:
        return Reason.CONSTRAINT
    return Reason.PRIORITY


def _bill_lines(inp: AllocationInput) -> list[Line]:
    return [
        Line(Kind.BILL, b.name, b.amount_minor, b.amount_minor, Reason.FIXED_BILL)
        for b in sorted(inp.bills, key=lambda b: b.name)
    ]


def plan(inp: AllocationInput) -> Plan | Infeasible:
    """The month's Allocation Plan, or why none satisfies the hard limits."""
    _validate(inp)
    spare = inp.income_minor - sum(b.amount_minor for b in inp.bills)
    if spare < 0:
        return Infeasible("bills_exceed_income")
    flex = _flex(inp)
    if any(f.hi is not None and f.lo > f.hi for f in flex):
        return Infeasible("floor_above_cap")
    if sum(f.lo for f in flex) > spare:
        return Infeasible("floors_exceed_income")
    xs = _solve(flex, spare)
    if xs is None:
        return Infeasible("solver")
    amounts = _round(xs, flex, spare)
    if amounts is None:
        return Infeasible("recheck")
    result = Plan(
        inp.income_minor,
        tuple(_bill_lines(inp))
        + tuple(
            Line(f.kind, f.name, a, f.ref, _reason(f, a))
            for f, a in zip(flex, amounts, strict=True)
        ),
    )
    return Infeasible("recheck") if violations(inp, result) else result


def split(total: int, weights: Sequence[int]) -> list[int]:
    """`total` in proportion to `weights` by largest remainder, in integers
    (equal shares when every weight is 0). Sums to `total` exactly."""
    if not weights:
        return []
    ws = list(weights) if any(weights) else [1] * len(weights)
    whole = sum(ws)
    out = [total * w // whole for w in ws]
    rem = [total * w % whole for w in ws]
    for i in sorted(range(len(ws)), key=lambda i: (-rem[i], i))[: total - sum(out)]:
        out[i] += 1
    return out


def fifty_thirty_twenty(inp: AllocationInput) -> Plan | Infeasible:
    """The baseline: 50% needs (bills, then essential Categories), 30% wants,
    20% goals, scaled to what is left after bills; within a group in
    proportion to recent spending (goals: targets). An empty group's share is
    unallocated. It ignores floors and caps, which is what it is compared on."""
    _validate(inp)
    bills = sum(b.amount_minor for b in inp.bills)
    spare = inp.income_minor - bills
    if spare < 0:
        return Infeasible("bills_exceed_income")
    needs, wants, saves = split(
        spare,
        [
            max(inp.income_minor * 5 - bills * 10, 0),
            inp.income_minor * 3,
            inp.income_minor * 2,
        ],
    )
    flex = _flex(inp)
    amounts = dict.fromkeys(range(len(flex)), 0)
    buffer = len(flex) - 1
    groups = (
        (
            needs,
            [i for i, f in enumerate(flex) if f.kind is Kind.CATEGORY and f.essential],
        ),
        (
            wants,
            [
                i
                for i, f in enumerate(flex)
                if f.kind is Kind.CATEGORY and not f.essential
            ],
        ),
        (saves, [i for i, f in enumerate(flex) if f.kind is Kind.GOAL]),
    )
    for total, idx in groups:
        if not idx:
            amounts[buffer] += total
            continue
        for i, a in zip(idx, split(total, [flex[i].ref for i in idx]), strict=True):
            amounts[i] += a
    return Plan(
        inp.income_minor,
        tuple(_bill_lines(inp))
        + tuple(
            Line(f.kind, f.name, amounts[i], f.ref, Reason.BASELINE)
            for i, f in enumerate(flex)
        ),
    )
