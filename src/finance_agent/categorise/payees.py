"""Payee-type rules for Review suggestions: checked before the kNN, so a
payment to a person, the government, a university or an AI service is never
offered the Category of its nearest grocer, and a keyword lexicon for plainly
named independents ("... FOOD AND WINE", "... FAST FOOD", "TOWN MARKET") and
software brands. They only suggest; a Correction
is still the only thing that assigns. Evaluated in evals/review_suggestions.py.

Taxonomy v2 has no Transfers, People or Education Category. A payment to a
person gets no suggestion at all (it is asked about one at a time), and
government and tuition payees take Bills/Other bills, where the UK base files
HMRC and the Student Loans Company.
"""

import re

type Category = tuple[str, str]

# a payee whose name reads as one of these gets that Category, first match wins
RULES: tuple[tuple[re.Pattern[str], Category], ...] = (
    (
        re.compile(
            r"\b(DWP|DEPT FOR WORK|DEPARTMENT FOR WORK|HMRC|HM REVENUE|HMCTS"
            r"|GOV\.UK|GOVUK|HM COURTS|DVLA|CHILD MAINTENANCE)\b"
        ),
        ("Bills", "Other bills"),
    ),
    (
        re.compile(r"\b(FLYWIRE|UNIVERSITY|UNIV|TUITION|COURSE FEES?|SCHOOL FEES?)\b"),
        ("Bills", "Other bills"),
    ),
    (
        re.compile(
            r"(\bAI\b|\.AI\b|OPENAI|CHATGPT|ANTHROPIC|\bCLAUDE\b|PERPLEXITY"
            r"|MIDJOURNEY|MISTRAL|ELEVENLABS|COPILOT|GEMINI|DEEPSEEK|REPLIT"
            r"|\bPOE\b|RUNWAYML|\bSUNO\b|ANYSPHERE)"
        ),
        ("Subscriptions", "AI tools"),
    ),
    (
        re.compile(
            r"\b(VIKI|IQIYI|WETV|KOCOWA|CRUNCHYROLL|DRAMABOX|REELSHORT|SHORTMAX"
            r"|DRAMAS?|NETFLIX|DISNEY PLUS|DISNEYPLUS|STREAMING)\b"
        ),
        ("Subscriptions", "Streaming"),
    ),
    (
        re.compile(
            r"\b(CANVA|FIGMA|NOTION|ADOBE|GITHUB|JETBRAINS|ATLASSIAN|SLACK|ZOOM"
            r"|GRAMMARLY|LASTPASS|NORDVPN|EXPRESSVPN|SURFSHARK|SQUARESPACE|WIX"
            r"|GODADDY|MAILCHIMP|DUOLINGO|EVERNOTE|TODOIST|AUTODESK)\b"
        ),
        ("Subscriptions", "Software"),
    ),
    # the keyword lexicon: plainly named independents the base has no entry for
    (
        re.compile(
            r"\b(FAST FOOD|KEBABS?|PIZZAS?|PIZZERIA|GRILL|PERI PERI|CHICKEN"
            r"|CHIPPY|FISH (AND|&) CHIPS|FISH BAR|TAKEAWAY|TAKE AWAY)\b"
        ),
        ("Food & Drink", "Takeaway"),
    ),
    (
        re.compile(
            r"\b(FOOD (AND|&) WINES?|WINES?|OFF LICEN[CS]E|CONVENIENCE|MINI MARKET"
            r"|MINIMARKET|SUPERMARKET|MARKET|GROCERS?|GROCERY|FOOD STORE|LOCAL"
            r"|EXPRESS)\b"
        ),
        ("Food & Drink", "Groceries"),
    ),
    (re.compile(r"\b(COFFEE|ESPRESSO|ROASTERS?)\b"), ("Food & Drink", "Coffee")),
    (
        re.compile(
            r"\b(BRASSERIE|BISTRO|RESTAURANT|TRATTORIA|STEAKHOUSE|DINER|CAFE|CAFFE"
            r"|TAPAS|TAVERNA|EATERY)\b"
        ),
        ("Food & Drink", "Eating out"),
    ),
)

