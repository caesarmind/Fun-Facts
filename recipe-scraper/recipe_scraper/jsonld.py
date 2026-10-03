"""schema.org/Recipe extraction from <script type="application/ld+json">."""
from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from .textutil import clean, first_number, parse_minutes


def _walk(node: Any):
    if isinstance(node, list):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                yield from _walk(node[key])


def _is_recipe(node: dict) -> bool:
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.lower() == "recipe" for x in types)


def find_recipe(soup: BeautifulSoup) -> dict | None:
    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = tag.string or tag.get_text() or ""
        raw = re.sub(r"[\x00-\x1f]", " ", raw).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in _walk(data):
            if _is_recipe(node):
                return node
    return None


def _texts(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [clean(value)] if clean(value) else []
    if isinstance(value, list):
        return [t for v in value for t in _texts(v)]
    if isinstance(value, dict):
        return _texts(value.get("name") or value.get("text") or value.get("url"))
    return [clean(str(value))]


def instructions(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        text = BeautifulSoup(value, "lxml").get_text("\n") if "<" in value else value
        out.extend(clean(x) for x in re.split(r"\n+", text) if clean(x))
    elif isinstance(value, list):
        for v in value:
            out.extend(instructions(v))
    elif isinstance(value, dict):
        if "itemListElement" in value:
            out.extend(instructions(value["itemListElement"]))
        elif value.get("text"):
            out.extend(instructions(value["text"]))
    return out


def summarize(node: dict) -> dict[str, Any]:
    """Flatten a Recipe node into plain fields."""
    image = node.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")
    author = _texts(node.get("author"))
    nutrition = node.get("nutrition") or {}
    total = node.get("totalTime") or None
    minutes = parse_minutes(total) if total else None
    if minutes is None and (node.get("prepTime") or node.get("cookTime")):
        minutes = (parse_minutes(node.get("prepTime")) or 0) + (parse_minutes(node.get("cookTime")) or 0) or None
    calories = clean(str(nutrition.get("calories"))) if isinstance(nutrition, dict) and nutrition.get("calories") else None
    yield_text = _texts(node.get("recipeYield"))
    kw = node.get("keywords")
    keywords = [clean(k) for k in kw.split(",")] if isinstance(kw, str) else _texts(kw)
    return {
        "title": clean(node.get("name")),
        "description": clean(node.get("description")) or None,
        "ingredients": _texts(node.get("recipeIngredient") or node.get("ingredients")),
        "steps": instructions(node.get("recipeInstructions")),
        "categories": _texts(node.get("recipeCategory")) + _texts(node.get("recipeCuisine")),
        "tags": [k for k in keywords if k],
        "servings_text": yield_text[0] if yield_text else None,
        "servings": first_number(yield_text[0]) if yield_text else None,
        "total_minutes": minutes,
        "calories_text": calories,
        "calories": first_number(calories),
        "image": image if isinstance(image, str) else None,
        "author": author[0] if author else None,
        "published": node.get("datePublished"),
    }
