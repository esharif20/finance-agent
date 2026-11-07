"""The versioned Category taxonomy. It lives under the Ledger, not
`categorise`, because the Ledger resolves Corrections made on an older version
to today's names on read; `categorise` seeds it and re-exports it.

A Correction keeps the Category row it was made on (ADR 0001); `current` maps
that row's names to the current version, so it is never lost or re-pointed.
"""

UNCATEGORISED = "Uncategorised"  # the catch-all Category; categorise seeds it

Taxonomy = dict[str, tuple[str, ...]]

# Two levels: top level -> subcategories (<= 8 each). Never edit a published
# version: add the next one, bump TAXONOMY_VERSION and map its renamed leaves.
TAXONOMIES: dict[int, Taxonomy] = {
    1: {
        "Food & Drink": ("Groceries", "Eating out", "Takeaway", "Coffee"),
        "Transport": ("Public transport", "Taxi", "Fuel", "Parking"),
        "Shopping": ("Online", "Clothing", "Electronics", "Household"),
        "Bills & Utilities": ("Energy", "Water", "Phone & internet", "Council tax"),
        "Subscriptions": ("Streaming", "Software", "Memberships"),
        "Housing": ("Rent", "Mortgage", "Maintenance"),
        "Health": ("Pharmacy", "Fitness", "Medical"),
        "Entertainment": ("Events", "Games", "Hobbies"),
        "Travel": ("Flights", "Hotels", "Holidays"),
        "Personal care": ("Hair & beauty", "Toiletries"),
        UNCATEGORISED: (UNCATEGORISED,),
    },
    2: {
        "Food & Drink": ("Groceries", "Eating out", "Takeaway", "Coffee"),
        "Transport": ("Public transport", "Taxi", "Fuel", "Parking"),
        "Shopping": ("Online", "Clothing", "Electronics", "Household"),
        "Bills": (
            "Rent or mortgage",
            "Energy",
            "Water",
            "Council tax",
            "Phone & broadband",
            "Insurance",
            "Other bills",
        ),
        "Subscriptions": (
            "Streaming",
            "Software",
            "AI tools",
            "Memberships",
            "Cloud storage",
            "Other subscriptions",
        ),
        "Housing": ("Maintenance",),
        "Health": ("Pharmacy", "Fitness", "Medical"),
        "Entertainment": ("Events", "Games", "Hobbies"),
        "Travel": ("Flights", "Hotels", "Holidays"),
        "Personal care": ("Hair & beauty", "Toiletries"),
        UNCATEGORISED: (UNCATEGORISED,),
    },
}
TAXONOMY_VERSION = max(TAXONOMIES)
TAXONOMY = TAXONOMIES[TAXONOMY_VERSION]

# version -> {leaf in that version: its leaf in the next}; unlisted leaves keep
# their names.
RENAMES: dict[int, dict[tuple[str, str], tuple[str, str]]] = {
    1: {
        ("Bills & Utilities", "Energy"): ("Bills", "Energy"),
        ("Bills & Utilities", "Water"): ("Bills", "Water"),
        ("Bills & Utilities", "Phone & internet"): ("Bills", "Phone & broadband"),
        ("Bills & Utilities", "Council tax"): ("Bills", "Council tax"),
        ("Housing", "Rent"): ("Bills", "Rent or mortgage"),
        ("Housing", "Mortgage"): ("Bills", "Rent or mortgage"),
    },
}


def current(version: int, category: tuple[str, str]) -> tuple[str, str]:
    """A Category named under taxonomy `version`, in today's names."""
    for v in range(version, TAXONOMY_VERSION):
        category = RENAMES.get(v, {}).get(category, category)
    return category
