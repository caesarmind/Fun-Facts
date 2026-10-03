"""End-to-end run (discover -> scrape -> nutrition -> build -> export -> compare) against fixtures."""
import csv
import json
from pathlib import Path

import pytest

from recipe_scraper import library
from recipe_scraper.cli import scrape_site
from recipe_scraper.compare import compare
from recipe_scraper.db import DB
from recipe_scraper.export import export_all
from recipe_scraper.http import Fetcher, FetchError
from recipe_scraper.nutrition import FIBER_TABLE, GEMRIELIA_BUNDLE, scrape_nutrition
from recipe_scraper.sites import SITES

FX = Path(__file__).parent / "fixtures"


def fx(name):
    return (FX / name).read_text(encoding="utf-8")


def sitemap(*urls, index=False):
    tag, item = ("sitemapindex", "sitemap") if index else ("urlset", "url")
    body = "".join(f"<{item}><loc>{u}</loc></{item}>" for u in urls)
    return f'<?xml version="1.0"?><{tag} xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</{tag}>'


def links(*hrefs):
    return "<html><body>" + "".join(f'<a href="{h}">x</a>' for h in hrefs) + "</body></html>"


KUL = "/receptebi/%E1%83%91%E1%83%90%E1%83%93%E1%83%A0%E1%83%98%E1%83%AF%E1%83%90%E1%83%9C%E1%83%98-%E1%83%9C%E1%83%98%E1%83%92%E1%83%95%E1%83%96%E1%83%98%E1%83%97_116/"
GEM = "https://gemrielia.ge/recipe/9724-kokosis-da-bananis-burtulebi-mxolod-3-ingredientit/"
ROUTES = {
    ("GET", "https://kulinaria.ge/receptebi/"): links("/receptebi/cat/karTuli-samzareulo/",
                                                      "/receptebi/cat/karTuli-samzareulo/regionuli-kulinaria/"),
    ("GET", "https://kulinaria.ge/receptebi/cat/karTuli-samzareulo/"): links(KUL, "?page=2"),
    ("GET", "https://kulinaria.ge/receptebi/cat/karTuli-samzareulo/?page=2"): links(KUL),
    ("GET", "https://kulinaria.ge/receptebi/ბადრიჯანი-ნიგვზით_116/"): fx("kulinaria.html"),
    ("GET", "https://gemrielia.ge/sitemap.xml"): sitemap("https://gemrielia.ge/media/sitemaps/article_1.xml",
                                                        "https://gemrielia.ge/media/sitemaps/recipe_1.xml", index=True),
    ("GET", "https://gemrielia.ge/media/sitemaps/recipe_1.xml"): sitemap(GEM),
    ("POST", "https://gemrielia.ge/api/recipe/"): fx("gemrielia_api.json"),
    ("GET", "https://fiber.ge/recepti-sitemap.xml"): sitemap("https://fiber.ge/recepti/ajapsandali/"),
    ("GET", "https://fiber.ge/recepti/ajapsandali/"): fx("fiber.html"),
    ("GET", "https://samzareulo.net/sitemap.xml"): sitemap(
        "https://samzareulo.net/receptebi/", "https://samzareulo.net/rchevebi/188-badagi.html",
        "https://samzareulo.net/receptebi/223-imeruli-xachapuri.html"),
    ("GET", "https://samzareulo.net/lastnews/"): links(
        "https://samzareulo.net/receptebi/223-imeruli-xachapuri.html",
        "https://samzareulo.net/rcheuli-mzareulebi/naira-beridze/223-imeruli-xachapuri.html"),  # same post, other section
    ("GET", "https://samzareulo.net/receptebi/223-imeruli-xachapuri.html"): fx("samzareulo.html"),
    ("GET", "https://kerdzebi.ge/sitemap.xml"): sitemap("https://kerdzebi.ge/kategoriis/tsomeuli",
                                                       "https://kerdzebi.ge/recepti/pasta-carbonara-1784725429472"),
    ("GET", "https://kerdzebi.ge"): links("/recepti/pasta-carbonara-1784725429472"),
    ("GET", "https://kerdzebi.ge/kategoriis/tsomeuli"): links("/recepti/pasta-carbonara-1784725429472"),
    ("GET", "https://kerdzebi.ge/recepti/pasta-carbonara-1784725429472"): fx("kerdzebi.html"),
    ("GET", GEMRIELIA_BUNDLE): fx("gemrielia_bundle.js"),
    ("GET", FIBER_TABLE): fx("fiber_calories.html"),
}


PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


class FakeFetcher(Fetcher):
    def __init__(self):
        super().__init__(cache_dir=None, delay=0, respect_robots=False)
        self.seen = []

    def _request(self, method, url, *, json_body=None, allow_404=False, **kw):
        self.seen.append((method, url, json_body))
        if (method, url) in ROUTES:
            return ROUTES[(method, url)]
        if allow_404:
            return None
        raise FetchError(f"HTTP 404 for {url}")

    def get_bytes(self, url):
        self.seen.append(("GET", url, None))
        return PNG, "image/png"


