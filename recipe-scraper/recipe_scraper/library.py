"""Build the ingredient library and dish library from scraped recipes.

Ingredient identity:  alias map -> drop prep/size words ("დაჭრილი", "დიდი") -> stemmed key,
so "ნიგოზი", "ნიგვზის" and "დაკეპილი ნიგოზი" share one library entry.

Dish identity: strip clickbait segments and filler words from titles
("ქოქოსის და ბანანის ბურთულები - მხოლოდ 3 ინგრედიენტით!" -> "ქოქოსის და ბანანის ბურთულები"),
then a stemmed, order-insensitive key ("აჯაფსანდალის რეცეპტი" == "აჯაფსანდალი").
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from importlib import resources
from pathlib import Path

from .db import DB
from .ingredients import PREP_WORDS
from .textutil import clean, fold, match_key, tokens

DEFAULT_ALIASES = "aliases.csv"

DISH_FILLER = {
    "რეცეპტი", "რეცეპტით", "რეცეპტის", "რეცეპტები", "ვიდეორეცეპტი", "ვიდეორეცეპტით", "ვიდეო",
    "უგემრიელესი", "გემრიელი", "ძალიან", "მარტივი", "მარტივად", "სწრაფი", "სწრაფად", "საოცარი",
    "საუკეთესო", "სახლში", "მომზადებული", "ორიგინალური", "კლასიკური", "ნამდვილი", "იდეალური",
    "არაჩვეულებრივი", "ულამაზესი", "უნაზესი", "ყველაზე", "ტრადიციული", "ჩემი", "ჩვენი", "ბებიას",
    "როგორ", "მოვამზადოთ", "მოამზადეთ", "მომზადება", "მომზადების", "წესი", "წესით", "ახლებური",
    "გემრიელად", "ჯანსაღი", "ერთ-ერთი", "სუპერ", "ნაზი", "ფაფუკი", "ხრაშუნა",
}
DISH_STOP = DISH_FILLER | {"და", "ან", "ერთად", "a", "the", "with", "and"}
CLICKBAIT = re.compile(
    r"\d|(?<!\w)(ეს|ასე|ამ|ამის|რომ|თუ|როგორ|მხოლოდ|წუთში|ოჯახ\w*|ყველა\w*|გააოცებ\w*|აგაფრთოვან\w*|"
    r"მკითხველ\w*|საიდუმლო\w*|დაგჭირდებათ|შეგიყვარდებათ|ნახეთ|გაიგეთ|უნდა|გამოდის|ვერ|არასდროს|არ)(?!\w)"
)
SEGMENT_SPLIT = re.compile(r"\s*[:|!?]\s*|\s+[-–—]\s+|,\s*(?=(?:რომელიც|რომლის|რაც|რომ)(?!\w))")
_PARENS = re.compile(r"\([^)]*\)|\[[^\]]*\]")


# ------------------------------------------------------------------ aliases
def load_aliases(extra: str | Path | None = None) -> dict[str, str]:
    """alias (folded) -> canonical display name. Package defaults, then the user's file."""
    out: dict[str, str] = {}
    text = resources.files("recipe_scraper.data").joinpath(DEFAULT_ALIASES).read_text(encoding="utf-8")
    sources = [text.splitlines()]
    if extra:
        sources.append(Path(extra).read_text(encoding="utf-8-sig").splitlines())
    for lines in sources:
        for row in csv.reader(lines):
            if len(row) >= 2 and row[0].strip() and not row[0].startswith("#") and row[0].strip() != "alias":
                out[fold(row[0])] = clean(row[1])
    return out


# --------------------------------------------------------------- identities
def ingredient_identity(name: str, aliases: dict[str, str]) -> tuple[str, str]:
    """-> (match key, display name)."""
    n = fold(name)
    n = aliases.get(n, n)
    words = [w for w in tokens(n) if w not in PREP_WORDS]
    display = " ".join(words) or clean(n)
    display = aliases.get(display, display)
    return match_key(display), display


