from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import library
from .compare import compare
from .db import DB
from .export import export_all
from .http import DEFAULT_UA, Fetcher, FetchError
from .ingredients import is_plausible
from .nutrition import scrape_nutrition
from .sites import DEFAULT_SITES, SITES, Site, get_site

log = logging.getLogger("recipe_scraper")


def _sites(spec: str | None) -> list[Site]:
    names = [s.strip() for s in (spec or ",".join(DEFAULT_SITES)).split(",") if s.strip()]
    return [get_site(n) for n in names]


def _fetcher(a: argparse.Namespace) -> Fetcher:
    return Fetcher(cache_dir=None if a.no_cache else a.cache, delay=a.delay, user_agent=a.user_agent,
                   respect_robots=not a.ignore_robots, refresh=a.refresh, offline=a.offline)


def scrape_site(site: Site, fx: Fetcher, db: DB, limit: int | None, render: bool, rescrape: bool) -> dict:
    t0 = time.time()
    try:
        urls = list(dict.fromkeys(site.discover(fx)))
    except FetchError as exc:
        log.error("%s: discovery failed: %s", site.name, exc)
        return {"site": site.name, "error": str(exc)}
    log.info("%s: found %d recipe URLs", site.name, len(urls))
    done = set() if rescrape else db.known_urls(site.name)
    fresh = [u for u in urls if u not in done]
    todo = fresh[:limit] if limit else fresh
    ok = failed = 0
    for n, url in enumerate(todo, 1):
        try:
            rec = site.scrape(fx, url, render=render)
        except FetchError as exc:
            db.fail(url, site.name, str(exc))
            failed += 1
            continue
        except Exception as exc:  # noqa: BLE001 - one bad page must not stop the crawl
            log.exception("%s: error parsing %s", site.name, url)
            db.fail(url, site.name, f"{type(exc).__name__}: {exc}")
            failed += 1
            continue
        if rec is not None:
            kept = [i for i in rec.ingredients if is_plausible(i)]
            if len(kept) != len(rec.ingredients):
                rec.extra["dropped_lines"] = [i.raw for i in rec.ingredients if not is_plausible(i)]
                rec.ingredients = kept
        if rec is None or not rec.ingredients:
            db.fail(url, site.name, "no ingredients found")
            failed += 1
            continue
        db.save(rec)
        ok += 1
        if n % 25 == 0 or n == len(todo):
            log.info("%s: %d/%d (ok %d, failed %d)", site.name, n, len(todo), ok, failed)
    return {"site": site.name, "urls": len(urls), "already_had": len(urls) - len(fresh),
            "scraped": ok, "failed": failed, "seconds": round(time.time() - t0)}


def cmd_scrape(a, db: DB) -> list[dict]:
    fx = _fetcher(a)
    sites = _sites(a.sites)
    with ThreadPoolExecutor(max_workers=len(sites)) as pool:  # one thread per site; each site is rate-limited
        results = list(pool.map(lambda s: scrape_site(s, fx, db, a.limit, a.render, a.rescrape), sites))
    for r in results:
        log.info("done: %s", r)
    log.info("http: %s", fx.stats)
    return results


def cmd_nutrition(a, db: DB) -> int:
    rows = scrape_nutrition(_fetcher(a))
    db.save_nutrition(rows)
    log.info("nutrition rows: %d", len(rows))
    return len(rows)


def cmd_discover(a, db: DB) -> None:
    fx = _fetcher(a)
    out = open(a.out, "w", encoding="utf-8") if a.out else sys.stdout
    for site in _sites(a.sites):
        n = 0
        for url in site.discover(fx):
            out.write(f"{site.name}\t{url}\n")
            n += 1
        log.info("%s: %d URLs", site.name, n)
    if a.out:
        out.close()


def cmd_parse(a, db: DB) -> None:
    site = get_site(a.site)
    if a.target.startswith("http"):
        rec = site.scrape(_fetcher(a), a.target, render=a.render)
    else:
        rec = site.parse(a.url or Path(a.target).resolve().as_uri(), Path(a.target).read_text(encoding="utf-8"))
    print(json.dumps(rec.to_dict() if rec else None, ensure_ascii=False, indent=2))


