"""Any other recipe site: sitemap discovery + JSON-LD / heading heuristics.

Use it from the CLI as  --site name=https://example.ge  (optionally with a URL regex,
--site 'name=https://example.ge|/recipe/').
"""
from __future__ import annotations

import re
from collections.abc import Iterator

from ..http import Fetcher
from ..models import Recipe
from .base import Site, iter_sitemap, normalize_url, soupify

SKIP_RE = re.compile(r"/(tag|tags|category|categories|author|page|search|login|about|contact)(/|$)|\.(jpe?g|png|webp|gif|pdf)$", re.I)


class GenericSite(Site):
    def __init__(self, name: str, base_url: str, recipe_pattern: str | None = None):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.recipe_re = re.compile(recipe_pattern) if recipe_pattern else None

    def discover(self, fx: Fetcher) -> Iterator[str]:
        sitemaps: list[str] = []
        robots = fx.get_optional(self.base_url + "/robots.txt") or ""
        sitemaps += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots)
        sitemaps += [self.base_url + "/sitemap.xml", self.base_url + "/sitemap_index.xml"]
        seen: set[str] = set()
        for sm in dict.fromkeys(sitemaps):
            if fx.get_optional(sm) is None:
                continue
            for loc in iter_sitemap(fx, sm):
                url = normalize_url(loc)
                if url in seen or SKIP_RE.search(url) or url.rstrip("/") == self.base_url:
                    continue
                if self.recipe_re and not self.recipe_re.search(url):
                    continue
                seen.add(url)
                yield url

    def parse(self, url: str, html: str) -> Recipe | None:
        soup = soupify(html)
        rec = self.new_recipe(url)
        return self.fallback(rec, soup)