def dish_name(title: str) -> str:
    """Pick the segment of a (possibly clickbait) title that names the dish."""
    t = clean(_PARENS.sub(" ", title or ""))
    segments = [s.strip(" .,'\"«»„“”") for s in SEGMENT_SPLIT.split(t) if s and s.strip(" .,'\"")]
    if not segments:
        return t
    scored = sorted(enumerate(segments), key=lambda p: (len(CLICKBAIT.findall(p[1])), p[0]))
    best = scored[0][1]
    words = [w for w in best.split() if fold(w).strip(".,!?") not in DISH_FILLER]
    name = clean(" ".join(words)) or best
    # "ფელამუში მარტივად და სწრაფად" -> "ფელამუში და" -> "ფელამუში"
    name = re.sub(r"^(?:(?:და|ან)\s+)+|(?:\s+(?:და|ან))+$", "", name)
    return clean(re.sub(r"\b(და|ან)(\s+\1)+\b", r"\1", name)) or best


def is_genitive_recipe_title(title: str) -> bool:
    """'ჩინური ტორტის რეცეპტი' -> the dish name keeps the genitive ending."""
    return bool(re.search(r"(?:ის|[აეოუ]ს)\s+რეცეპტ\w*\s*$", clean(title)))


def nominative(name: str, vocab: Counter) -> str:
    """Turn the last word of 'ჩინური ტორტის' back into the nominative ('ჩინური ტორტი'), choosing among
    the possible endings the form that appears most often elsewhere in the scraped titles."""
    words = name.split()
    if not words:
        return name
    w = words[-1]
    if w.endswith("ის") and len(w) > 3:
        stem = w[:-2]
        options = [stem + "ი", stem + "ა", stem + "ე", stem + "ო", stem + "უ"]
    elif len(w) > 3 and w.endswith("ს") and w[-2] in "აეოუ":
        options = [w[:-1]]
    else:
        return name
    best = max(options, key=lambda o: (vocab.get(o, 0), o.endswith("ი")))
    return " ".join(words[:-1] + [best])


def dish_identity(title: str, aliases: dict[str, str] | None = None) -> tuple[str, str]:
    name = dish_name(title)
    if aliases:
        name = aliases.get(fold(name), name)
    return match_key(name, DISH_STOP), name


