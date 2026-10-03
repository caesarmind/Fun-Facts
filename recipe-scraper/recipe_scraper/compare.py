"""Compare the scraped libraries with each other and with your app's own lists.

Your lists are CSV (a 'name' column, optional 'aliases' column with '|'-separated synonyms,
any other columns are carried through) or plain text with one name per line.
"""
from __future__ import annotations

import csv
import difflib
import json
from pathlib import Path

from .db import DB
from .export import _write_csv
from .library import dish_identity, ingredient_identity, load_aliases
from .textutil import clean


def load_user_list(path: str | Path) -> list[dict]:
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    if p.suffix.lower() == ".txt" or (lines and "," not in lines[0] and "\t" not in lines[0]):
        return [{"name": clean(x), "aliases": []} for x in lines if clean(x)]
    try:
        dialect = csv.Sniffer().sniff(lines[0], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(lines, dialect=dialect))
    if not rows:
        return []
    cols = {c.lower().strip(): c for c in rows[0].keys() if c}
    name_col = next((cols[c] for c in ("name", "name_ka", "title", "dish", "ingredient", "სახელი", "დასახელება")
                     if c in cols), list(rows[0].keys())[0])
    alias_col = next((cols[c] for c in ("aliases", "alias", "synonyms") if c in cols), None)
    out = []
    for r in rows:
        name = clean(r.get(name_col))
        if name:
            aliases = [clean(a) for a in (r.get(alias_col) or "").split("|") if clean(a)] if alias_col else []
            out.append({"name": name, "aliases": aliases, "row": r})
    return out


def _match(user_items: list[dict], lib: list[dict], identity, cutoff: float) -> tuple[list, set[str]]:
    """-> ([(user item, lib row | None, match_type)], matched lib keys)."""
    by_key = {r["key"]: r for r in lib}
    keys = list(by_key)
    results, matched = [], set()
    for item in user_items:
        cand_keys = [identity(n)[0] for n in [item["name"], *item["aliases"]]]
        hit, kind = None, None
        for k in cand_keys:
            if k in by_key:
                hit, kind = by_key[k], "exact"
                break
        if hit is None and cutoff < 1:
            for k in cand_keys:
                close = difflib.get_close_matches(k, keys, n=1, cutoff=cutoff)
                if close:
                    hit, kind = by_key[close[0]], "fuzzy"
                    break
        if hit is not None:
            matched.add(hit["key"])
        results.append((item, hit, kind))
    return results, matched


