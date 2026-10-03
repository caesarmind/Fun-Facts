# Georgian recipe scraper → dish & ingredient libraries

Scrapes the Georgian recipe sites below, parses every ingredient line into
**quantity / unit / name / grams-or-ml**, and builds:

- an **ingredient library**: every distinct ingredient across all sites, with recipe counts per site,
  spelling variants, typical units, and kcal/protein/fat/carbs per 100 g where a Georgian nutrition
  table has a match;
- a **dish library**: every distinct dish (clickbait stripped from titles), which sites have it,
  and its most common ingredients;
- a **comparison** of the sites with each other and with *your* app's ingredient and dish lists:
  what the sites have that you don't, and the reverse.

| site | how recipes are found | how ingredients are read | size (Oct 2026) |
|---|---|---|---|
| kulinaria.ge | category pages `?page=N` (no sitemap) | `.list__item` (qty / unit / name) | ~1,000 |
| gemrielia.ge | `sitemap.xml` → `recipe_*.xml` | site JSON API `POST /api/recipe/` (it is a Vue app) | ~9,700 |
| fiber.ge | `recepti-sitemap.xml` | `.recipe-ingredients__list li`, plus time/servings/**kcal** | ~150 |
| samzareulo.net | `sitemap.xml` + `/lastnews/page/N/` | `label.control` (“რძე - 300 მლ.”) | ~200 |
| kerdzebi.ge | `sitemap.xml` + category pages | heading-anchored list (Tailwind markup) | small |
| receptebi.ge | — | domain did not resolve; registered as a generic site in case it returns | — |

Any other site can be added from the command line (`--sites name=https://site.ge`); it is read
via schema.org JSON-LD or an "ინგრედიენტები" heading.

Nutrition tables (per 100 g, Georgian names) are also collected:
**gemrielia.ge** (629 foods in 40 groups, embedded in its calorie calculator) and
**fiber.ge/kaloriebis-tskhrili** (fruit, vegetables, meat & fish, grains).

## Install

Python 3.10+.

```bash
cd recipe-scraper
pip install -r requirements.txt
```

## Run

```bash
# quick test: 5 recipes per site, everything else as in a full run
python -m recipe_scraper run --limit 5

# full run, compared with your own lists
python -m recipe_scraper run \
    --my-ingredients my_ingredients.csv \
    --my-dishes my_dishes.csv
```

`run` = scrape → nutrition tables → build libraries → export → compare. Results go to `data/`:

```
data/recipes.db                  SQLite with everything (recipes, recipe_ingredients, ingredients, dishes, nutrition)
data/export/recipes.jsonl        one JSON object per recipe, with parsed ingredients and steps
data/export/recipes.csv
data/export/recipe_ingredients.csv   one row per ingredient line: raw text, name, quantity, unit, grams, ml, canonical
data/export/ingredient_library.csv   the ingredient library (see columns below)
data/export/dish_library.csv         the dish library
data/export/nutrition.csv            both nutrition tables
data/export/failures.csv             pages that could not be parsed, with the reason
data/export/compare/summary.md       the comparison report
data/export/compare/*.csv            matrices and match lists (see below)
```

CSV files are UTF-8 with BOM, so Excel and Google Sheets show Georgian correctly.

**Time:** requests are rate-limited to 1 per second *per site* (`--delay`), and sites run in
parallel. gemrielia.ge (~9.7k recipes) takes about 3 hours; the other sites finish well within
that (kulinaria, the next largest, is ~1,300 requests ≈ 25 minutes).
Runs are **resumable**: recipes already in the database are skipped and failed pages are retried.
Every downloaded page is cached in `data/cache/`, so `run --offline --rescrape` re-parses everything
without touching the network (useful after changing parsing rules).

### Individual steps

```bash
python -m recipe_scraper scrape --sites kulinaria,fiber      # just scrape (and rebuild libraries)
python -m recipe_scraper discover --sites gemrielia --out urls.tsv
python -m recipe_scraper nutrition
python -m recipe_scraper build --aliases my_aliases.csv       # after editing aliases
python -m recipe_scraper export --out data/export
python -m recipe_scraper compare --my-ingredients mine.csv --my-dishes dishes.txt --out data/compare
python -m recipe_scraper stats
python -m recipe_scraper parse gemrielia https://gemrielia.ge/recipe/9724-...   # debug one page
```

Global options: `--db`, `--cache`, `--delay`, `--offline`, `--refresh`, `--user-agent`,
`--ignore-robots`, `-v`.

## Photos

Every recipe's main photo URL is stored, together with the page it was published on:
`photos.csv` and the workbook's **Photos** sheet list one row per photo (dish, recipe, site,
photo url, published on, author, and `dish photo = yes` for the photo chosen to represent the dish).
In the workbook the links are clickable. To download the files themselves:

```bash
python -m recipe_scraper images --per-dish            # one photo per dish  -> data/images/<site>/
python -m recipe_scraper images                       # one photo per recipe
python -m recipe_scraper images --per-dish --thumb 600   # plus 600px JPEG copies in data/images/thumbs/ (pip install pillow)
python -m recipe_scraper run --images dish            # or as part of a full run
```

Downloads are rate-limited like pages, skip files already downloaded, and are recorded in the
`images` table (size, SHA-1, width × height); the `photo file` / `image_file` columns are then filled.

**Rights:** the photos belong to the sites or their authors. Use them as internal reference
(e.g. to pick or check dishes) and get permission, license, or shoot your own before showing
them in your app.

## Comparing with your library

Give your lists as CSV with a `name` column (optional `aliases` column, `|`-separated) or as a
plain `.txt` with one name per line. See `examples/`.

```csv
name,aliases
ნიგოზი,ნიგვზის გული
მცენარეული ზეთი,ზეთი|მზესუმზირის ზეთი
```

Output in the compare folder:

| file | what it answers |
|---|---|
| `ingredients_missing_from_my_library.csv` | ingredients used on the sites that your library lacks, most used first |
| `ingredients_not_found_on_sites.csv` | your ingredients no scraped recipe uses |
| `ingredients_matched.csv` | your name → site name, `exact` or `fuzzy`, with per-site counts |
| `dishes_missing_from_my_library.csv`, `dishes_not_found_on_sites.csv`, `dishes_matched.csv` | same for dishes |
| `ingredient_site_matrix.csv`, `dish_site_matrix.csv` | which site has what (`only_on` marks items unique to one site) |
| `summary.md` | per-site counts, top ingredients, dishes found on most sites, your coverage |

Fuzzy matching (`--fuzzy 0.88`, `1` to disable) catches spelling variants; check the `fuzzy`
rows by hand.

## How matching works

Georgian inflects nouns (ნიგოზი / ნიგვზის / ნიგვზით), so names are compared with a light stemmer
that strips case endings and folds the syncope alternation (ქათამი/ქათმის, პომიდორი/პომიდვრის).
For ingredients, preparation and size words are dropped first (`დაჭრილი`, `გახეხილი`, `დიდი` …),
so "დაკეპილი ნიგოზი" and "ნიგვზის" land on the same entry. For dishes, titles are split at
`:`, ` - `, `!` and ", რომელიც…", the segment without clickbait is kept, and filler words
(`რეცეპტი`, `უგემრიელესი`, `მარტივი` …) are removed:

> ქოქოსის და ბანანის ბურთულები - მხოლოდ 3 ინგრედიენტით! → **ქოქოსის და ბანანის ბურთულები**

Synonyms go in `recipe_scraper/data/aliases.csv` (built in) or your own file via `--aliases`
(`alias,canonical`). After editing, run `build --aliases …` and then `export`/`compare` to regroup
without re-downloading anything.

### Ingredient line parsing

| line on the site | name | qty | unit | grams / ml | note |
|---|---|---|---|---|---|
| `ბანანი - 1 ცალი (დაახლოებით 100–120 გრ)` | ბანანი | 1 | pcs | 110 g | დაახლოებით 100–120 გრ |
| `1 ს/კ ტომატის პასტა` | ტომატის პასტა | 1 | tbsp | 15 ml | |
| `ფქვილი (500 გრ)` | ფქვილი | 500 | g | 500 g | |
| `1-2 კბილი ნიორი` | ნიორი | 1–2 | clove | | |
| `ნახევარი ჭიქა შაქარი` | შაქარი | 0.5 | cup | 125 ml | |
| `მწიკვი მარილი` | მარილი | 1 | pinch | | |
| `მარილი გემოვნებით` | მარილი | | | | გემოვნებით (optional) |
| `გუანჩალე ან პანჩეტა` | გუანჩალე | | | | ან პანჩეტა |

Lines like `ცომისთვის:` become ingredient groups. Volume-to-ml uses 1 tbsp = 15 ml,
1 tsp = 5 ml, 1 cup (ჭიქა) = 250 ml. Grams are only filled from weights, never guessed from volume.

## Notes and caveats

- **gemrielia.ge** renders in the browser; the scraper calls the same JSON endpoint its
  front-end uses (found in the site's JS bundle). The response's field names were inferred from
  that bundle, and the parser accepts several shapes (HTML, text, lists, Editor.js). Check one
  page first with `python -m recipe_scraper parse gemrielia <recipe-url>`. If the API ever
  changes, `--render` falls back to rendering pages with Playwright.
- **kerdzebi.ge** republishes recipes from other sites and some of its ingredient lists contain
  instructions or author credits. Lines that are clearly not ingredients are dropped and kept
  in the recipe's `extra.dropped_lines`.
- **Calories published by sites** (`calories_site`) are as printed (fiber.ge and kerdzebi.ge,
  usually per serving). Library kcal values are per 100 g from the nutrition tables.
- The scraper honours `robots.txt`, identifies itself in the User-Agent and waits between
  requests. Check each site's terms before using the data commercially; recipes and photos
  are the sites' content.

## Adding a site

Generic (no code): `--sites kulinaria,fiber,sakhluri=https://sakhluri.ge` — optionally restrict
URLs with a regex: `'sakhluri=https://sakhluri.ge|/recipe/'`.

Custom adapter: subclass `Site` in `recipe_scraper/sites/`, implement `discover()` (yield recipe
URLs) and `parse(url, html)` (return a `Recipe`), register it in `sites/__init__.py`, and add a
saved page under `tests/fixtures/` with a test.

## Tests

```bash
pip install pytest
python -m pytest
```

Fixtures are real pages saved from each site (plus labelled synthetic samples for the gemrielia
API and rendered page); `tests/test_pipeline.py` runs discover → scrape → nutrition → build →
export → compare end to end against them.
