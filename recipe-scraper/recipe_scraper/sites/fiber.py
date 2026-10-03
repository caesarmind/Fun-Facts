"""fiber.ge — Astro site with /recepti-sitemap.xml; clean semantic markup incl. calories.

    <ul class="recipe-ingredients__list"><li>1 ს/კ ტომატის პასტა</li>...
    <dl class="recipe-stats"><div><dt>დრო</dt><dd>1 სთ</dd></div>...
"""
from __future__ import annotations

import re
from collections.abc import Iterator

from ..http import Fetcher
from ..ingredients import parse_line
from ..models import Recipe
from ..textutil import first_number, parse_minutes
from .base import Site, iter_sitemap, normalize_url, soupify, text_of

RECIPE_RE = re.compile(r"fiber\.ge/recepti/[^/]+/?$")


class Fiber(Site):
    name = "fiber"
    base_url = "https://fiber.ge"

    def discover(self, fx: Fetcher) -> Iterator[str]:
        seen: set[str] = set()
        for loc in iter_sitemap(fx, self.base_url + "/recepti-sitemap.xml"):
            url = normalize_url(loc)
            if RECIPE_RE.search(url) and url not in seen:
                seen.add(url)
                yield url

    def parse(self, url: str, html: str) -> Recipe | None:
        soup = soupify(html)
        rec = self.new_recipe(url, text_of(soup.select_one(".recipe-hero__title") or soup.find("h1")))
        rec.parse_method = "html"
        rec.source_id = rec.url.rstrip("/").rsplit("/", 1)[-1]
        rec.description = text_of(soup.select_one(".recipe-hero__intro")) or None
        rec.categories = list(dict.fromkeys(text_of(a) for a in soup.select(".recipe-hero__cat") if text_of(a)))
        img = soup.select_one(".recipe-hero__img[src]")
        rec.image = self.abs(img["src"]) if img else None

        for row in soup.select(".recipe-stats > div"):
            label, value = text_of(row.find("dt")), text_of(row.find("dd"))
            if "დრო" in label:
                rec.time_text, rec.total_minutes = value, parse_minutes(value)
            elif "პორცი" in label:
                rec.servings_text, rec.servings = value, first_number(value)
            elif "კალორი" in label:
                rec.calories_text, rec.calories = value, first_number(value)

        box = soup.select_one(".recipe-ingredients")
        group = None
        if box is not None:
            for el in box.find_all(["h3", "h4", "h5", "strong", "li"]):
                if el.name == "li":
                    rec.ingredients.extend(parse_line(text_of(el), group))
                elif "recipe-ingredients__title" not in (el.get("class") or []):
                    group = text_of(el).rstrip(":") or group
        rec.steps = [text_of(li) for li in soup.select(".recipe-steps__list li") if text_of(li)]
        return self.fallback(rec, soup)
