"""The one sanitiser for bank-supplied text (descriptions, Merchant names) before
it reaches the model (docs/AI_DESIGN.md, THREAT_MODEL: prompt injection).

Folds the text to plain printable ASCII, so zero-width, bidi, tag and lookalike
characters cannot hide a phrase, then cuts it at the first instruction-like
marker: what follows a marker is the payload, so none of it is kept."""

import re
import unicodedata

MAX_TEXT = 60

# Cyrillic and Greek letters NFKC leaves alone but that read as Latin ones
_LOOKALIKES = str.maketrans(
    "аеорсухіјѕӏԁԛԝАВЕКМНОРСТХІЈЅοαινκτρυΑΒΕΗΙΚΜΝΟΡΤΥΧ",
    "aeopcyxijsldqwABEKMHOPCTXIJSoaivktpuABEHIKMNOPTYX",
)

# Cut at: markup and tool-call syntax, URLs, role markers, and imperatives aimed
# at the model. Bank text never needs any of them.
_MARKER = re.compile(
    r"""[<>{}\[\]|#`\\]
    | https?:// | \bwww\.
    | \b(?:system|assistant|user|tool|developer|human)\s*:
    | \b(?:ignore|disregard|forget|override|bypass|jailbreak|pretend|roleplay)\b
    | \b(?:you\s+are\s+(?:now|an?)|act\s+as|instructions?|prompt|please)\b
    | \b(?:call|use|run|invoke)\s+(?:the\s+|a\s+)?(?:tool|function)
    | \b(?:tell|ask)\s+the\s+user\b
    | \b(?:say|respond|reply|answer)\s+(?:that|with|you)\b
    | \brepeat\s+after\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_LOOKALIKES)
    out: list[str] = []
    for ch in unicodedata.normalize("NFKD", text):
        if ch.isspace():
            out.append(" ")
        elif " " < ch <= "~":
            out.append(ch)
        # anything else (accents, zero-width, bidi, tags, other scripts) is dropped
    return " ".join("".join(out).split())


def sanitise(text: str) -> str:
    """Short, inert, printable ASCII; idempotent."""
    text = _fold(text)[:MAX_TEXT]
    if m := _MARKER.search(text):
        text = text[: m.start()]
    return text.strip()
