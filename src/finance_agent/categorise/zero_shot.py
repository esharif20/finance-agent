"""Zero-shot Category suggestions from the Merchant vectors (ADR 0009: no
training). Each taxonomy subcategory has a short description; its vector is
embedded once per model version, at sync time, and held in memory. A
Merchant's suggestion is its nearest description, only when that one beats
the runner-up by the model's calibrated margin (evals/review_suggestions.py).
The request path never calls Ollama: with no cached vectors it suggests
nothing."""

import logging
from collections.abc import Mapping

from finance_agent.categorise import embedding
from finance_agent.categorise.embedding import Embedder, Vector

log = logging.getLogger(__name__)  # counts only

type Category = tuple[str, str]

DESCRIPTIONS: dict[Category, str] = {
    (
        "Food & Drink",
        "Groceries",
    ): "supermarket grocer convenience store mini market food and wine off licence",
    ("Food & Drink", "Eating out"): "restaurant bistro brasserie cafe dining pub meal",
    (
        "Food & Drink",
        "Takeaway",
    ): "takeaway fast food kebab pizza fried chicken grill delivery",
    ("Food & Drink", "Coffee"): "coffee shop espresso bar cafe latte",
    ("Transport", "Public transport"): "train rail bus tram underground metro tickets",
    ("Transport", "Taxi"): "taxi cab minicab ride hailing",
    ("Transport", "Fuel"): "petrol station fuel diesel forecourt",
    ("Transport", "Parking"): "car park parking meter",
    ("Shopping", "Online"): "online marketplace web shop",
    ("Shopping", "Clothing"): "clothing fashion shoes apparel boutique",
    ("Shopping", "Electronics"): "electronics computers phones gadgets",
    ("Shopping", "Household"): "household homeware hardware diy furniture",
    ("Bills", "Rent or mortgage"): "rent landlord letting agent mortgage",
    ("Bills", "Energy"): "electricity gas energy supplier",
    ("Bills", "Water"): "water supply sewerage",
    ("Bills", "Council tax"): "council tax borough local authority",
    ("Bills", "Phone & broadband"): "mobile phone broadband internet telecom",
    ("Bills", "Insurance"): "insurance cover policy premium",
    ("Bills", "Other bills"): "government tax licence fees tuition",
    ("Subscriptions", "Streaming"): "streaming video music tv series dramas",
    ("Subscriptions", "Software"): "software app design tool saas licence",
    ("Subscriptions", "AI tools"): "artificial intelligence ai assistant chatbot",
    ("Subscriptions", "Memberships"): "membership club society",
    ("Subscriptions", "Cloud storage"): "cloud storage backup drive",
    ("Subscriptions", "Other subscriptions"): "subscription monthly plan",
    ("Housing", "Maintenance"): "plumber electrician builder repairs maintenance",
    ("Health", "Pharmacy"): "pharmacy chemist prescriptions",
    ("Health", "Fitness"): "gym fitness sports yoga",
    ("Health", "Medical"): "dentist doctor clinic optician hospital vet",
    ("Entertainment", "Events"): "cinema theatre concert tickets events",
    ("Entertainment", "Games"): "video games gaming console",
    ("Entertainment", "Hobbies"): "hobbies crafts books music instruments",
    ("Travel", "Flights"): "airline flights airways",
    ("Travel", "Hotels"): "hotel hostel accommodation inn",
    ("Travel", "Holidays"): "holiday travel agent package tour",
    ("Personal care", "Hair & beauty"): "hairdresser barber salon nails beauty",
    ("Personal care", "Toiletries"): "toiletries cosmetics skincare",
}

# Embedder name -> margin (similarity thousandths) the best description must
# beat the runner-up by. Empty for a model not calibrated yet: it then
# suggests nothing. A test keeps this equal to the committed eval results.
MARGINS: dict[str, int] = {}

_cache: dict[str, dict[Category, Vector]] = {}


def prime(model: str, embedder: Embedder) -> None:
    """Embed the descriptions for `model` (a stored model version), once."""
    if model in _cache:
        return
    cats = sorted(DESCRIPTIONS)
    vecs = embedder.embed([DESCRIPTIONS[c] for c in cats])
    _cache[model] = dict(zip(cats, vecs, strict=True))
    log.info("zero-shot labels=%d", len(cats))


def labels(model: str) -> Mapping[Category, Vector] | None:
    return _cache.get(model)


def nearest(v: Vector, labels: Mapping[Category, Vector]) -> tuple[int, Category]:
    """(margin over the runner-up, the nearest Category); ties by name."""
    scored = sorted(
        (
            (embedding.similarity(v, lv), c)
            for c, lv in labels.items()
            if len(lv) == len(v)
        ),
        key=lambda x: (-x[0], x[1]),
    )
    if not scored:
        return 0, ("", "")
    second = scored[1][0] if len(scored) > 1 else -embedding.SIMILARITY
    return scored[0][0] - second, scored[0][1]


def margin(model: str) -> int | None:
    return MARGINS.get(model.split("@")[0])


def guess(model: str | None, v: Vector | None) -> Category | None:
    """The zero-shot Category for a Merchant vector, or None (no cached
    labels, an uncalibrated model, or too close a runner-up)."""
    if model is None or v is None:
        return None
    m, ls = margin(model), labels(model)
    if m is None or not ls:
        return None
    gap, cat = nearest(v, ls)
    return cat if gap >= m else None
