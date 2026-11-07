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

# Matched against a de-leeted copy (1GN0RE reads as IGNORE), same length, so the
# cut lands at the same place in the kept text.
_LEET = str.maketrans("0134578@$", "oieastbas")

# Cut at: markup, quotes and tool-call syntax, URLs, role markers, tool names,
# spelt-out words, and imperatives aimed at the model. Bank text never needs
# them; a false positive only shortens a description.
_MARKER = re.compile(
    r"""[<>{}\[\]|#`\\"] | \*\* | __
    | https?:// | \bwww\.
    | \b(?:system|assistant|user|tool|developer|human|ai|llm|gpt|model|bot)\s*:
    | \b(?:ignor|disregard|forget|overrid|bypass|jailbreak|pretend|roleplay|obey)
    | \b(?:you\s+are\s+(?:now|an?)|act\s+as|instructions?|prompt|please)\b
    | \b(?:call|use|run|invoke)\s+(?:the\s+|a\s+)?(?:tool|function)
    | \b(?:say|tell|respond|reply|reveal)\b | \brepeat\s+after\b
    | \banswer\s+(?:that|with|you)\b
    | \b(?:you|assistant|ai|model)\s+(?:must|should|shall|will|need)\b
    | \b(?:do\s+not|don'?t|never)\s+(?:mention|show|list|include|report)\b
    | \b(?:new|next)\s+(?:rules?|task)\b | \bnote\s+to\b | \bend\s+of\s+data\b
    | \b(?:output|print|show)\s+(?:the\s+|your\s+)?(?:system|memory|hidden)\b
    | \b[a-z]+_[a-z_]+\b
    | \b(?:[a-z][\s.*_-]+){3,}[a-z]\b
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
    while m := _MARKER.search(text.translate(_LEET)):  # a cut can expose a marker
        text = text[: m.start()].rstrip()
    return text.strip()
