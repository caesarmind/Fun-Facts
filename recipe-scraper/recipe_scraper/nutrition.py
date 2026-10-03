"""Georgian nutrition tables (per 100 g) published by the recipe sites themselves.

* gemrielia.ge embeds ~630 foods (protein / fat / carbs / kcal) in the calorie calculator of
  its RecipeArticle.js bundle, grouped into ~40 food groups.
* fiber.ge has a calorie table page (fruit, vegetables, meat & fish, grains).

These give the ingredient library a first-pass kcal/macros match by Georgian name.
"""
from __future__ import annotations

import codecs
import logging
import re

from .http import Fetcher, FetchError
from .sites.base import soupify, text_of
from .textutil import fold, match_key, to_float

log = logging.getLogger(__name__)

GEMRIELIA_BUNDLE = "https://gemrielia.ge/static/build/RecipeArticle.js"
FIBER_TABLE = "https://fiber.ge/kaloriebis-tskhrili/"

_ROW = re.compile(r'\{group:"(?P<g>[^"]*)",name:"(?P<n>[^"]+)",proteins:(?P<p>[\d.]+),fats:(?P<f>[\d.]+),'
                  r'carbonhydrates:(?P<c>[\d.]+),calories:(?P<k>[\d.]+)\}')
_GROUP = re.compile(r'\{group:"(?P<g>[^"]*)",name:"(?P<n>[^"]+)",items:\[\]')


def _js_str(s: str) -> str:
    return codecs.decode(s, "unicode_escape") if "\\u" in s else s


def parse_gemrielia_bundle(js: str) -> list[dict]:
    groups = {m["g"]: _js_str(m["n"]) for m in _GROUP.finditer(js)}
    rows = []
    for m in _ROW.finditer(js):
        name = _js_str(m["n"])
        rows.append({"source": "gemrielia", "group": groups.get(m["g"], m["g"]), "name": name,
                     "key": match_key(name), "kcal": float(m["k"]), "protein": float(m["p"]),
                     "fat": float(m["f"]), "carbs": float(m["c"])})
    return rows


def parse_fiber_table(html: str) -> list[dict]:
    soup = soupify(html)
    rows = []
    for table in soup.select("table.calorie-table, table"):
        block = table.find_parent(lambda t: t.name == "div" and t.find("h2") is not None)
        group = text_of(block.find("h2")) if block else ""
        headers = [fold(text_of(th)) for th in table.select("thead th")]
        col = {}
        for i, h in enumerate(headers):
            if "კკალ" in h or "კალორ" in h:
                col["kcal"] = i
            elif "პროტეინ" in h or "ცილ" in h:
                col["protein"] = i
            elif "ნახშირწყ" in h:
                col["carbs"] = i
            elif "ცხიმ" in h:
                col["fat"] = i
            elif "დასახელ" in h or "პროდუქტ" in h:
                col["name"] = i
        if "name" not in col or "kcal" not in col:
            continue
        for tr in table.select("tbody tr"):
            cells = [text_of(td) for td in tr.find_all("td")]
            if len(cells) < len(headers) or tr.has_attr("hidden"):
                continue
            name = cells[col["name"]]
            rows.append({"source": "fiber", "group": group, "name": name, "key": match_key(name),
                         **{k: to_float(cells[i]) for k, i in col.items() if k != "name"}})
    for r in rows:
        for k in ("kcal", "protein", "fat", "carbs"):
            r.setdefault(k, None)
    return rows


def scrape_nutrition(fx: Fetcher) -> list[dict]:
    rows: list[dict] = []
    try:
        rows += parse_gemrielia_bundle(fx.get(GEMRIELIA_BUNDLE))
    except FetchError as exc:
        log.warning("gemrielia nutrition table: %s", exc)
    try:
        rows += parse_fiber_table(fx.get(FIBER_TABLE))
    except FetchError as exc:
        log.warning("fiber nutrition table: %s", exc)
    return rows
