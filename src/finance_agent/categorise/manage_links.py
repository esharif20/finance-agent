"""Where to manage a subscription, by Merchant (#97, moved from the web app by
#174). A reviewed, built-in list: a link is never taken from an email, a CSV
row or a description (phishing), and an unknown Merchant gets none. Every url
is https on the entry's own `domain`. Each `source` comment names the official
page reached; checked = 2026-11-07 (live HTTP check; sign-in redirects count as
answered). Not listed because no official url could be confirmed: NOW, EE."""

from typing import NamedTuple


class ManageLink(NamedTuple):
    names: tuple[str, ...]
    domain: str
    url: str


MANAGE_LINKS: tuple[ManageLink, ...] = (
    # source: netflix.com/youraccount
    ManageLink(
        (
            "Netflix",
            "Netflix.com",
        ),
        "netflix.com",
        "https://www.netflix.com/youraccount",
    ),
    # source: spotify.com/account
    ManageLink(("Spotify",), "spotify.com", "https://www.spotify.com/account/"),
    # source: amazon.co.uk/gp/primecentral (-> Membership Central)
    ManageLink(
        (
            "Amazon Prime",
            "Prime Video",
            "Amazon Prime Video",
        ),
        "amazon.co.uk",
        "https://www.amazon.co.uk/gp/primecentral",
    ),
    # source: disneyplus.com/account (-> /commerce/account)
    ManageLink(
        (
            "Disney Plus",
            "Disney+",
            "DisneyPlus",
        ),
        "disneyplus.com",
        "https://www.disneyplus.com/account",
    ),
    # source: youtube.com/paid_memberships
    ManageLink(
        (
            "YouTube Premium",
            "Youtube Member",
            "Google YouTube",
        ),
        "youtube.com",
        "https://www.youtube.com/paid_memberships",
    ),
    # source: apps.apple.com/account/subscriptions (-> Apple ID sign-in)
    ManageLink(
        (
            "Apple",
            "Apple.com/bill",
            "Apple Bill",
            "Apple Services",
        ),
        "apple.com",
        "https://apps.apple.com/account/subscriptions",
    ),
    # source: account.microsoft.com/services (-> Microsoft sign-in)
    ManageLink(
        (
            "Microsoft 365",
            "Microsoft",
            "Microsoft Office 365",
        ),
        "microsoft.com",
        "https://account.microsoft.com/services",
    ),
    # source: account.adobe.com/plans
    ManageLink(
        (
            "Adobe",
            "Adobe Creative Cloud",
        ),
        "adobe.com",
        "https://account.adobe.com/plans",
    ),
    # source: one.google.com/settings (-> Google sign-in)
    ManageLink(
        (
            "Google One",
            "Google Storage",
        ),
        "google.com",
        "https://one.google.com/settings",
    ),
    # source: audible.co.uk/account (-> Amazon sign-in)
    ManageLink(("Audible",), "audible.co.uk", "https://www.audible.co.uk/account"),
    # source: three.co.uk/my3 (-> /account)
    ManageLink(
        (
            "Three",
            "Three UK",
        ),
        "three.co.uk",
        "https://www.three.co.uk/account",
    ),
    # source: o2.co.uk/myo2 (-> /myvmo2)
    ManageLink(
        (
            "O2",
            "Virgin Media O2",
        ),
        "o2.co.uk",
        "https://www.o2.co.uk/myo2",
    ),
    # source: vodafone.co.uk/myvodafone (-> My Vodafone sign-in)
    ManageLink(
        ("Vodafone",), "vodafone.co.uk", "https://www.vodafone.co.uk/myvodafone"
    ),
    # source: giffgaff.com/auth/login
    ManageLink(("Giffgaff",), "giffgaff.com", "https://www.giffgaff.com/auth/login"),
    # source: virginmedia.com/my-virgin-media
    ManageLink(
        ("Virgin Media",),
        "virginmedia.com",
        "https://www.virginmedia.com/my-virgin-media",
    ),
    # source: sky.com/mysky
    ManageLink(("Sky",), "sky.com", "https://www.sky.com/mysky"),
    # source: bt.com/mybt (-> my.bt.com)
    ManageLink(("BT",), "bt.com", "https://www.bt.com/mybt"),
)
