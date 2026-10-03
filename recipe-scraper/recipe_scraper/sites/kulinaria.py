"""kulinaria.ge — Django site, no sitemap; recipes listed in paginated categories (?page=N).

Ingredient markup (one .list__item per ingredient, text split over lines):
    <h6 class="title">რეცეპტის ინგრედიენტები</h6>
    <div class="list">
      <div class="list__item"><div class="list--sprite"></div> 500 \n გრამი \n ბადრიჯანი</div>
"""
from __future__ import annotations

import re
from collections.abc import Iterator

from ..http import Fetcher
from ..ingredients import is_group_header, make_line, parse_line
from ..models import Recipe
from ..textutil import clean, first_number, parse_minutes
from .base import Site, crawl_pages, soupify, text_of

RECIPE_RE = re.compile(r"kulinaria\.ge/receptebi/(?!cat/|add/)(?P<slug>[^/?#]+)_(?P<id>\d+)/?$")
TOP_CATEGORY_RE = re.compile(r"kulinaria\.ge/receptebi/cat/[^/]+/$")
QTY_RE = re.compile(r"^[\d½¼¾⅓⅔⅛.,/\s–-]+$")


class Kulinaria(Site):
    name = "kulinaria"
    base_url = "https://kulinaria.ge"

    def discover(self, fx: Fetcher) -> Iterator[str]:
        soup = soupify(fx.get(self.base_url + "/receptebi/"))
        cats = sorted({self.abs(a["href"]) for a in soup.find_all("a", href=True)
                       if TOP_CATEGORY_RE.search(self.abs(a["href"]))})
        seen: set[str] = set()
        for root in [self.base_url + "/receptebi/"] + cats:
            pages = crawl_pages(fx, lambda n, r=root: r if n == 1 else f"{r}?page={n}", RECIPE_RE, self.base_url)
            for url in pages:
                m = RECIPE_RE.search(url)
                canonical = f"{self.base_url}/receptebi/{m.group('slug')}_{m.group('id')}/"
                if m.group("id") not in seen:
                    seen.add(m.group("id"))
                    yield canonical

    def parse(self, url: str, html: str) -> Recipe | None:
        soup = soupify(html)
        rec = self.new_recipe(url, text_of(soup.select_one(".post__title h1") or soup.find("h1")))
        rec.parse_method = "html"
        m = re.search(r"_(\d+)/?$", rec.url)
        rec.source_id = m.group(1) if m else None
        rec.description = text_of(soup.select_one(".post__description")) or None
        rec.author = text_of(soup.select_one(".post__author a")) or None
        img = soup.select_one(".post__img img[src]")
        rec.image = self.abs(img["src"]) if img else None
        rec.categories = list(dict.fromkeys(text_of(a) for a in soup.select(".recipe-cat-pills__item") if text_of(a)))

        for item in soup.select(".lineDesc__item"):
            t = text_of(item)
            if re.fullmatch(r"\d+:\d{2}(?::\d{2})?", t):
                rec.time_text, rec.total_minutes = t, parse_minutes(t)
            elif re.search(r"ულუფ|პორცი", t):
                rec.servings_text, rec.servings = t, first_number(t)

        heading = next((h for h in soup.select("h6.title, h6") if "ინგრედიენტ" in text_of(h)), None)
        box = heading.find_next_sibling(class_="list") if heading else None
        items = (box or soup).select(".list__item")
        group = None
        for item in items:
            lines = [clean(x) for x in item.get_text("\n").split("\n") if clean(x)]
            if not lines:
                continue
            raw = " ".join(lines)
            if len(lines) == 1:
                if is_group_header(lines[0]):
                    group = lines[0].rstrip(":")
                    continue
                rec.ingredients.extend(parse_line(lines[0], group))
            elif QTY_RE.match(lines[0]):
                q = re.split(r"\s*[–-]\s*", lines[0].strip(), maxsplit=1)
                rest = lines[1:]
                unit, name = (rest[0], " ".join(rest[1:])) if len(rest) >= 2 else (None, rest[0])
                rec.ingredients.append(make_line(raw, name, q[0], unit, group, None, q[1] if len(q) > 1 else None))
            else:
                rec.ingredients.extend(parse_line(raw, group))
        rec.ingredients = [i for i in rec.ingredients if i.name]

        rec.steps = [text_of(p) for p in soup.select(".lineList .lineList__item p") if text_of(p)]
        return self.fallback(rec, soup)