def test_full_pipeline(tmp_path):
    f = FakeFetcher()
    db = DB(tmp_path / "r.db")
    results = [scrape_site(SITES[name], f, db, None, False, False)
               for name in ("kulinaria", "gemrielia", "fiber", "samzareulo", "kerdzebi")]
    assert [(r["site"], r["urls"], r["scraped"], r["failed"]) for r in results] == [
        ("kulinaria", 1, 1, 0), ("gemrielia", 1, 1, 0), ("fiber", 1, 1, 0), ("samzareulo", 1, 1, 0),
        ("kerdzebi", 1, 1, 0)]
    # gemrielia goes through the JSON API with the body the site's own front-end sends
    assert ("POST", "https://gemrielia.ge/api/recipe/", {"many": False, "find": {"id": 9724}, "fields": []}) in f.seen
    # article sitemaps are skipped, tips (/rchevebi/) are not treated as recipes
    assert not any("article_1.xml" in u for _, u, _ in f.seen)
    assert not any("188-badagi" in u for _, u, _ in f.seen)

    # second run skips what is already stored
    again = scrape_site(SITES["fiber"], f, db, None, False, False)
    assert (again["already_had"], again["scraped"]) == (1, 0)

    db.save_nutrition(scrape_nutrition(f))
    stats = library.build(db)
    assert stats["recipes"] == 5 and stats["dishes"] == 5

    ing = {r["name"]: dict(r) for r in db.query("SELECT * FROM ingredients")}
    assert ing["ბადრიჯანი"]["sites"] == 2  # kulinaria + fiber
    assert json.loads(ing["ნიორი"]["per_site"]) == {"fiber": 1, "kulinaria": 1}
    assert ing["ბადრიჯანი"]["kcal_100g"] == 25 and ing["ბადრიჯანი"]["nutrition_source"] == "fiber"
    assert ing["ბანანი"]["kcal_100g"] == 89

    out = tmp_path / "export"
    counts = export_all(db, out)
    assert counts["recipes.csv"] == 5 and counts["nutrition.csv"] == 11
    with (out / "recipe_ingredients.csv").open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert {r["source"] for r in rows} == {"kulinaria", "gemrielia", "fiber", "samzareulo", "kerdzebi"}

    openpyxl = pytest.importorskip("openpyxl")
    from recipe_scraper.xlsx import export_xlsx
    wb = openpyxl.load_workbook(export_xlsx(db, out / "recipe_library.xlsx"))
    assert wb.sheetnames == ["Summary", "Ingredient library", "Dish library", "Recipes", "Recipe ingredients",
                             "Nutrition tables"]
    summary = {row[0]: row[1:] for row in wb["Summary"].iter_rows(min_row=2, max_row=7, values_only=True)}
    assert summary["fiber"] == (1, 9, 1, 9, 0)  # recipes, lines, dishes, ingredients, failures
    assert summary["all sites"][0] == 5
    assert wb["Recipes"].max_row == 6

    # photos: every recipe has one; one per dish -> 5 files
    from recipe_scraper.images import download_images
    assert db.query("SELECT COUNT(*) FROM recipes WHERE image LIKE 'https://%'")[0][0] == 5
    assert all(r[0] for r in db.query("SELECT image FROM dishes"))
    st = download_images(db, f, tmp_path / "img", per_dish=True)
    assert st == {"downloaded": 5, "already_had": 0, "failed": 0}
    assert download_images(db, f, tmp_path / "img", per_dish=True)["already_had"] == 5
    files = db.query("SELECT path FROM images")
    assert len(files) == 5 and all(Path(r[0]).read_bytes() == PNG for r in files)
    export_all(db, out)
    with (out / "dish_library.csv").open(encoding="utf-8-sig") as fh:
        assert all(row["image_file"].endswith(".png") for row in csv.DictReader(fh))

    mine = tmp_path / "my_ingredients.csv"
    mine.write_text("name,aliases\nბადრიჯანი,\nნიგვზი,ნიგოზი\nავოკადო,\n", encoding="utf-8")
    dishes = tmp_path / "my_dishes.txt"
    dishes.write_text("ბადრიჯანი ნიგვზით\nაჯაფსანდალი\nხინკალი\n", encoding="utf-8")
    s = compare(db, tmp_path / "cmp", mine, dishes)
    assert (s["n_ingredients"], s["n_dishes"]) == (len(ing), 5)
    assert s["vs_mine"]["ingredients"]["matched_exact"] == 2 and s["vs_mine"]["ingredients"]["mine_not_on_sites"] == 1
    assert s["vs_mine"]["dishes"]["matched_exact"] == 2 and s["vs_mine"]["dishes"]["mine_not_on_sites"] == 1
    missing = (tmp_path / "cmp" / "ingredients_missing_from_my_library.csv").read_text(encoding="utf-8-sig")
    assert "სპაგეტი" in missing and "\nბადრიჯანი," not in missing
    md = (tmp_path / "cmp" / "summary.md").read_text(encoding="utf-8")
    assert f"Ingredient library: **{len(ing)}** distinct ingredients" in md
    assert "Your ingredients vs. the sites" in md and "- yours: 3, matched exactly: 2" in md

    # Without user lists the comparison still writes the cross-site matrices and summary
    s2 = compare(db, tmp_path / "cmp2")
    assert s2["vs_mine"] == {} and (tmp_path / "cmp2" / "dish_site_matrix.csv").exists()


def test_curated_nutrition_map(tmp_path):
    db = DB(tmp_path / "n.db")
    db.save_nutrition([
        {"source": "gemrielia", "group": "რძე", "name": n, "key": "", "kcal": k, "protein": 3, "fat": 3, "carbs": 5}
        for n, k in (("რძე 6 %", 84), ("რძე 3,2%", 57))])
    idx = library._nutrition_index(db, library.load_aliases())
    assert idx[library.ingredient_identity("რძე", {})[0]]["kcal"] == 57  # not the 6% row