def compare(db: DB, out_dir: str | Path, my_ingredients: str | Path | None = None,
            my_dishes: str | Path | None = None, aliases_path: str | Path | None = None,
            fuzzy_cutoff: float = 0.88) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    aliases = load_aliases(aliases_path)
    sources = [r[0] for r in db.query("SELECT DISTINCT source FROM recipes ORDER BY source")]
    ings = [dict(r) for r in db.query("SELECT * FROM ingredients ORDER BY recipes DESC")]
    dishes = [dict(r) for r in db.query("SELECT * FROM dishes ORDER BY sites DESC, recipes DESC")]
    for r in ings + dishes:
        r["per_site"] = json.loads(r["per_site"] or "{}")
    summary: dict = {"sources": sources, "n_ingredients": len(ings), "n_dishes": len(dishes), "vs_mine": {}}

    # Cross-site coverage: what does each site have that the others don't?
    def matrix(rows, fname):
        _write_csv(out / fname, ["key", "name", "recipes", "sites", *sources, "only_on"],
                   ([r["key"], r["name"], r["recipes"], r["sites"], *[r["per_site"].get(s, 0) for s in sources],
                     next(iter(r["per_site"])) if r["sites"] == 1 else ""] for r in rows))
    matrix(ings, "ingredient_site_matrix.csv")
    matrix(dishes, "dish_site_matrix.csv")
    summary["only_on_one_site"] = {
        s: {"dishes": sum(1 for d in dishes if d["sites"] == 1 and s in d["per_site"]),
            "ingredients": sum(1 for i in ings if i["sites"] == 1 and s in i["per_site"])} for s in sources}

    def against(user_path, lib, identity, label):
        items = load_user_list(user_path)
        results, matched = _match(items, lib, identity, fuzzy_cutoff)
        _write_csv(out / f"{label}_matched.csv",
                   ["my_name", "match", "site_name", "key", "recipes", "sites", *sources],
                   ([it["name"], kind, hit["name"], hit["key"], hit["recipes"], hit["sites"],
                     *[hit["per_site"].get(s, 0) for s in sources]] for it, hit, kind in results if hit))
        _write_csv(out / f"{label}_missing_from_my_library.csv",
                   ["name", "key", "recipes", "sites", *sources, "variants"],
                   ([r["name"], r["key"], r["recipes"], r["sites"], *[r["per_site"].get(s, 0) for s in sources],
                     " | ".join(json.loads(r["variants"] or "{}"))] for r in lib if r["key"] not in matched))
        _write_csv(out / f"{label}_not_found_on_sites.csv", ["my_name", "aliases"],
                   ([it["name"], " | ".join(it["aliases"])] for it, hit, _ in results if hit is None))
        summary["vs_mine"][label] = {
            "mine": len(items),
            "matched_exact": sum(1 for _, h, k in results if h and k == "exact"),
            "matched_fuzzy": sum(1 for _, h, k in results if h and k == "fuzzy"),
            "mine_not_on_sites": sum(1 for _, h, _ in results if h is None),
            "sites_not_in_mine": sum(1 for r in lib if r["key"] not in matched),
            "top_missing": [(r["name"], r["recipes"], r["sites"]) for r in lib if r["key"] not in matched][:25],
        }

    if my_ingredients:
        against(my_ingredients, ings, lambda n: ingredient_identity(n, aliases), "ingredients")
    if my_dishes:
        against(my_dishes, dishes, lambda n: dish_identity(n, aliases), "dishes")

    (out / "summary.md").write_text(_summary_md(db, summary, ings, dishes), encoding="utf-8")
    return summary


def _summary_md(db: DB, s: dict, ings: list[dict], dishes: list[dict]) -> str:
    lines = ["# Recipe library comparison", ""]
    lines += ["## Per site", "", "| site | recipes | ingredient lines | failed pages | dishes only here | ingredients only here |",
              "|---|---:|---:|---:|---:|---:|"]
    for src in s["sources"]:
        n = db.query("SELECT COUNT(*), COALESCE(SUM(n_ingredients),0) FROM recipes WHERE source=?", (src,))[0]
        f = db.query("SELECT COUNT(*) FROM failures WHERE source=?", (src,))[0][0]
        o = s["only_on_one_site"][src]
        lines.append(f"| {src} | {n[0]} | {n[1]} | {f} | {o['dishes']} | {o['ingredients']} |")
    lines += ["", f"Ingredient library: **{s['n_ingredients']}** distinct ingredients. "
                  f"Dish library: **{s['n_dishes']}** distinct dishes.", ""]
    lines += ["## Most used ingredients", "", "| ingredient | recipes | sites | kcal/100g |", "|---|---:|---:|---:|"]
    lines += [f"| {r['name']} | {r['recipes']} | {r['sites']} | {r['kcal_100g'] if r['kcal_100g'] is not None else ''} |"
              for r in ings[:30]]
    lines += ["", "## Dishes found on the most sites", "", "| dish | sites | recipes |", "|---|---:|---:|"]
    lines += [f"| {r['name']} | {r['sites']} | {r['recipes']} |" for r in dishes[:30]]
    for label, d in s["vs_mine"].items():
        if d:
            lines += ["", f"## Your {label} vs. the sites", "",
                      f"- yours: {d['mine']}, matched exactly: {d['matched_exact']}, fuzzy: {d['matched_fuzzy']}",
                      f"- yours not found on any site: {d['mine_not_on_sites']}",
                      f"- on the sites but missing from yours: {d['sites_not_in_mine']}", "",
                      "Top missing (name, recipes, sites):", ""]
            lines += [f"- {n} ({r}, {st})" for n, r, st in d["top_missing"]]
    return "\n".join(lines) + "\n"
