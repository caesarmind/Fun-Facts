"""kerdzebi.ge — Next.js + Tailwind site with /sitemap.xml and /kategoriis/<slug> pages.

Class names are utility classes, so parsing anchors on headings instead:
    <h2>… ინგრედიენტები</h2> … <ul><li><span><span>250 გ</span>სპაგეტი</span></li>
"""
from __future__ import annotations

import re
from collections.abc import Iterator

from bs4 import Tag

from ..http import Fetcher
from ..ingredients import parse_line
from ..models import Recipe
from ..textutil import clean, first_number, parse_minutes
from .base import Site, crawl_pages, iter_sitemap, normalize_url, soupify, text_of

RECIPE_RE = re.compile(r"kerdzebi\.ge/recepti/[^/?#]+$")
CATEGORY_RE = re.compile(r"kerdzebi\.ge/kategoriis/[^/?#]+$")
EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️]")


class Kerdzebi(Site):
    name = "kerdzebi"
    base_url = "https://kerdzebi.ge"

    def discover(self, fx: Fetcher) -> Iterator[str]:
        seen: set[str] = set()
        categories = []
        for loc in iter_sitemap(fx, self.base_url + "/sitemap.xml"):
            url = normalize_url(loc).rstrip("/")
            if RECIPE_RE.search(url) and url not in seen:
                seen.add(url)
                yield url
            elif CATEGORY_RE.search(url):
                categories.append(url)
        for root in [self.base_url] + categories:
            for href in crawl_pages(fx, lambda n, r=root: r if n == 1 else f"{r}?page={n}", RECIPE_RE, self.base_url,
                                    max_pages=50):
                href = href.rstrip("/")
                if href not in seen:
                    seen.add(href)
                    yield href

    @staticmethod
    def _heading(scope: Tag, pattern: str) -> Tag | None:
        for s in scope.find_all(string=re.compile(pattern)):
            h = s.find_parent(["h1", "h2", "h3", "h4"])
            if h is not None:
                return h
        return None

    def parse(self, url: str, html: str) -> Recipe | None:
        soup = soupify(html)
        article = soup.find("article") or soup
        rec = self.new_recipe(url, text_of(article.find("h1")))
        rec.parse_method = "html"
        rec.source_id = rec.url.rstrip("/").rsplit("/", 1)[-1]
        h1 = article.find("h1")
        desc = h1.find_next("p") if h1 else None
        rec.description = text_of(desc) or None
        rec.categories = list(dict.fromkeys(
            clean(EMOJI_RE.sub("", text_of(a))) for a in article.select('a[href*="/kategoriis/"]') if text_of(a)))

        # Stats: <div><span>მომზადება</span>10 წუთი</div>
        minutes = 0
        for label in article.find_all("span"):
            lt = text_of(label)
            parent = label.parent
            if not lt or parent is None or len(lt) > 25:
                continue
            value = clean(text_of(parent)[len(lt):]) if text_of(parent).startswith(lt) else ""
            if not value:
                continue
            if lt.startswith(("მომზადება", "ცხობა", "ხარშვა", "დრო")):
                minutes += parse_minutes(value) or 0
                rec.time_text = ((rec.time_text + "; ") if rec.time_text else "") + f"{lt}: {value}"
            elif lt.startswith("პორცი"):
                rec.servings_text, rec.servings = value, first_number(value)
            elif lt.startswith("კალორი"):
                rec.calories_text, rec.calories = value, first_number(value)
        rec.total_minutes = minutes or None

        h = self._heading(article, r"ინგრედიენტ")
        box = h
        while box is not None and box.find("ul") is None:
            box = box.parent
        if box is not None:
            for li in box.find("ul").find_all("li"):
                amount = text_of(li.select_one("span span"))
                full = text_of(li)
                name = clean(full[len(amount):]) if amount and full.startswith(amount) else full
                rec.ingredients.extend(parse_line(clean(f"{amount} {name}")))

        steps_h = self._heading(article, r"მომზადების ეტაპ|ეტაპები")
        if steps_h is not None:
            for h4 in steps_h.find_all_next("h4"):
                p = h4.find_next("p")
                body = text_of(p)
                if not body:
                    break
                rec.steps.append(f"{text_of(h4)}: {body}")
        img = article.find("img", src=True)
        if img:
            rec.image = img["src"]
        return self.fallback(rec, soup)
