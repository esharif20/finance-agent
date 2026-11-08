# synthetic
"""Compound spending questions for the query planner (#271,
docs/ML_EVALUATIONS.md). Synthetic only: six months (October 2025 to March
2026) of made-up card spending for one user, and questions whose answer is a
set of Merchants, e.g. "what did my car cost me this year" -> fuel, parking,
car insurance, DVLA, the garages and the MOT.

Each case carries the oracle's `spend_for_query` arguments (what a good model
would ask for), the gold Merchants as row descriptions, and the gold range.
The gold total is the sum of the gold rows in the range, computed here from
the rows, never by the code under test. A case with `after` is a follow-up:
it narrows that case's answer in the same conversation."""

import hashlib
import json
from datetime import date
from typing import Any, NamedTuple

DATASET_VERSION = 1
TODAY = date(2026, 3, 31)
START = date(2026, 1, 1)  # synthetic_lloyds' START: row days count from it
MONTHS = ("2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03")

# (day of month, description, pence spent) every month; amounts drift by
# month so no two months sum alike
MONTHLY = (
    (2, "ADMIRAL INSURANCE", 4567),
    (2, "AVIVA HOME INSURANCE", 1823),
    (3, "SHELL KINGS CROSS", 4800),
    (4, "OCTOPUS ENERGY", 9500),
    (5, "NCP CAR PARK", 1200),
    (6, "THAMES WATER", 3100),
    (7, "EE LIMITED", 3999),
    (8, "COUNCIL TAX LBC", 14200),
    (9, "ESSO MOTORWAY SVC", 5200),
    (10, "NETFLIX", 1099),
    (10, "SPOTIFY", 1199),
    (11, "PUREGYM", 2499),
    (12, "RINGGO PARKING", 350),
    (13, "TESCO STORES 3214", 6234),
    (14, "PRET A MANGER 0231", 675),
    (15, "BP CONNECT", 3900),
    (15, "BOOTS", 845),
    (16, "COSTA COFFEE", 390),
    (17, "DELIVEROO 2BX9", 2310),
    (19, "SAINSBURYS", 4520),
    (20, "TFL TRAVEL CH", 1250),
    (20, "GREGGS", 320),
    (21, "TESCO PFS 4471", 3600),
    (22, "STARBUCKS", 455),
    (23, "JUST EAT", 1875),
    (24, "WAGAMAMA", 2650),
    (25, "LIDL", 2875),
    (26, "NANDOS", 2180),
    (27, "UBER TRIP", 1630),
)
# (ISO date, description, pence spent): one-offs
ONE_OFF = (
    ("2025-10-18", "SCREWFIX", 3450),
    ("2025-11-08", "ZARA", 4999),
    ("2025-11-20", "HALFORDS AUTOCENTRE", 8999),
    ("2025-12-02", "EASYJET", 18999),
    ("2025-12-19", "TRAINLINE", 6420),
    ("2025-12-20", "PREMIER INN", 24500),
    ("2026-01-05", "AMAZON MKTPLACE", 2399),
    ("2026-01-14", "DVLA VEHICLE TAX", 19000),
    ("2026-01-18", "APCOA PARKING", 800),
    ("2026-01-24", "ODEON CINEMA", 1450),
    ("2026-02-07", "TRAINLINE", 5880),
    ("2026-02-11", "KWIK FIT", 12900),
    ("2026-02-21", "ARGOS", 2999),
    ("2026-03-05", "AMAZON MKTPLACE", 1875),
    ("2026-03-09", "MOT TEST CENTRE", 5485),
    ("2026-03-14", "ODEON CINEMA", 1450),
    ("2026-03-18", "APCOA PARKING", 950),
)


def spending() -> list[tuple[date, str, int]]:
    """(date, description, pence spent), oldest first."""
    out = [
        (date(int(m[:4]), int(m[5:]), day), desc, pence + 37 * i)
        for i, m in enumerate(MONTHS)
        for day, desc, pence in MONTHLY
    ]
    out += [(date.fromisoformat(d), desc, p) for d, desc, p in ONE_OFF]
    return sorted(out)


def rows() -> list[tuple[int, str, str, int]]:
    """For `synthetic_assistant.seed`: (day after START, type, description,
    signed pence), salary on the 1st of each month."""
    pay = [
        (date(int(m[:4]), int(m[5:]), 1), "ACME LTD SALARY", -250000) for m in MONTHS
    ]
    return [
        ((d - START).days, "FPI" if p < 0 else "DEB", desc, -p)
        for d, desc, p in sorted(pay + spending())
    ]


