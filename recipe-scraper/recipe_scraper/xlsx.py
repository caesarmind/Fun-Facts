"""One Excel workbook with everything (needs openpyxl): summary, libraries, recipes, nutrition."""
from __future__ import annotations

import json
from pathlib import Path

from .db import DB

FONT = "Arial"


def export_xlsx(db: DB, path: str | Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sources = [r[0] for r in db.query("SELECT DISTINCT source FROM recipes ORDER BY source")]
    wb = Workbook()
    head_font = Font(name=FONT, bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="3B5B4F")
    body_font = Font(name=FONT, size=10)

    def sheet(title: str, header: list[str], rows, widths: dict[int, int] | None = None, first: bool = False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        ws.append(header)
        for row in rows:
            ws.append(["" if v is None else v for v in row])
        for cell in ws[1]:
            cell.font, cell.fill = head_font, head_fill
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.font = body_font
        ws.freeze_panes = "A2"
        if ws.max_row > 1:
            ws.auto_filter.ref = ws.dimensions
        for i, _ in enumerate(header, 1):
            ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(i, 14)
        return ws

    def j(value: str | None, sep: str = " | ") -> str:
        if not value:
            return ""
        data = json.loads(value)
        if isinstance(data, dict):
            return sep.join(f"{k}: {v}" for k, v in data.items())
        return sep.join(str(x) for x in data)

    # ---- Summary (counts are formulas over the data sheets)
    ws = sheet("Summary", ["site", "recipes", "ingredient lines", "dishes on this site", "ingredients on this site",
                           "failed pages"], [], {1: 16, 2: 12, 3: 16, 4: 18, 5: 22, 6: 13}, first=True)
    n_ing = db.query("SELECT COUNT(*) FROM ingredients")[0][0]
    n_dish = db.query("SELECT COUNT(*) FROM dishes")[0][0]
    for i, s in enumerate(sources):
        r = i + 2
        fails = db.query("SELECT COUNT(*) FROM failures WHERE source=?", (s,))[0][0]
        col = get_column_letter(5 + i)  # in_<site> column in both library sheets
        ws.append([s, f"=COUNTIF(Recipes!B:B,A{r})", f"=COUNTIF('Recipe ingredients'!B:B,A{r})",
                   f"=COUNTIF('Dish library'!{col}:{col},\">0\")",
                   f"=COUNTIF('Ingredient library'!{col}:{col},\">0\")", fails])
    total = len(sources) + 2
    ws.append(["all sites", f"=SUM(B2:B{total - 1})", f"=SUM(C2:C{total - 1})",
               "=COUNTA('Dish library'!A:A)-1", "=COUNTA('Ingredient library'!A:A)-1", f"=SUM(F2:F{total - 1})"])
    for cell in ws[total]:
        cell.font = Font(name=FONT, size=10, bold=True)
    notes = [
        "",
        "How to read this workbook",
        "Ingredient library: one row per distinct ingredient across all sites (Georgian case forms and prep words merged).",
        "  in_<site> = number of that site's recipes using it. kcal/protein/fat/carbs are per 100 g from the gemrielia.ge "
        "and fiber.ge nutrition tables, matched by name — check before using.",
        "Dish library: one row per distinct dish (clickbait removed from titles); in_<site> = recipes on that site.",
        "Recipe ingredients: every ingredient line as published (raw) and parsed (name, quantity, unit, grams/ml).",
        "  ml uses 1 tbsp (ს/კ) = 15 ml, 1 tsp (ჩ/კ) = 5 ml, 1 cup (ჭიქა) = 250 ml. grams are only filled from weights.",
        "Recipes: calories_site is what the site prints (usually per serving), not computed.",
        f"Totals: {n_ing} ingredients, {n_dish} dishes.",
    ]
    for line in notes:
        ws.append([line])
    ws.cell(row=total + 2, column=1).font = Font(name=FONT, bold=True)

    # ---- Ingredient library
    ing = db.query("SELECT * FROM ingredients ORDER BY recipes DESC, name")
    sheet("Ingredient library",
          ["name", "recipes", "sites", "key", *[f"in_{s}" for s in sources], "kcal_100g", "protein_100g", "fat_100g",
           "carbs_100g", "nutrition_match", "spelling variants", "units used", "examples"],
          ([r["name"], r["recipes"], r["sites"], r["key"], *[json.loads(r["per_site"]).get(s, 0) for s in sources],
            r["kcal_100g"], r["protein_100g"], r["fat_100g"], r["carbs_100g"],
            f"{r['nutrition_name']} ({r['nutrition_source']})" if r["nutrition_name"] else "",
            j(r["variants"]), j(r["units"]), j(r["examples"])] for r in ing),
          {1: 28, 4: 18, 9 + len(sources): 30, 10 + len(sources): 40, 11 + len(sources): 24, 12 + len(sources): 60})

    # ---- Dish library
    dishes = db.query("SELECT * FROM dishes ORDER BY sites DESC, recipes DESC, name")
    sheet("Dish library",
          ["dish", "recipes", "sites", "key", *[f"in_{s}" for s in sources], "title variants", "categories",
           "top ingredients", "example url"],
          ([r["name"], r["recipes"], r["sites"], r["key"], *[json.loads(r["per_site"]).get(s, 0) for s in sources],
            j(r["variants"]), j(r["categories"]), j(r["top_ingredients"], ", "),
            (json.loads(r["urls"]) or [""])[0]] for r in dishes),
          {1: 34, 4: 18, 5 + len(sources): 50, 6 + len(sources): 30, 7 + len(sources): 60, 8 + len(sources): 50})

    # ---- Recipes
    recipes = db.query("SELECT * FROM recipes ORDER BY source, id")
    sheet("Recipes",
          ["id", "source", "title", "dish", "categories", "servings", "minutes", "calories_site", "ingredients", "url"],
          ([r["id"], r["source"], r["title"], r["dish_name"], j(r["categories"]), r["servings"], r["total_minutes"],
            r["calories"], r["n_ingredients"], r["url"]] for r in recipes),
          {1: 7, 3: 40, 4: 30, 5: 30, 10: 60})

    # ---- Recipe ingredients
    titles = {r["id"]: r["title"] for r in recipes}
    source_of = {r["id"]: r["source"] for r in recipes}
    lines = db.query("SELECT ri.*, i.name AS cname FROM recipe_ingredients ri LEFT JOIN ingredients i "
                     "ON i.key = ri.canonical ORDER BY ri.recipe_id, ri.position")
    sheet("Recipe ingredients",
          ["recipe_id", "source", "recipe", "raw", "name", "library name", "quantity", "quantity_max", "unit",
           "unit_norm", "grams", "ml", "note", "group", "optional"],
          ([r["recipe_id"], source_of.get(r["recipe_id"]), titles.get(r["recipe_id"]), r["raw"], r["name"], r["cname"],
            r["quantity"], r["quantity_max"], r["unit"], r["unit_norm"], r["grams"], r["ml"], r["note"], r["grp"],
            "yes" if r["optional"] else ""] for r in lines),
          {3: 34, 4: 40, 5: 26, 6: 26, 13: 26})

    # ---- Nutrition tables
    nut = db.query("SELECT * FROM nutrition ORDER BY source, grp, name")
    if nut:
        sheet("Nutrition tables", ["source", "group", "name", "kcal_100g", "protein_100g", "fat_100g", "carbs_100g"],
              ([r["source"], r["grp"], r["name"], r["kcal"], r["protein"], r["fat"], r["carbs"]] for r in nut),
              {2: 40, 3: 36})

    wb.save(path)
    return path
