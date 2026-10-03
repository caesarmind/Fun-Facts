"""Parse Georgian ingredient lines into quantity / unit / name / notes.

Handles the formats seen on Georgian recipe sites:
    "რძე - 300 მლ."                         (name - amount)
    "ბანანი - 1 ცალი (დაახლოებით 100–120 გრ)" (amount with a weight hint)
    "1 ს/კ ტომატის პასტა"                    (amount name)
    "ფქვილი 500 გრ" / "ფქვილი (500 გრ)"      (name amount)
    "მარილი გემოვნებით"                      (to taste)
    "მწვანილი და ბროწეულის მარცვლები გასაფორმებლად" (list without amounts)
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from .models import IngredientLine
from .textutil import clean, fold, to_float

# normalized unit -> (aliases, grams per unit, ml per unit)
UNITS: dict[str, tuple[list[str], float | None, float | None]] = {
    "kg": (["კილოგრამი", "კილოგრამის", "კილო", "კგ", "kg"], 1000, None),
    "mg": (["მილიგრამი", "მგ", "mg"], 0.001, None),
    "g": (["გრამი", "გრამის", "გრამით", "გრამს", "გრამ", "გრ", "გ", "gr", "g"], 1, None),
    "l": (["ლიტრი", "ლიტრის", "ლიტრ", "ლ", "l"], None, 1000),
    "ml": (["მილილიტრი", "მილილიტრის", "მლ/ლ", "მლ", "ml"], None, 1),
    "tbsp": (["სუფრის კოვზი", "სუფრის კოვზის", "სუფრის კოვზით", "სუფ. კოვზი", "სუფ.კოვზი",
              "სუფრის კ.", "ს/კოვზი", "ს/კ", "ს.კ.", "ს.კ", "სკ"], None, 15),
    "tsp": (["ჩაის კოვზი", "ჩაის კოვზის", "ჩაის კოვზით", "ჩაის კ.", "ჩ/კოვზი", "ჩ/კ", "ჩ.კ.", "ჩ.კ", "ჩკ"], None, 5),
    "dsp": (["დესერტის კოვზი", "დესერტის კოვზის", "დ/კ"], None, 10),
    "cup": (["ჩაის ჭიქა", "ჩაის ჭიქის", "ჩ/ჭ", "ჭიქა", "ჭიქის", "ჭიქით", "სტაქანი", "ჭ."], None, 250),
    "pcs": (["ცალი", "ცალის", "ცალს", "ცალ", "ც.", "ც", "ძირი", "ტარო"], None, None),
    "clove": (["კბილი", "კბილის", "კბ."], None, None),
    "head": (["თავი", "თავის"], None, None),
    "bunch": (["პატარა კონა", "დიდი კონა", "კონა", "კონის", "შეკვრა", "ბღუჯა"], None, None),
    "handful": (["მუჭა", "მუჭი", "პეშვი"], None, None),
    "pinch": (["მწიკვი", "პინჩი"], None, None),
    "slice": (["ნაჭერი", "ნაჭრი", "ფენა"], None, None),
    "pack": (["პაკეტი", "შეფუთვა", "კოლოფი", "პაჩკა", "პაკ."], None, None),
    "can": (["ქილა", "ბანკა"], None, None),
    "bottle": (["ბოთლი"], None, None),
    "leaf": (["ფოთოლი", "ფურცელი"], None, None),
    "stalk": (["ღერო", "ღერი", "ტოტი"], None, None),
    "drop": (["წვეთი"], None, None),
    "spoon": (["კოვზი"], None, None),
}

_UNIT_LOOKUP: dict[str, tuple[str, float | None, float | None]] = {}
for _norm, (_aliases, _g, _ml) in UNITS.items():
    for _a in _aliases:
        _UNIT_LOOKUP[fold(_a)] = (_norm, _g, _ml)

_UNIT_RE = "|".join(re.escape(a) for a in sorted(_UNIT_LOOKUP, key=len, reverse=True))
_UNIT = rf"(?P<unit>{_UNIT_RE})(?!\w)\.?"

_FRAC = {"½": 0.5, "⅓": 1 / 3, "⅔": 2 / 3, "¼": 0.25, "¾": 0.75, "⅕": 0.2, "⅖": 0.4, "⅗": 0.6, "⅘": 0.8,
         "⅙": 1 / 6, "⅚": 5 / 6, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875}
_FRAC_CHARS = "".join(_FRAC)
_WORD_NUM = {
    "ნახევარი": 0.5, "ნახევარ": 0.5, "მეოთხედი": 0.25, "ერთი": 1, "ორი": 2, "სამი": 3,
    "ოთხი": 4, "ხუთი": 5, "ექვსი": 6, "შვიდი": 7, "რვა": 8, "ცხრა": 9, "ათი": 10,
}
_NUM = (
    rf"(?:\d+(?:[.,]\d+)?\s*[{_FRAC_CHARS}]|\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d+(?:[.,]\d+)?|[{_FRAC_CHARS}]"
    rf"|(?<!\w)(?:{'|'.join(sorted(_WORD_NUM, key=len, reverse=True))})(?!\w))"
)
_QTY = rf"(?P<q1>{_NUM})(?:\s*(?:-|–|—|ან)\s*(?P<q2>{_NUM}))?"

TO_TASTE = [
    "გემოვნებით", "გემოს მიხედვით", "გემოზე", "სურვილისამებრ", "სურვილის მიხედვით",
    "საჭიროებისამებრ", "საჭიროების მიხედვით", "საჭირო რაოდენობით", "სასურველი რაოდენობით",
    "ნებისმიერი რაოდენობით", "ცოტაოდენი", "ოდნავ", "ცოტა", "რამდენიმე", "optional",
]
APPROX = ["დაახლოებით", "დაახლ.", "დაახ.", "მინიმუმ", "მაქსიმუმ", "~", "≈"]
_TO_TASTE_RE = re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in sorted(TO_TASTE, key=len, reverse=True)) + r")(?!\w)")
_APPROX_RE = re.compile(r"^(?:" + "|".join(re.escape(w) for w in APPROX) + r")\s*")
_LEAD_TASTE = re.compile(r"^(?:" + "|".join(re.escape(w) for w in TO_TASTE) + r")\s+")
# "გასაფორმებლად" (for decoration), "შესაწვავად" (for frying), "სერვირებისთვის" (for serving) ...
_PURPOSE_RE = re.compile(r"(?<!\w)(?:(?:გა|შე|მო|და|წა|ჩა|ა|გადა|ამო|ჩამო)?სა\w+ად|\w+ისთვის)(?!\w)")
_DECOR_RE = re.compile(r"გასაფორმებ|დეკორ|სერვირ")

_AMOUNT_START = re.compile(rf"^(?:{_NUM}|{'|'.join(re.escape(w) for w in APPROX + TO_TASTE)})", re.I)
_SEP = re.compile(r"\s+[-–—]\s+|\s*:\s+|\s+[-–—](?=\s*\d)")
_LEAD = re.compile(rf"^{_QTY}\s*(?:{_UNIT})?\s*(?P<rest>.*)$", re.I)
_TRAIL = re.compile(rf"^(?P<name>.+?)\s*[(,]?\s*{_QTY}\s*(?:{_UNIT})?\s*\)?$", re.I)
# Only unambiguous unit words may lead a line without a number (not "გ", "ლ", "ც" ...).
_UNIT_LEAD_RE = "|".join(re.escape(a) for a in sorted(_UNIT_LOOKUP, key=len, reverse=True)
                         if len(a) >= 4 or "/" in a)
_UNIT_LEAD = re.compile(rf"^(?P<unit>{_UNIT_LEAD_RE})(?!\w)\.?\s+(?P<rest>.+)$", re.I)
_WEIGHT_HINT = re.compile(r"(?P<q1>\d+(?:[.,]\d+)?)(?:\s*[-–—]\s*(?P<q2>\d+(?:[.,]\d+)?))?\s*(?P<u>კგ|გრამი|გრ|გ)(?!\w)")
_PARENS = re.compile(r"\(([^)]*)\)")
_HAS_LETTER = re.compile(r"[^\W\d_]")

# Descriptors that make a comma-separated part a note rather than another ingredient.
PREP_WORDS = {
    "დაჭრილი", "დაკეპილი", "დაქუცმაცებული", "გახეხილი", "მოხარშული", "შემწვარი", "გაფცქვნილი",
    "გათალული", "დაფქული", "დანაყილი", "გამდნარი", "გაცივებული", "გალღობილი", "გარეცხილი",
    "გაცრილი", "ახალი", "ახლად", "ცივი", "თბილი", "ცხელი", "წვრილად", "მსხვილად", "დიდი",
    "პატარა", "საშუალო", "ზომის", "კუბიკებად", "ნაჭრებად", "რგოლებად", "ზოლებად", "ოთახის",
    "ტემპერატურის", "ტემპერატურაზე", "დალბილებული", "გამხმარი", "გაყინული", "დამარილებული",
    "უმი", "უძვლო", "ძვლიანი", "კანიანი", "უკანო", "მთლიანი", "ფენებად", "თხლად", "მსხვილი",
    "წვრილი", "დაბალი", "მაღალი", "ცხიმიანობის", "ცხიმიანი", "უცხიმო", "ახლადდაფქული", "ახლადგამოწურული",
    "გამოწურული", "დაწურული", "გათლილი", "გარჩეული", "გაყოფილი", "ორად", "დასაჭრელი", "სამუშაოდ", "გასუფთავებული",
    "დარბილებული", "ათქვეფილი", "გათქვეფილი",
}


def parse_number(text: str | None) -> float | None:
    if not text:
        return None
    t = clean(text)
    if t in _WORD_NUM:
        return _WORD_NUM[t]
    m = re.fullmatch(r"(\d+)\s+(\d+)\s*/\s*(\d+)", t)
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3)) if int(m.group(3)) else None
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", t)
    if m:
        return int(m.group(1)) / int(m.group(2)) if int(m.group(2)) else None
    if t and t[-1] in _FRAC:
        base = to_float(t[:-1].strip()) if t[:-1].strip() else 0.0
        return (base or 0.0) + _FRAC[t[-1]]
    return to_float(t)


def unit_info(unit: str | None) -> tuple[str | None, float | None, float | None]:
    if not unit:
        return None, None, None
    return _UNIT_LOOKUP.get(fold(unit).rstrip("."), _UNIT_LOOKUP.get(fold(unit), (None, None, None)))


def is_group_header(text: str) -> bool:
    """'ცომისთვის:', 'შიგთავსი:', 'სოუსისთვის' -> sub-heading rather than an ingredient."""
    t = clean(text)
    if not t or re.search(r"\d", t):
        return False
    words = t.rstrip(":").split()
    if t.endswith(":") and len(words) <= 5:
        return True
    if not words[-1].endswith("თვის"):
        return False
    return len(words) == 1 or (len(words) == 2 and words[0].endswith("ის"))


def _strip_notes(name: str, notes: list[str]) -> tuple[str, bool]:
    """Move parentheses, to-taste and purpose phrases out of the name."""
    optional = False
    for m in _PARENS.finditer(name):
        if m.group(1).strip():
            notes.append(m.group(1).strip())
    name = _PARENS.sub(" ", name)
    for m in _TO_TASTE_RE.finditer(name):
        notes.append(m.group(1))
        optional = True
    name = _TO_TASTE_RE.sub(" ", name)
    for m in _PURPOSE_RE.finditer(name):
        notes.append(m.group(0))
        if _DECOR_RE.search(m.group(0)):
            optional = True
    name = _PURPOSE_RE.sub(" ", name)
    name = clean(name).strip(" -–—:;,.*•")
    return name, optional


def make_line(raw: str, name: str, qty_text: str | None = None, unit_text: str | None = None,
              group: str | None = None, notes: list[str] | None = None,
              qty_max_text: str | None = None) -> IngredientLine:
    """Build an IngredientLine from already-separated parts (used by structured sites too)."""
    notes = list(notes or [])
    name, optional = _strip_notes(clean(name), notes)
    # Alternatives: "გუანჩალე ან პანჩეტა" -> name = first option, rest kept as a note.
    alt = re.split(r"\s+ან\s+|\s+/\s+|(?<=[^\W\d_]{2})/(?=[^\W\d_]{2})", name, maxsplit=1)
    if len(alt) == 2 and alt[0] and alt[1]:
        name = alt[0].strip()
        notes.append("ან " + alt[1].strip())
    # "ქათმის ფილე, დაჭრილი" -> note
    if "," in name:
        head, tail = name.split(",", 1)
        name = head.strip()
        notes.append(tail.strip())

    qty = parse_number(qty_text) if qty_text else None
    qmax = parse_number(qty_max_text) if qty_max_text else None
    unit_text = clean(unit_text) or None
    norm, g_per, ml_per = unit_info(unit_text)
    mean_qty = (qty + qmax) / 2 if qty is not None and qmax is not None else qty

    grams = mean_qty * g_per if mean_qty is not None and g_per else None
    ml = mean_qty * ml_per if mean_qty is not None and ml_per else None
    if grams is None:
        # "1 ცალი (დაახლოებით 100–120 გრ)" -> weight hint from the notes
        for n in notes:
            m = _WEIGHT_HINT.search(n)
            if m:
                lo, hi = to_float(m.group("q1")), to_float(m.group("q2")) if m.group("q2") else None
                w = (lo + hi) / 2 if lo is not None and hi is not None else lo
                if w is not None:
                    grams = w * (1000 if m.group("u") == "კგ" else 1)
                break
    if qty is None and not optional and any(_TO_TASTE_RE.search(n) or _DECOR_RE.search(n) for n in notes):
        optional = True

    note = "; ".join(dict.fromkeys(n for n in (clean(x) for x in notes) if n)) or None
    return IngredientLine(
        raw=clean(raw), name=name, quantity=qty, quantity_max=qmax,
        quantity_text=clean(" - ".join(x for x in (qty_text, qty_max_text) if x)) or None,
        unit=unit_text, unit_norm=norm, grams=round(grams, 2) if grams is not None else None,
        ml=round(ml, 2) if ml is not None else None, note=note, group=group, optional=optional,
    )


def _parse_amount(amount: str) -> tuple[str | None, str | None, str | None, list[str]]:
    """'1 ცალი (დაახლოებით 100–120 გრ)' -> ('1', None, 'ცალი', ['დაახლოებით 100–120 გრ'])."""
    notes: list[str] = []
    a = clean(amount)
    m = _APPROX_RE.match(a)
    if m:
        notes.append(m.group(0).strip())
        a = a[m.end():]
    m = _LEAD.match(a)
    if not m:
        return None, None, None, [amount] if amount else []
    rest = clean(m.group("rest"))
    if rest:
        notes.append(rest.strip("()"))
    return m.group("q1"), m.group("q2"), m.group("unit"), notes


def _looks_like_note(part: str) -> bool:
    words = fold(part).split()
    return bool(words) and all(w in PREP_WORDS or _TO_TASTE_RE.fullmatch(w) or _PURPOSE_RE.fullmatch(w) for w in words)


def parse_line(raw: str, group: str | None = None) -> list[IngredientLine]:
    """Parse one ingredient line. Returns a list because amount-less lists
    ("მარილი, პილპილი") are split into separate ingredients."""
    text = clean(raw).strip(" •·*-–—;")
    text = re.sub(r"\.$", "", text)
    if not text:
        return []
    lead = _APPROX_RE.match(text) or _LEAD_TASTE.match(text)
    if lead and re.match(r"\d", text[lead.end():]):
        # "დაახლ. 500 გრ ფქვილი", "გემოვნებით 0.5 ჩ/კ პილპილი" -> parse the rest, keep the word as a note
        lines = parse_line(text[lead.end():], group)
        for i in lines:
            i.raw = clean(raw)
            i.note = "; ".join(x for x in (lead.group(0).strip(), i.note) if x)
            i.optional = i.optional or bool(_TO_TASTE_RE.match(lead.group(0)))
        return lines

    # 1) "name - amount" / "name: amount"
    for sep in _SEP.finditer(text):
        left, right = text[: sep.start()].strip(), text[sep.end():].strip()
        if _HAS_LETTER.search(left) and right and _AMOUNT_START.match(right):
            q1, q2, unit, notes = _parse_amount(right)
            return [make_line(raw, left, q1, unit, group, notes, q2)]

    # 2) "amount name"
    m = _LEAD.match(text)
    if m and m.group("rest") and not re.match(r"^[%°]", m.group("rest")):
        return [make_line(raw, m.group("rest"), m.group("q1"), m.group("unit"), group, None, m.group("q2"))]

    # 2b) unit without a number: "მწიკვი მარილი", "ჭიქა შაქარი", "ს/კ თაფლი" -> one of that unit
    m = _UNIT_LEAD.match(text)
    if m and m.group("rest"):
        return [make_line(raw, m.group("rest"), "1", m.group("unit"), group)]

    # 3) "name amount" / "name (amount)"
    m = _TRAIL.match(text)
    if m and _HAS_LETTER.search(m.group("name")):
        return [make_line(raw, m.group("name"), m.group("q1"), m.group("unit"), group, None, m.group("q2"))]

    # 4) name only — maybe a list of several ingredients without amounts
    notes: list[str] = []
    name, _ = _strip_notes(text, notes)
    parts = [p.strip() for p in re.split(r",\s*|\s+და\s+", name) if p.strip()]
    if len(parts) > 1 and all(len(p.split()) <= 4 for p in parts) and not any(_looks_like_note(p) for p in parts):
        return [make_line(raw, p, None, None, group, notes) for p in parts]
    return [make_line(raw, text, None, None, group)]


_CREDIT = re.compile(r"^(ავტ\.?|ავტორი|წყარო|source)(?!\w)", re.I)
# A sentence break ("…მხრიდან. მზა…") — but not abbreviations like "ს.კ. შაქარი" or "სუფ. კოვზი".
# "1 გემოვნებით ტაფის ხაჭაპური რომელსაც…" — a number followed by "to taste" and a long phrase.
_NUM_TASTE = re.compile(r"^\d+\s+(?:" + "|".join(re.escape(w) for w in TO_TASTE) + r")\s")
_SENTENCE = re.compile(r"[^\W\d_]{5,}\.\s+[^\W\d_]{2,}")


def is_plausible(line: IngredientLine) -> bool:
    """Reject lines that are clearly not ingredients (sentences, author credits) — some sites
    re-publish recipes with instructions or credits pasted into the ingredient list."""
    name = line.name
    raw = clean(line.raw)
    return (bool(name) and len(name.split()) <= 8 and len(name) <= 80
            and not _CREDIT.match(name) and not _SENTENCE.search(raw)
            and not (_NUM_TASTE.match(raw) and len(name.split()) >= 3))


def parse_lines(lines: Iterable[str], group: str | None = None) -> list[IngredientLine]:
    """Parse a sequence of lines, treating 'ცომისთვის:'-style lines as group headings."""
    out: list[IngredientLine] = []
    current = group
    for line in lines:
        t = clean(line)
        if not t:
            continue
        if is_group_header(t):
            current = t.rstrip(":").strip()
            continue
        out.extend(parse_line(t, current))
    return [i for i in out if i.name]