# -------------------------------------------------------------------- build
def build(db: DB, aliases_path: str | Path | None = None) -> dict[str, int]:
    aliases = load_aliases(aliases_path)

    # ---- ingredients
    rows = db.query(
        "SELECT ri.recipe_id, ri.position, ri.name, ri.raw, ri.unit_norm, r.source "
        "FROM recipe_ingredients ri JOIN recipes r ON r.id = ri.recipe_id")
    groups: dict[str, dict] = defaultdict(lambda: {"variants": Counter(), "recipes": set(),
                                                   "per_site": defaultdict(set), "units": Counter(), "examples": []})
    updates = []
    recipe_keys: dict[int, list[str]] = defaultdict(list)
    for r in rows:
        key, display = ingredient_identity(r["name"] or "", aliases)
        updates.append((key or None, r["recipe_id"], r["position"]))
        if not key:
            continue
        g = groups[key]
        g["variants"][display] += 1
        g["recipes"].add(r["recipe_id"])
        g["per_site"][r["source"]].add(r["recipe_id"])
        g["units"][r["unit_norm"] or "-"] += 1
        if len(g["examples"]) < 5 and r["raw"] not in g["examples"]:
            g["examples"].append(r["raw"])
        recipe_keys[r["recipe_id"]].append(key)

    nutrition = _nutrition_index(db, aliases)
    ing_rows = []
    for key, g in groups.items():
        n = nutrition.get(key)
        ing_rows.append((
            key, g["variants"].most_common(1)[0][0], len(g["recipes"]), len(g["per_site"]),
            json.dumps({s: len(v) for s, v in sorted(g["per_site"].items())}, ensure_ascii=False),
            json.dumps(dict(g["variants"].most_common(10)), ensure_ascii=False),
            json.dumps(dict(g["units"].most_common()), ensure_ascii=False),
            json.dumps(g["examples"], ensure_ascii=False),
            *(n and (n["kcal"], n["protein"], n["fat"], n["carbs"], n["source"], n["name"]) or (None,) * 6),
        ))
    display_of = {row[0]: row[1] for row in ing_rows}

    # ---- dishes
    recipes = db.query("SELECT id, source, title, categories, url FROM recipes")
    dgroups: dict[str, dict] = defaultdict(lambda: {"variants": Counter(), "recipes": [], "per_site": Counter(),
                                                    "categories": Counter(), "ingredients": Counter(), "urls": []})
    vocab = Counter(w for r in recipes if not is_genitive_recipe_title(r["title"] or "")
                    for w in clean(r["title"]).split())
    dish_updates = []
    for r in recipes:
        key, name = dish_identity(r["title"] or "", aliases)
        if is_genitive_recipe_title(r["title"] or ""):
            name = nominative(name, vocab)
            weight = 0.5  # a sibling title in the nominative wins ties
        else:
            weight = 1
        dish_updates.append((name, key or None, r["id"]))
        if not key:
            continue
        g = dgroups[key]
        g["variants"][name] += weight
        g["recipes"].append(r["id"])
        g["per_site"][r["source"]] += 1
        g["categories"].update(json.loads(r["categories"] or "[]"))
        g["ingredients"].update(set(recipe_keys.get(r["id"], [])))
        if len(g["urls"]) < 10:
            g["urls"].append(r["url"])
    dish_rows = [(
        key, g["variants"].most_common(1)[0][0], len(g["recipes"]), len(g["per_site"]),
        json.dumps(dict(sorted(g["per_site"].items())), ensure_ascii=False),
        json.dumps(dict(g["variants"].most_common(10)), ensure_ascii=False),
        json.dumps([c for c, _ in g["categories"].most_common(5)], ensure_ascii=False),
        json.dumps([display_of.get(k, k) for k, _ in g["ingredients"].most_common(12)], ensure_ascii=False),
        json.dumps(g["urls"], ensure_ascii=False),
    ) for key, g in dgroups.items()]

    with db.lock, db.conn:
        db.conn.executemany("UPDATE recipe_ingredients SET canonical=? WHERE recipe_id=? AND position=?", updates)
        db.conn.execute("DELETE FROM ingredients")
        db.conn.executemany("INSERT INTO ingredients VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ing_rows)
        db.conn.executemany("UPDATE recipes SET dish_name=?, dish_key=? WHERE id=?", dish_updates)
        db.conn.execute("DELETE FROM dishes")
        db.conn.executemany("INSERT INTO dishes VALUES (?,?,?,?,?,?,?,?,?)", dish_rows)
    return {"ingredient_lines": len(rows), "ingredients": len(ing_rows), "recipes": len(recipes),
            "dishes": len(dish_rows)}


def _nutrition_index(db: DB, aliases: dict[str, str]) -> dict[str, dict]:
    """Ingredient key -> nutrition row (per 100 g). fiber.ge first, then gemrielia's table."""
    best: dict[str, tuple[tuple, dict]] = {}
    for r in db.query("SELECT * FROM nutrition"):
        key, display = ingredient_identity(r["name"], aliases)
        if not key:
            continue
        # Prefer fiber.ge, then rows whose name needed no stripping ("მაკარონი" over "შემწვარი მაკარონი").
        rank = (r["source"] != "fiber", fold(r["name"]) != display, len(r["name"]))
        if key not in best or rank < best[key][0]:
            best[key] = (rank, dict(r))
    out = {k: v[1] for k, v in best.items()}
    # Curated picks for staples whose names differ between recipes and the tables (რძე -> "რძე 3,2%").
    by_name = {fold(r["name"]): dict(r) for r in db.query("SELECT * FROM nutrition")}
    text = resources.files("recipe_scraper.data").joinpath("nutrition_map.csv").read_text(encoding="utf-8")
    for row in csv.reader(text.splitlines()):
        if len(row) >= 2 and not row[0].startswith("#") and row[0] != "ingredient" and fold(row[1]) in by_name:
            out[ingredient_identity(row[0], aliases)[0]] = by_name[fold(row[1])]
    return out