# Payment-type words a bank puts round a person's name
MARKER = re.compile(
    r"^(?:(?:FPO?|FPI|SO|STO|BP|TFR|TO|FASTER PAYMENTS?|STANDING ORDER"
    r"|BANK TRANSFER|MOBILE PAYMENT|PAYMENT TO|TRANSFER TO)\s+)+"
    r"|(?:\s+(?:FPO?|FPI|SO|STO|BP|TFR|FASTER PAYMENTS?|STANDING ORDER))+$"
)
TITLES = frozenset({"MR", "MRS", "MS", "MISS", "DR", "MX", "MASTER"})
# A word no personal name in a payee line carries
BUSINESS = frozenset(
    """LTD LIMITED PLC LLP INC CO COMPANY UK GB SHOP SHOPS STORE STORES CAFE
    COFFEE RESTAURANT BAR PUB KITCHEN GRILL FOOD FOODS MARKET MART SUPERMARKET
    SERVICES SERVICE GROUP HOLDINGS INTERNATIONAL TRADING SOLUTIONS AND THE OF
    FOR CLUB ACADEMY SCHOOL UNIVERSITY COLLEGE CENTRE CENTER HOTEL TAXI TAXIS
    CARS MOTORS GARAGE PAY PAYMENTS APP ONLINE COM NET TECH MEDIA STUDIO STUDIOS
    DESIGN SPORTS FITNESS GYM BAKERY BUTCHERS PHARMACY CHEMIST DENTAL CLINIC
    SALON BARBER BARBERS NAILS BEAUTY HAIR TRAVEL TOURS AIRWAYS RAIL BUS
    PARKING FUEL ENERGY WATER COUNCIL INSURANCE BANK FINANCE LOANS CREDIT
    CAPITAL DIRECT EXPRESS LOCAL MINI PLUS PRO HOUSE HOME BOX DELIVERY STREET
    ROAD NOODLE NOODLES PIZZA CURRY CHICKEN BURGER KEBAB WINE WINES BOOKS
    FLOWERS FLORIST TRUST CHARITY FOUNDATION SUBSCRIPTION MEMBERSHIP TICKETS
    WORLD CITY HALL PARK STATION GARDEN GARDENS ARMS INN LOUNGE DELI BISTRO
    BRASSERIE TAVERN TEA JUICE BAKERS ELECTRICAL PLUMBING BUILDERS MOBILE
    TELECOM DIGITAL SOFTWARE SYSTEMS LABS GAMES MUSIC FILMS CINEMA THEATRE""".split()
)
# Common first names, so a bare "FIRST LAST" with no marker is still a person.
# shortcut: a short public list; widen it from the eval's misses.
FIRST_NAMES = frozenset(
    """JAMES JOHN ROBERT MICHAEL WILLIAM DAVID RICHARD JOSEPH THOMAS CHARLES
    DANIEL MATTHEW ANTHONY MARK STEVEN PAUL ANDREW JOSHUA KEVIN BRIAN GEORGE
    EDWARD RYAN JACOB GARY NICHOLAS ERIC JONATHAN STEPHEN LARRY JUSTIN SCOTT
    BENJAMIN SAMUEL ALEXANDER PATRICK JACK HARRY OLIVER CHARLIE OSCAR LEO ALFIE
    FREDDIE ARCHIE NOAH LUKE ADAM CALLUM LIAM CONNOR JAMIE TOM BEN SAM JOE DAN
    CHRIS MATT ALEX TOBY MAX NATHAN ELLIOT HENRY ISAAC ETHAN
    MARY PATRICIA JENNIFER LINDA ELIZABETH BARBARA SUSAN JESSICA SARAH KAREN
    LISA NANCY BETTY SANDRA MARGARET ASHLEY EMILY DONNA MICHELLE CAROL AMANDA
    MELISSA DEBORAH STEPHANIE REBECCA LAURA HELEN AMY ANNA EMMA OLIVIA SOPHIE
    CHLOE JESSICA LUCY GRACE ELLA MIA ISLA AVA POPPY HANNAH KATIE CHARLOTTE
    LAUREN BETH HOLLY GEORGIA MEGAN ABIGAIL ZOE RACHEL NATALIE JADE AMELIA
    MOHAMMED MUHAMMAD MOHAMMAD AHMED ALI HASSAN HUSSAIN OMAR USMAN BILAL HAMZA
    IBRAHIM YUSUF ZAIN ADNAN IMRAN ASIF FAISAL TARIQ KHALID RASHID SAMIR
    FATIMA AISHA AYESHA MARYAM ZAINAB SANA SARA HINA NADIA SAMINA RUBINA
    ESHAN RAHUL ROHAN ARJUN VIKRAM AMIT ANIL RAJ RAVI SANJAY SUNIL VIJAY ARUN
    KIRAN PRIYA ANJALI NEHA POOJA DIVYA SHREYA ANIKA ISHA NISHA
    WEI LI WANG ZHANG CHEN LIU YANG HUANG JUN MIN HAO YAN
    KWAME KOFI CHIDI EMEKA TUNDE SEUN FEMI AMARA NGOZI ADAEZE
    JOSE LUIS CARLOS JUAN MARIA ANA LUCIA SOFIA PIOTR TOMASZ ANNA MAGDA
    KASIA AGNIESZKA""".split()
)
_WORD = re.compile(r"[A-Z][A-Z'-]*")


def person(name: str) -> bool:
    """A payee that reads as a person's name. Conservative: a title, an
    initial beside a surname, a payment-type marker, or a common first name,
    and never a word that only a business carries."""
    upper = " ".join(name.upper().split())
    bare = MARKER.sub("", upper).strip()
    marked = bare != upper
    words = bare.split()
    if not 2 <= len(words) <= 4 or not all(_WORD.fullmatch(w) for w in words):
        return False  # digits, "&", "." or "*": a shop, a ref or a web address
    if any(w in BUSINESS for w in words):
        return False
    if words[0] in TITLES:
        return True
    long = [w for w in words if len(w) > 1]
    if not long:
        return False  # all initials: "B Q", "H M"
    initials = len(words) - len(long)
    if initials and len(long) == 1 and len(long[0]) >= 3:
        return True  # "J SMITH", "J A SMITH", "SMITH J"
    if marked and len(words) <= 3:
        return True
    return words[0] in FIRST_NAMES and initials == 0 and len(words) <= 3


def rule(name: str) -> Category | None:
    """The Category a payee-type rule gives this payee name, if any."""
    upper = " ".join(name.upper().split())
    return next((c for p, c in RULES if p.search(upper)), None)