FUEL = ("SHELL KINGS CROSS", "ESSO MOTORWAY SVC", "BP CONNECT", "TESCO PFS 4471")
PARKING = ("NCP CAR PARK", "RINGGO PARKING", "APCOA PARKING")
GARAGE = ("KWIK FIT", "HALFORDS AUTOCENTRE", "MOT TEST CENTRE")
CAR = (*FUEL, *PARKING, *GARAGE, "DVLA VEHICLE TAX", "ADMIRAL INSURANCE")
COFFEE = ("PRET A MANGER 0231", "COSTA COFFEE", "STARBUCKS")
TAKEAWAY = ("DELIVEROO 2BX9", "JUST EAT", "GREGGS")
GROCERIES = ("TESCO STORES 3214", "SAINSBURYS", "LIDL")
EATING_OUT = ("WAGAMAMA", "NANDOS")
HOME = ("OCTOPUS ENERGY", "THAMES WATER", "EE LIMITED", "COUNCIL TAX LBC")
BILLS = (*HOME, "ADMIRAL INSURANCE", "AVIVA HOME INSURANCE", "DVLA VEHICLE TAX")
CAR_PLAN: dict[str, Any] = {
    "categories": ["Transport > Fuel", "Transport > Parking"],
    "merchants": ["DVLA", "Admiral", "Kwik Fit", "Halfords"],
    "keywords": ["MOT"],
}
YEAR: dict[str, Any] = {"since": "2026-01", "until": "2026-03"}
SINCE_OCT: dict[str, Any] = {"since": "2025-10", "until": "2026-03"}


def _m(month: str) -> dict[str, str]:
    return {"since": month, "until": month}


class Case(NamedTuple):
    id: str
    question: str
    args: dict[str, Any]  # the oracle's spend_for_query arguments
    gold: tuple[str, ...]  # descriptions of the Merchants that count
    since: str
    until: str
    after: str | None = None  # the case this one narrows


def _case(
    id: str,
    question: str,
    args: dict[str, Any],
    gold: tuple[str, ...],
    span: dict[str, str],
    after: str | None = None,
) -> Case:
    return Case(id, question, args, gold, span["since"], span["until"], after)


def _follow(
    id: str, question: str, args: dict[str, Any], gold: tuple[str, ...], after: str
) -> Case:
    first = BY_ID[after]
    return Case(
        id, question, args | {"follow_up": True}, gold, first.since, first.until, after
    )


