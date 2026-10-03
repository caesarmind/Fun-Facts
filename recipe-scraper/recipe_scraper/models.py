from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class IngredientLine:
    raw: str                         # text exactly as shown on the site
    name: str                        # ingredient name with amount/notes removed
    quantity: float | None = None
    quantity_max: float | None = None  # upper bound for ranges ("1-2")
    quantity_text: str | None = None
    unit: str | None = None          # unit as written ("ს/კ", "გრამი")
    unit_norm: str | None = None     # normalized: g, kg, ml, l, tbsp, tsp, cup, pcs, clove, ...
    grams: float | None = None       # only when derivable from a weight (g/kg), incl. "(≈100 გრ)" notes
    ml: float | None = None          # only when derivable from a volume unit
    note: str | None = None          # "გემოვნებით", "გასაფორმებლად", parenthesised remarks...
    group: str | None = None         # sub-heading, e.g. "ცომისთვის"
    optional: bool = False           # "to taste" / decoration / "სურვილისამებრ"
    canonical: str | None = None     # match key into the ingredient library (filled by library.build)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Recipe:
    source: str
    url: str
    title: str
    ingredients: list[IngredientLine] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    source_id: str | None = None
    description: str | None = None
    categories: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    servings: float | None = None
    servings_text: str | None = None
    total_minutes: int | None = None
    time_text: str | None = None
    calories: float | None = None    # as published by the site (basis varies, usually per serving)
    calories_text: str | None = None
    image: str | None = None
    author: str | None = None
    published: str | None = None
    parse_method: str | None = None  # html / jsonld / api / heuristic / rendered
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ingredients"] = [i.to_dict() for i in self.ingredients]
        return d