def cmd_stats(a, db: DB) -> None:
    for r in db.query("SELECT source, COUNT(*) n, SUM(n_ingredients) lines FROM recipes GROUP BY source"):
        f = db.query("SELECT COUNT(*) FROM failures WHERE source=?", (r["source"],))[0][0]
        print(f"{r['source']:12} recipes {r['n']:6}  ingredient lines {r['lines'] or 0:7}  failures {f}")
    for t in ("ingredients", "dishes", "nutrition"):
        print(f"{t:12} {db.query(f'SELECT COUNT(*) FROM {t}')[0][0]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m recipe_scraper",
                                 description="Scrape Georgian recipe sites into dish & ingredient libraries.")
    ap.add_argument("--db", default="data/recipes.db", help="SQLite database (default: %(default)s)")
    ap.add_argument("--cache", default="data/cache", help="HTTP cache directory (default: %(default)s)")
    ap.add_argument("--no-cache", action="store_true", help="do not cache pages on disk")
    ap.add_argument("--refresh", action="store_true", help="ignore cached pages and re-download")
    ap.add_argument("--offline", action="store_true", help="only use cached pages (re-parse without network)")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests to the same site")
    ap.add_argument("--user-agent", default=DEFAULT_UA)
    ap.add_argument("--ignore-robots", action="store_true", help="do not check robots.txt")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def site_args(p, limit=True):
        p.add_argument("--sites", help=f"comma list (default: {','.join(DEFAULT_SITES)}); "
                                       f"known: {', '.join(SITES)}; or name=https://other-site.ge")
        if limit:
            p.add_argument("--limit", type=int, help="max new recipes per site (for a quick test)")
            p.add_argument("--render", action="store_true",
                           help="use Playwright for JavaScript pages when the gemrielia API fails")
            p.add_argument("--rescrape", action="store_true", help="re-scrape recipes already in the database")

    def compare_args(p):
        p.add_argument("--my-ingredients", help="your ingredient list (CSV with 'name'[,'aliases'] or .txt)")
        p.add_argument("--my-dishes", help="your dish list (CSV with 'name'[,'aliases'] or .txt)")
        p.add_argument("--aliases", help="extra alias CSV (alias,canonical) on top of the built-in one")
        p.add_argument("--fuzzy", type=float, default=0.88, help="fuzzy match cutoff 0-1 (1 = exact only)")

    p = sub.add_parser("run", help="scrape + nutrition + build + export + compare")
    site_args(p)
    compare_args(p)
    p.add_argument("--out", default="data/export")
    p.add_argument("--no-nutrition", action="store_true")

    p = sub.add_parser("scrape", help="discover and scrape recipes into the database")
    site_args(p)
    p = sub.add_parser("discover", help="only list recipe URLs")
    site_args(p, limit=False)
    p.add_argument("--out", help="write site<TAB>url lines to this file")
    sub.add_parser("nutrition", help="fetch the gemrielia & fiber nutrition tables")
    p = sub.add_parser("build", help="(re)build ingredient & dish libraries from scraped recipes")
    p.add_argument("--aliases")
    p = sub.add_parser("export", help="write CSV/JSONL files")
    p.add_argument("--out", default="data/export")
    p = sub.add_parser("compare", help="compare sites with each other and with your own lists")
    compare_args(p)
    p.add_argument("--out", default="data/compare")
    p = sub.add_parser("parse", help="debug: parse one page (URL or saved HTML file) and print JSON")
    p.add_argument("site")
    p.add_argument("target", help="URL or path to an .html file")
    p.add_argument("--url", help="original URL when parsing a file")
    p.add_argument("--render", action="store_true")
    sub.add_parser("stats", help="counts per site")

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    db = DB(a.db)
    try:
        if a.cmd == "run":
            cmd_scrape(a, db)
            if not a.no_nutrition:
                cmd_nutrition(a, db)
            log.info("libraries: %s", library.build(db, a.aliases))
            log.info("exported: %s", export_all(db, a.out))
            s = compare(db, Path(a.out) / "compare", a.my_ingredients, a.my_dishes, a.aliases, a.fuzzy)
            log.info("compare: %d ingredients, %d dishes -> %s", s["n_ingredients"], s["n_dishes"],
                     Path(a.out) / "compare")
        elif a.cmd == "scrape":
            cmd_scrape(a, db)
            log.info("libraries: %s", library.build(db))
        elif a.cmd == "discover":
            cmd_discover(a, db)
        elif a.cmd == "nutrition":
            cmd_nutrition(a, db)
        elif a.cmd == "build":
            log.info("libraries: %s", library.build(db, a.aliases))
        elif a.cmd == "export":
            log.info("exported: %s", export_all(db, a.out))
        elif a.cmd == "compare":
            compare(db, a.out, a.my_ingredients, a.my_dishes, a.aliases, a.fuzzy)
            print((Path(a.out) / "summary.md").read_text(encoding="utf-8"))
            log.info("files in %s", a.out)
        elif a.cmd == "parse":
            cmd_parse(a, db)
        elif a.cmd == "stats":
            cmd_stats(a, db)
    finally:
        db.close()
    return 0