BY_ID: dict[str, Case] = {}
_FIRST = (
    _case("car-year", "What did my car cost me this year?", CAR_PLAN | YEAR, CAR, YEAR),
    _case(
        "car-since-october",
        "What has running the car cost since October?",
        CAR_PLAN | SINCE_OCT,
        CAR,
        SINCE_OCT,
    ),
    _case(
        "car-january",
        "What did the car cost me in January?",
        CAR_PLAN | _m("2026-01"),
        CAR,
        _m("2026-01"),
    ),
    _case(
        "car-february",
        "What did the car cost in February, garage and all?",
        CAR_PLAN | _m("2026-02"),
        CAR,
        _m("2026-02"),
    ),
    _case(
        "car-march",
        "How much did the car cost me last month? I mean March.",
        CAR_PLAN | _m("2026-03"),
        CAR,
        _m("2026-03"),
    ),
    _case(
        "car-servicing",
        "How much have servicing, repairs and the MOT cost since October?",
        {"merchants": ["Kwik Fit", "Halfords"], "keywords": ["MOT"]} | SINCE_OCT,
        GARAGE,
        SINCE_OCT,
    ),
    _case(
        "car-tax-insurance",
        "What did car tax and car insurance cost since October?",
        {"merchants": ["DVLA", "Admiral"]} | SINCE_OCT,
        ("DVLA VEHICLE TAX", "ADMIRAL INSURANCE"),
        SINCE_OCT,
    ),
    _case(
        "fuel-q4",
        "How much fuel did I buy in October, November and December?",
        {"categories": ["Fuel"], "since": "2025-10", "until": "2025-12"},
        FUEL,
        {"since": "2025-10", "until": "2025-12"},
    ),
    _case(
        "parking-year",
        "What has parking cost this year?",
        {"categories": ["Parking"]} | YEAR,
        PARKING,
        YEAR,
    ),
    _case(
        "food-february",
        "How much did food cost me in February, shopping and eating out together?",
        {"categories": ["Food & Drink"]} | _m("2026-02"),
        (*GROCERIES, *COFFEE, *TAKEAWAY, *EATING_OUT),
        _m("2026-02"),
    ),
    _case(
        "groceries-year",
        "What did I spend on groceries this year?",
        {"categories": ["Groceries"]} | YEAR,
        GROCERIES,
        YEAR,
    ),
    _case(
        "coffee-takeaway-february",
        "How much went on coffee and takeaways in February?",
        {"categories": ["Coffee", "Takeaway"]} | _m("2026-02"),
        (*COFFEE, *TAKEAWAY),
        _m("2026-02"),
    ),
    _case(
        "coffee-year",
        "What did coffee shops cost me this year?",
        {"categories": ["Food & Drink > Coffee"]} | YEAR,
        COFFEE,
        YEAR,
    ),
    _case(
        "delivery-since-october",
        "How much on Deliveroo and Just Eat since October?",
        {"merchants": ["Deliveroo", "Just Eat"]} | SINCE_OCT,
        ("DELIVEROO 2BX9", "JUST EAT"),
        SINCE_OCT,
    ),
    _case(
        "restaurants-year",
        "What did eating out at restaurants cost this year?",
        {"categories": ["Eating out"]} | YEAR,
        EATING_OUT,
        YEAR,
    ),
    _case(
        "supermarkets-february",
        "How much did I spend at Sainsbury's and Lidl in February?",
        {"merchants": ["Sainsbury's", "Lidl"]} | _m("2026-02"),
        ("SAINSBURYS", "LIDL"),
        _m("2026-02"),
    ),
    _case(
        "coffee-greggs-december",
        "How much did coffee and Greggs cost in December?",
        {"categories": ["Coffee"], "merchants": ["Greggs"]} | _m("2025-12"),
        (*COFFEE, "GREGGS"),
        _m("2025-12"),
    ),
    _case(
        "home-bills-year",
        "What do energy, water, broadband and council tax come to this year?",
        {
            "categories": [
                "Bills > Energy",
                "Bills > Water",
                "Bills > Phone & broadband",
                "Bills > Council tax",
            ]
        }
        | YEAR,
        HOME,
        YEAR,
    ),
    _case(
        "energy-water-february",
        "How much were energy and water in February?",
        {"categories": ["Energy", "Water"]} | _m("2026-02"),
        ("OCTOPUS ENERGY", "THAMES WATER"),
        _m("2026-02"),
    ),
    _case(
        "phone-since-october",
        "What did my phone and broadband cost since October?",
        {"categories": ["Phone & broadband"]} | SINCE_OCT,
        ("EE LIMITED",),
        SINCE_OCT,
    ),
    _case(
        "home-insurance",
        "What did home insurance cost since October?",
        {"keywords": ["home insurance"]} | SINCE_OCT,
        ("AVIVA HOME INSURANCE",),
        SINCE_OCT,
    ),
    _case(
        "insurance-year",
        "How much do I pay for insurance, car and home, this year?",
        {"categories": ["Bills > Insurance"]} | YEAR,
        ("ADMIRAL INSURANCE", "AVIVA HOME INSURANCE"),
        YEAR,
    ),
    _case(
        "bills-february",
        "What did all my bills come to in February?",
        {"categories": ["Bills"]} | _m("2026-02"),
        BILLS,
        _m("2026-02"),
    ),
    _case(
        "subscriptions-year",
        "What did Netflix, Spotify and the gym cost me this year?",
        {"merchants": ["Netflix", "Spotify", "PureGym"]} | YEAR,
        ("NETFLIX", "SPOTIFY", "PUREGYM"),
        YEAR,
    ),
    _case(
        "health-year",
        "What did the gym and Boots cost me this year?",
        {"merchants": ["PureGym", "Boots"]} | YEAR,
        ("PUREGYM", "BOOTS"),
        YEAR,
    ),
    _case(
        "fun-year",
        "How much went on the cinema and streaming this year?",
        {"categories": ["Entertainment > Events", "Subscriptions > Streaming"]} | YEAR,
        ("ODEON CINEMA", "NETFLIX", "SPOTIFY"),
        YEAR,
    ),
    _case(
        "trips-since-october",
        "What did my trips cost since October: flights, hotels and trains?",
        {
            "categories": ["Travel > Flights", "Travel > Hotels"],
            "merchants": ["Trainline"],
        }
        | SINCE_OCT,
        ("EASYJET", "PREMIER INN", "TRAINLINE"),
        SINCE_OCT,
    ),
    _case(
        "getting-around-february",
        "How much did getting around cost in February: the tube and taxis?",
        {"categories": ["Public transport", "Taxi"]} | _m("2026-02"),
        ("TFL TRAVEL CH", "TRAINLINE", "UBER TRIP"),
        _m("2026-02"),
    ),
    _case(
        "tesco-year",
        "How much did I spend at Tesco this year, the shop and the fuel?",
        {"keywords": ["Tesco"]} | YEAR,
        ("TESCO STORES 3214", "TESCO PFS 4471"),
        YEAR,
    ),
    _case(
        "diy-since-october",
        "What did DIY and household bits from Screwfix and Argos cost since October?",
        {"merchants": ["Screwfix", "Argos"]} | SINCE_OCT,
        ("SCREWFIX", "ARGOS"),
        SINCE_OCT,
    ),
    _case(
        "shopping-since-october",
        "What did clothes and online shopping cost since October?",
        {"categories": ["Clothing", "Online"]} | SINCE_OCT,
        ("ZARA", "AMAZON MKTPLACE"),
        SINCE_OCT,
    ),
    _case(
        "transport-and-car-march",
        "What did all my transport and the car cost in March?",
        {
            "categories": ["Transport"],
            **{k: CAR_PLAN[k] for k in ("merchants", "keywords")},
        }
        | _m("2026-03"),
        (*CAR, "TFL TRAVEL CH", "UBER TRIP"),
        _m("2026-03"),
    ),
)
for _c in _FIRST:
    BY_ID[_c.id] = _c
