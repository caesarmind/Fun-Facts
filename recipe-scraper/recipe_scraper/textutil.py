"""Text helpers for Georgian recipe text: cleaning, light stemming, match keys."""
from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")
_DIGITS = re.compile(r"\d+")
_PARENS = re.compile(r"\([^)]*\)|\[[^\]]*\]")

GEO_VOWELS = "აეიოუ"


def clean(text: str | None) -> str:
    """Normalize unicode and collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\xa0", " ").replace("​", "").replace("﻿", "")
    return _WS.sub(" ", text).strip()


def fold(text: str | None) -> str:
    """Lowercase (also maps Georgian Mtavruli capitals to Mkhedruli)."""
    return clean(text).casefold()


# Case endings, longest first. Applied once per word.
_SUFFIXES = (
    "ებისთვის", "ისთვის", "ებიდან", "ებით", "ებში", "ებზე", "ების", "ებმა",
    "ებს", "ები", "იდან", "ითა", "ისა", "თან", "ით", "ის", "ში", "ზე", "ად",
    "მა", "ს", "ი",
)


def stem(word: str) -> str:
    """Very light Georgian stemmer used only to build match keys.

    Strips one case/plural ending, then collapses the syncope alternation
    (ქათამი/ქათმის, ნიგოზი/ნიგვზის, პომიდორი/პომიდვრის) so nominative and
    oblique forms land on the same key. Not linguistically exact, but applied
    identically to every word, so related forms usually collide.
    """
    w = word
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 2:
            w = w[: -len(suf)]
            break
    # Vowel stems keep their vowel in the nominative only (რძე/რძის, სოკო/სოკოს).
    if len(w) >= 3 and w[-1] in "აეოუ":
        w = w[:-1]
    if len(w) >= 4:
        a, v, c = w[-3], w[-2], w[-1]
        if a not in GEO_VOWELS and c not in GEO_VOWELS and v in "აეო":
            w = w[:-2] + ("ვ" if v == "ო" else "") + c
    return w


def tokens(text: str, stop: set[str] | frozenset[str] = frozenset()) -> list[str]:
    text = _PARENS.sub(" ", fold(text))
    text = _PUNCT.sub(" ", text)
    text = _DIGITS.sub(" ", text)
    return [t for t in text.split() if t and t not in stop]


def match_key(text: str, stop: set[str] | frozenset[str] = frozenset()) -> str:
    """Order-insensitive stemmed key, e.g. 'ქათმის სალათა' == 'სალათა ქათმის'."""
    return " ".join(sorted({stem(t) for t in tokens(text, stop)}))


def to_float(text: str) -> float | None:
    try:
        return float(text.replace(",", "."))
    except (ValueError, AttributeError):
        return None


_DURATION_ISO = re.compile(
    r"P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+(?:\.\d+)?)H)?(?:(?P<m>\d+(?:\.\d+)?)M)?(?:(?P<s>\d+)S)?)?", re.I
)


def parse_minutes(text: str | int | float | None) -> int | None:
    """'0:45:00', 'PT1H30M', '1 სთ 30 წთ', '45 წუთი', 90 -> minutes."""
    if text is None or text == "":
        return None
    if isinstance(text, (int, float)):
        return int(text)
    t = clean(str(text))
    m = re.fullmatch(r"(\d+):(\d{1,2})(?::\d{1,2})?", t)  # H:MM or H:MM:SS
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = _DURATION_ISO.fullmatch(t)
    if m and any(m.group(k) for k in "dhms"):
        total = float(m.group("d") or 0) * 1440 + float(m.group("h") or 0) * 60 + float(m.group("m") or 0)
        return int(round(total))
    hours = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:სთ|საათ)", t)
    mins = re.search(r"(\d+)\s*(?:წთ|წუთ|წ\b|min)", t)
    if hours or mins:
        total = (to_float(hours.group(1)) or 0) * 60 if hours else 0
        total += int(mins.group(1)) if mins else 0
        return int(round(total))
    if re.fullmatch(r"\d+", t):
        return int(t)
    return None


def first_number(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", str(text))
    return to_float(m.group(0)) if m else None
