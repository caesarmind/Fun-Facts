"""gemrielia.ge — Vue single-page app. Server HTML has only the title, so recipes come from
the same JSON API the front-end uses:

    POST https://gemrielia.ge/api/recipe/
    {"many": false, "find": {"id": 9724}, "fields": []}   ->   {"result": {...recipe...}}

(endpoint and body taken from the site's RecipeArticle.js bundle). If the API changes, the
scraper falls back to the server HTML and, with --render, to a Playwright-rendered page where
ingredients are <ul class="unorderedlist"><li>ბანანი - 1 ცალი (…)</li></ul>.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from typing import Any

from bs4 import BeautifulSoup

from ..http import Fetcher, FetchError
from ..ingredients import parse_lines
from ..models import Recipe
from ..textutil import clean, first_number, parse_minutes
from .base import Site, iter_sitemap, normalize_url, soupify, text_of

log = logging.getLogger(__name__)

RECIPE_RE = re.compile(r"gemrielia\.ge/recipe/(?P<id>\d+)-[^/]*/?$")
DIFFICULTY = {1: "მარტივი", 2: "საშუალო", 3: "რთული"}

_TEXT_KEYS = ("text", "content", "name", "title", "value", "ingredient", "label")
_LIST_KEYS = ("items", "ingredients", "list", "children", "values", "data")


def _html_lines(fragment: str) -> list[str]:
    """Ingredient lines from an HTML fragment: list items, else paragraphs/lines (keeps headings)."""
    soup = BeautifulSoup(fragment, "lxml")
    out: list[str] = []
    for el in soup.find_all(["li", "p", "h3", "h4", "h5", "h6", "strong", "b"]):
        if el.name in ("strong", "b") and el.find_parent(["li", "p"]):
            continue
        if el.name == "p" and el.find("li"):
            continue
        t = text_of(el)
        if t:
            out.append(t)
    if not out:
        out = [clean(x) for x in soup.get_text("\n").split("\n") if clean(x)]
    return out


def ingredient_texts(obj: Any) -> list[str]:
    """Flatten whatever shape the API uses for ingredients into ordered text lines.

    Handles: HTML strings, newline-separated text, JSON-encoded strings, Editor.js
    {"blocks": [...]}, lists of strings, and lists of {name, amount/quantity, unit} or
    {title, items: [...]} groups (group titles are emitted as "title:" headings).
    """
    if obj is None:
        return []
    if isinstance(obj, str):
        s = obj.strip()
        if s[:1] in "[{":
            try:
                return ingredient_texts(json.loads(s))
            except json.JSONDecodeError:
                pass
        if "<" in s and ">" in s:
            return _html_lines(s)
        return [clean(x) for x in re.split(r"\n|<br\s*/?>", s) if clean(x)]
    if isinstance(obj, list):
        return [t for item in obj for t in ingredient_texts(item)]
    if isinstance(obj, dict):
        if "blocks" in obj:  # Editor.js
            out: list[str] = []
            for block in obj["blocks"] or []:
                data = block.get("data") or {}
                if block.get("type") == "list":
                    for it in data.get("items") or []:
                        out.extend(ingredient_texts(it if isinstance(it, str) else it.get("content", "")))
                elif block.get("type") in ("header", "paragraph"):
                    t = clean(BeautifulSoup(data.get("text", ""), "lxml").get_text(" "))
                    if t:
                        out.append(t + (":" if block.get("type") == "header" and not t.endswith(":") else ""))
            return out
        nested = next((obj[k] for k in _LIST_KEYS if isinstance(obj.get(k), list)), None)
        title = next((clean(str(obj[k])) for k in ("title", "name", "group") if isinstance(obj.get(k), str)), "")
        if nested is not None:
            return ([title.rstrip(":") + ":"] if title else []) + ingredient_texts(nested)
        name = next((clean(str(obj[k])) for k in _TEXT_KEYS if isinstance(obj.get(k), (str, int, float))), "")
        amount = " ".join(clean(str(obj[k])) for k in ("amount", "quantity", "qty", "count", "unit", "measure")
                          if obj.get(k) not in (None, ""))
        if name and amount:
            return [f"{name} - {amount}"]
        return [name] if name else []
    return [clean(str(obj))]


def _steps(obj: Any) -> list[str]:
    lines = ingredient_texts(obj)
    return [x for x in lines if len(x) > 2]


class Gemrielia(Site):
    name = "gemrielia"
    base_url = "https://gemrielia.ge"
    api_url = "https://gemrielia.ge/api/recipe/"
    needs_render = True

    def discover(self, fx: Fetcher) -> Iterator[str]:
        index = fx.get(self.base_url + "/sitemap.xml")
        sitemaps = [x for x in re.findall(r"<loc>\s*(.*?)\s*</loc>", index) if "recipe" in x]
        seen: set[str] = set()
        for sm in sitemaps:
            for loc in iter_sitemap(fx, sm):
                url = normalize_url(loc)
                m = RECIPE_RE.search(url)
                if m and m.group("id") not in seen:
                    seen.add(m.group("id"))
                    yield url

    def fetch_api(self, fx: Fetcher, recipe_id: int) -> dict | None:
        data = fx.post_json(self.api_url, {"many": False, "find": {"id": recipe_id}, "fields": []})
        if isinstance(data, dict):
            result = data.get("result", data)
            if isinstance(result, list):
                result = result[0] if result else None
            return result if isinstance(result, dict) else None
        return None

    def scrape(self, fx: Fetcher, url: str, render: bool = False) -> Recipe | None:
        m = RECIPE_RE.search(normalize_url(url))
        rec = None
        if m:
            try:
                data = self.fetch_api(fx, int(m.group("id")))
                if data:
                    rec = self.from_api(url, data)
            except (FetchError, ValueError) as exc:
                log.warning("gemrielia API failed for %s: %s", url, exc)
        if rec and rec.ingredients:
            return rec
        if render:
            html = fx.render(url, wait_selector=".recipe__body--ingredients li, ul.unorderedlist li")
            rendered = self.parse(url, html)
            if rendered and rendered.ingredients:
                rendered.parse_method = "rendered"
                return rendered
        return rec or self.parse(url, fx.get(url))

    def from_api(self, url: str, r: dict) -> Recipe:
        rec = self.new_recipe(url, clean(str(r.get("title") or "")))
        rec.parse_method = "api"
        rec.source_id = str(r.get("id") or "") or None
        rec.ingredients = parse_lines(ingredient_texts(r.get("ingredients")))
        for key in ("steps", "preparation", "instructions", "description", "body", "text", "content"):
            if r.get(key):
                rec.steps = _steps(r[key])
                if rec.steps:
                    break
        cat = r.get("category")
        if isinstance(cat, dict):
            name = cat.get("title") or cat.get("name")
            if name:
                rec.categories = [clean(str(name))]
        elif isinstance(cat, str):
            rec.categories = [clean(cat)]
        if r.get("time") not in (None, ""):
            rec.time_text = clean(str(r["time"]))
            rec.total_minutes = parse_minutes(r["time"])
        if r.get("serving") not in (None, ""):
            rec.servings_text = clean(str(r["serving"]))
            rec.servings = first_number(rec.servings_text)
        if r.get("difficult") not in (None, ""):
            try:
                rec.extra["difficulty"] = DIFFICULTY.get(int(r["difficult"]), r["difficult"])
            except (TypeError, ValueError):
                rec.extra["difficulty"] = r["difficult"]
        image = r.get("image") or r.get("cover")
        if isinstance(image, dict):
            image = image.get("url") or image.get("src")
        if isinstance(image, str) and image:
            rec.image = self.abs(image)
        for key in ("publish_date", "published_at", "created_at", "date", "created"):
            if r.get(key):
                rec.published = str(r[key])
                break
        author = r.get("author") or r.get("user")
        if isinstance(author, dict):
            author = author.get("name") or author.get("username")
        if isinstance(author, str):
            rec.author = clean(author)
        rec.extra["api_keys"] = sorted(r.keys())
        return rec

    def parse(self, url: str, html: str) -> Recipe | None:
        """Server HTML (title only) or a Playwright-rendered page."""
        soup = soupify(html)
        title = re.sub(r"\s*-\s*Gemrielia\.ge\s*$", "", text_of(soup.find("h1")))
        rec = self.new_recipe(url, title)
        rec.parse_method = "html"
        m = RECIPE_RE.search(rec.url)
        rec.source_id = m.group("id") if m else None
        box = soup.select_one(".recipe__body--ingredients")
        if box is not None:
            rec.ingredients = parse_lines(_html_lines(str(box)))
            rec.ingredients = [i for i in rec.ingredients if "ინგრედიენტ" not in i.name]
        else:
            uls = soup.select("ul.unorderedlist")
            if uls:
                rec.ingredients = parse_lines(text_of(li) for li in uls[0].find_all("li"))
        prep = soup.select_one(".recipe__body--preparing")
        if prep is not None:
            rec.steps = [t for t in _html_lines(str(prep)) if "მომზადება" not in t or len(t) > 30]
        return self.fallback(rec, soup)