_FOLLOW = (
    _follow(
        "car-year-fuel",
        "Of that, how much was fuel?",
        {"categories": ["Fuel"]},
        FUEL,
        "car-year",
    ),
    _follow(
        "car-year-parking",
        "And of that, how much was parking?",
        {"categories": ["Transport > Parking"]},
        PARKING,
        "car-year",
    ),
    _follow(
        "car-since-october-garage",
        "Of that, what went on the garage and the MOT?",
        {"merchants": ["Kwik Fit", "Halfords"], "keywords": ["MOT"]},
        GARAGE,
        "car-since-october",
    ),
    _follow(
        "coffee-takeaway-pret",
        "Of that, how much was Pret?",
        {"merchants": ["Pret A Manger"]},
        ("PRET A MANGER 0231",),
        "coffee-takeaway-february",
    ),
    _follow(
        "coffee-year-starbucks",
        "Of that, how much was Starbucks?",
        {"merchants": ["Starbucks"]},
        ("STARBUCKS",),
        "coffee-year",
    ),
    _follow(
        "insurance-year-car",
        "Of that, how much was the car?",
        {"merchants": ["Admiral"]},
        ("ADMIRAL INSURANCE",),
        "insurance-year",
    ),
    _follow(
        "subscriptions-streaming",
        "Of that, how much was just streaming?",
        {"categories": ["Subscriptions > Streaming"]},
        ("NETFLIX", "SPOTIFY"),
        "subscriptions-year",
    ),
    _follow(
        "trips-flights",
        "Of that, how much were the flights?",
        {"categories": ["Flights"]},
        ("EASYJET",),
        "trips-since-october",
    ),
    _follow(
        "tesco-fuel",
        "Of that, how much was fuel?",
        {"categories": ["Fuel"]},
        ("TESCO PFS 4471",),
        "tesco-year",
    ),
    _follow(
        "transport-march-uber",
        "Of that, how much was Uber?",
        {"merchants": ["Uber"]},
        ("UBER TRIP",),
        "transport-and-car-march",
    ),
    _follow(
        "parking-year-ncp",
        "Of that, how much was NCP?",
        {"merchants": ["NCP"]},
        ("NCP CAR PARK",),
        "parking-year",
    ),
)
CASES: tuple[Case, ...] = (*_FIRST, *_FOLLOW)
BY_ID.update({c.id: c for c in _FOLLOW})


def gold_total(case: Case) -> int:
    """Pence spent at the gold Merchants in the case's range, from the rows."""
    return sum(
        p
        for d, desc, p in spending()
        if desc in case.gold and case.since <= f"{d:%Y-%m}" <= case.until
    )


def fingerprint(cases: tuple[Case, ...] = CASES) -> str:
    raw = json.dumps([c._asdict() for c in cases] + [rows()], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()
