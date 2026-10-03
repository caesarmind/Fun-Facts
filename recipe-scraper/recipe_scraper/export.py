"""Write the database out as CSV (UTF-8 with BOM so Excel shows Georgian) and JSONL."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .db import DB


def _write_csv(path: Path, header: list[str], rows) -> int:
    n = 0
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows:
            w.writerow(row)
            n += 1
    return n


def _j(value: str | None, sep: str = " | ") -> str:
    """JSON list/dict column -> readable cell."""
    if not value:
        return ""
    data = json.loads(value)
    if isinstance(data, dict):
        return sep.join(f"{k}: {v}" for k, v in data.items())
    if isinstance(data, list):
        return sep.join(str(x) for x in data)
    return str(data)


def export_all(db: DB, out_dir: str | Path) -> dict[str, int]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sources = [r[0] for r in db.query("SELECT DISTINCT source FROM recipes ORDER BY source")]
    counts: dict[str, int] = {}

    ing_by_recipe: dict[int, list[dict]] = {}
    ing_rows = db.query(
        "SELECT ri.*, i.name AS canonical_name FROM recipe_ingredients ri "
        "LEFT JOIN ingredients i ON i.key = ri.canonical ORDER BY ri.recipe_id, ri.position")
    for r in ing_rows:
        ing_by_recipe.setdefault(r["recipe_id"], []).append(dict(r))

    recipes = db.query("SELECT * FROM recipes ORDER BY source, id")
    with (out / "recipes.jsonl").open("w", encoding="utf-8") as f:
        for r in recipes:
            d = dict(r)
            for k in ("categories", "tags", "steps", "extra"):
                d[k] = json.loads(d[k]) if d[k] else ([] if k != "extra" else {})
            d["ingredients"] = [{k: v for k, v in i.items() if k != "recipe_id"} for i in ing_by_recipe.get(r["id"], [])]
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    counts["recipes.jsonl"] = len(recipes)

    counts["recipes.csv"] = _write_csv(
        out / "recipes.csv",
        ["id", "source", "url", "title", "dish_name", "dish_key", "categories", "servings", "total_minutes",
         "calories_site", "n_ingredients", "ingredients", "parse_method"],
        ([r["id"], r["source"], r["url"], r["title"], r["dish_name"], r["dish_key"], _j(r["categories"]),
          r["servings"], r["total_minutes"], r["calories"], r["n_ingredients"],
          " | ".join(i["name"] for i in ing_by_recipe.get(r["id"], [])), r["parse_method"]] for r in recipes))

    titles = {r["id"]: (r["source"], r["title"], r["url"]) for r in recipes}
    counts["recipe_ingredients.csv"] = _write_csv(
        out / "recipe_ingredients.csv",
        ["recipe_id", "source", "recipe_title", "recipe_url", "position", "group", "raw", "name", "canonical_key",
         "canonical_name", "quantity", "quantity_max", "unit", "unit_norm", "grams", "ml", "note", "optional"],
        ([r["recipe_id"], *titles.get(r["recipe_id"], ("", "", "")), r["position"], r["grp"], r["raw"], r["name"],
          r["canonical"], r["canonical_name"], r["quantity"], r["quantity_max"], r["unit"], r["unit_norm"],
          r["grams"], r["ml"], r["note"], r["optional"]] for r in ing_rows))

    ingredients = db.query("SELECT * FROM ingredients ORDER BY recipes DESC, name")
    counts["ingredient_library.csv"] = _write_csv(
        out / "ingredient_library.csv",
        ["key", "name", "recipes", "sites", *[f"in_{s}" for s in sources], "variants", "units", "examples",
         "kcal_100g", "protein_100g", "fat_100g", "carbs_100g", "nutrition_source", "nutrition_name"],
        ([r["key"], r["name"], r["recipes"], r["sites"],
          *[json.loads(r["per_site"]).get(s, 0) for s in sources], _j(r["variants"]), _j(r["units"]),
          _j(r["examples"]), r["kcal_100g"], r["protein_100g"], r["fat_100g"], r["carbs_100g"],
          r["nutrition_source"], r["nutrition_name"]] for r in ingredients))

    dishes = db.query("SELECT * FROM dishes ORDER BY sites DESC, recipes DESC, name")
    counts["dish_library.csv"] = _write_csv(
        out / "dish_library.csv",
        ["key", "name", "recipes", "sites", *[f"in_{s}" for s in sources], "title_variants", "categories",
         "top_ingredients", "urls"],
        ([r["key"], r["name"], r["recipes"], r["sites"], *[json.loads(r["per_site"]).get(s, 0) for s in sources],
          _j(r["variants"]), _j(r["categories"]), _j(r["top_ingredients"]), _j(r["urls"], " ")] for r in dishes))

    nutrition = db.query("SELECT * FROM nutrition ORDER BY source, grp, name")
    if nutrition:
        counts["nutrition.csv"] = _write_csv(
            out / "nutrition.csv", ["source", "group", "name", "kcal_100g", "protein_100g", "fat_100g", "carbs_100g"],
            ([r["source"], r["grp"], r["name"], r["kcal"], r["protein"], r["fat"], r["carbs"]] for r in nutrition))

    failures = db.query("SELECT * FROM failures ORDER BY source, url")
    counts["failures.csv"] = _write_csv(out / "failures.csv", ["source", "url", "reason", "at"],
                                        ([r["source"], r["url"], r["reason"], r["at"]] for r in failures))
    return counts
