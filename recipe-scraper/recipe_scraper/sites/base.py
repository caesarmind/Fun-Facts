from __future__ import annotations

import html as htmllib
import logging
import re
from collections.abc import Callable, Iterable, Iterator
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from .. import jsonld
from ..http import Fetcher, FetchError
from ..ingredients import parse_lines
from ..models import Recipe
from ..textutil import clean

log = logging.getLogger(__name__)

INGREDIENTS_HEADING = re.compile(r"ინგრედიენტ|ingredient", re.I)
STEPS_HEADING = re.compile(r"მომზადებ|მზადდება|ეტაპ|საფეხურ|წესი|instruction|method|preparation", re.I)
HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def soupify(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def text_of(el: Tag | None, sep: str = " ") -> str:
    return clean(el.get_text(sep)) if el is not None else ""


def normalize_url(url: str) -> str:
    """Canonical form used as the database key: no fragment, decoded path, trailing slash kept as-is."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme or "https", parts.netloc.lower(), unquote(parts.path), parts.query, ""))


def iter_sitemap(fx: Fetcher, url: str, depth: int = 0) -> Iterator[str]:
    """Yield page URLs from a sitemap or sitemap index (recursively)."""
    if depth > 3:
        return
    try:
        xml = fx.get(url)
    except FetchError as exc:
        log.warning("sitemap %s: %s", url, exc)
        return
    locs = [htmllib.unescape(x.strip()) for x in re.findall(r"<loc>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</loc>", xml, re.S)]
    if "<sitemapindex" in xml:
        for loc in locs:
            yield from iter_sitemap(fx, loc, depth + 1)
    else:
        yield from locs


def crawl_pages(fx: Fetcher, page_url: Callable[[int], str], link_re: re.Pattern[str], base: str,
                max_pages: int = 1000, start: int = 1) -> Iterator[str]:
    """Walk a paginated listing until a page yields no new matching links."""
    seen: set[str] = set()
    for n in range(start, start + max_pages):
        html = fx.get_optional(page_url(n))
        if not html:
            break
        soup = soupify(html)
        new = []
        for a in soup.find_all("a", href=True):
            href = normalize_url(urljoin(base, a["href"]))
            if link_re.search(href) and href not in seen:
                seen.add(href)
                new.append(href)
        if not new:
            break
        yield from new


def heading_items(soup: BeautifulSoup, heading_re: re.Pattern[str], stop_re: re.Pattern[str] | None = None) -> list[str]:
    """Fallback: collect <li>/<label> texts that follow a heading such as 'ინგრედიენტები'."""
    for h in soup.find_all(list(HEADINGS) + ["b", "strong", "p", "span", "div"]):
        own = clean(h.get_text(" "))
        if not own or len(own) > 40 or not heading_re.search(own):
            continue
        items: list[str] = []
        for el in h.find_all_next():
            if el.name in HEADINGS and items:
                break
            if stop_re and el.name in HEADINGS + ("b", "strong") and stop_re.search(text_of(el)) and len(text_of(el)) < 60:
                break
            if el.name in ("li", "label"):
                t = text_of(el)
                if t:
                    items.append(t)
            if len(items) > 80:
                break
        if items:
            return items
    return []


class Site:
    name: str = ""
    base_url: str = ""
    #: True when ingredients are only reachable through JavaScript / an API
    needs_render: bool = False

    def discover(self, fx: Fetcher) -> Iterable[str]:
        raise NotImplementedError

    def scrape(self, fx: Fetcher, url: str, render: bool = False) -> Recipe | None:
        html = fx.get(url)
        return self.parse(url, html)

    def parse(self, url: str, html: str) -> Recipe | None:
        raise NotImplementedError

    # ----------------------------------------------------------- helpers
    def abs(self, href: str) -> str:
        return normalize_url(urljoin(self.base_url, href))

    def new_recipe(self, url: str, title: str = "") -> Recipe:
        return Recipe(source=self.name, url=normalize_url(url), title=clean(title))

    def merge_jsonld(self, rec: Recipe, soup: BeautifulSoup) -> Recipe:
        """Fill anything the HTML parser missed from schema.org JSON-LD, if present."""
        node = jsonld.find_recipe(soup)
        if not node:
            return rec
        d = jsonld.summarize(node)
        if not rec.ingredients and d["ingredients"]:
            rec.ingredients = parse_lines(d["ingredients"])
            rec.parse_method = "jsonld"
        if not rec.steps:
            rec.steps = d["steps"]
        for f in ("title", "description", "servings", "servings_text", "total_minutes", "image", "author",
                  "published", "calories", "calories_text"):
            if not getattr(rec, f) and d.get(f):
                setattr(rec, f, d[f])
        rec.categories = rec.categories or d["categories"]
        rec.tags = rec.tags or d["tags"]
        return rec

    def fallback(self, rec: Recipe, soup: BeautifulSoup) -> Recipe:
        """JSON-LD, then heading heuristics, for whatever is still missing."""
        self.merge_jsonld(rec, soup)
        if not rec.ingredients:
            items = heading_items(soup, INGREDIENTS_HEADING, STEPS_HEADING)
            if items:
                rec.ingredients = parse_lines(items)
                rec.parse_method = "heuristic"
        if not rec.steps:
            rec.steps = heading_items(soup, STEPS_HEADING)
        if not rec.title:
            rec.title = text_of(soup.find("h1")) or text_of(soup.find("title"))
        if not rec.image:
            og = soup.find("meta", attrs={"property": "og:image"})
            if og and og.get("content"):
                rec.image = self.abs(og["content"])
        if not rec.description:
            md = soup.find("meta", attrs={"name": "description"})
            if md and md.get("content"):
                rec.description = clean(md["content"])
        return rec
