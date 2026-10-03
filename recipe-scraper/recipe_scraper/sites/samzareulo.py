"""samzareulo.net — DataLife Engine site with /sitemap.xml and /lastnews/page/N/ listings.

Ingredient markup ("name - amount" inside checkbox labels):
    <div class="control-group"><h3><b>ინგრედიენტები</b></h3>
      <label class="control control--checkbox"> რძე - 300 მლ. <input type="checkbox"/>...</label>
"""
from __future__ import annotations

import re
from collections.abc import Iterator

from ..http import Fetcher
from ..ingredients import parse_lines
from ..models import Recipe
from ..textutil import clean
from .base import Site, crawl_pages, iter_sitemap, normalize_url, soupify, text_of

# Posts are /<section>/<id>-<slug>.html; tips, articles and product guides are not recipes.
RECIPE_RE = re.compile(r"samzareulo\.net/(?!rchevebi/|statiebi/|produqtebi/)(?:[^/]+/)*\d+-[^/]+\.html$")


class Samzareulo(Site):
    name = "samzareulo"
    base_url = "https://samzareulo.net"

    def discover(self, fx: Fetcher) -> Iterator[str]:
        seen: set[str] = set()

        def emit(url: str):
            url = normalize_url(url)
            if RECIPE_RE.search(url) and url not in seen:
                seen.add(url)
                return url
            return None

        for loc in iter_sitemap(fx, self.base_url + "/sitemap.xml"):
            if (u := emit(loc)):
                yield u
        # The sitemap can lag behind; the listings catch newer and user-submitted recipes.
        for root in ("/lastnews/", "/momxmareblis-receptebi/"):
            base = self.base_url + root
            for href in crawl_pages(fx, lambda n, b=base: b if n == 1 else f"{b}page/{n}/", RECIPE_RE, self.base_url):
                if (u := emit(href)):
                    yield u

    def parse(self, url: str, html: str) -> Recipe | None:
        soup = soupify(html)
        rec = self.new_recipe(url, text_of(soup.find("h1")))
        rec.parse_method = "html"
        m = re.search(r"/(\d+)-[^/]+\.html$", rec.url)
        rec.source_id = m.group(1) if m else None

        labels = soup.select(".control-group label") or soup.select("label.control")
        rec.ingredients = parse_lines(text_of(lb) for lb in labels)

        b = next((x for x in soup.find_all(["b", "strong"]) if "მომზადების" in text_of(x)), None)
        if b is not None and b.parent is not None:
            header = text_of(b)
            text = b.parent.get_text("\n")
            text = text.replace(b.get_text(), "", 1)
            rec.steps = [clean(x) for x in text.split("\n") if clean(x) and clean(x) != header]

        rec.tags = list(dict.fromkeys(text_of(a) for a in soup.select('a[href*="/tags/"]') if text_of(a)))
        section = re.search(r"samzareulo\.net/([^/]+)/", rec.url)
        if section and section.group(1) != "receptebi":
            rec.extra["section"] = section.group(1)
        img = soup.select_one('img[src*="/uploads/posts/"]')
        if img:
            rec.image = self.abs(img["src"])
        return self.fallback(rec, soup)
