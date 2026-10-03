from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import Recipe

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,
    url           TEXT NOT NULL UNIQUE,
    source_id     TEXT,
    title         TEXT,
    dish_name     TEXT,          -- title with clickbait / filler removed (library.build)
    dish_key      TEXT,          -- match key into dishes (library.build)
    description   TEXT,
    categories    TEXT,          -- JSON list
    tags          TEXT,          -- JSON list
    servings      REAL,
    servings_text TEXT,
    total_minutes INTEGER,
    time_text     TEXT,
    calories      REAL,
    calories_text TEXT,
    image         TEXT,
    author        TEXT,
    published     TEXT,
    steps         TEXT,          -- JSON list
    extra         TEXT,          -- JSON object
    n_ingredients INTEGER,
    parse_method  TEXT,
    scraped_at    TEXT
);
CREATE TABLE IF NOT EXISTS recipe_ingredients (
    recipe_id     INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    grp           TEXT,
    raw           TEXT,
    name          TEXT,
    canonical     TEXT,          -- match key into ingredients (library.build)
    quantity      REAL,
    quantity_max  REAL,
    quantity_text TEXT,
    unit          TEXT,
    unit_norm     TEXT,
    grams         REAL,
    ml            REAL,
    note          TEXT,
    optional      INTEGER,
    PRIMARY KEY (recipe_id, position)
);
CREATE INDEX IF NOT EXISTS ri_canonical ON recipe_ingredients(canonical);
CREATE INDEX IF NOT EXISTS recipes_dish ON recipes(dish_key);
CREATE TABLE IF NOT EXISTS failures (
    url TEXT PRIMARY KEY, source TEXT, reason TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS ingredients (
    key TEXT PRIMARY KEY, name TEXT, recipes INTEGER, sites INTEGER, per_site TEXT,
    variants TEXT, units TEXT, examples TEXT,
    kcal_100g REAL, protein_100g REAL, fat_100g REAL, carbs_100g REAL,
    nutrition_source TEXT, nutrition_name TEXT
);
CREATE TABLE IF NOT EXISTS dishes (
    key TEXT PRIMARY KEY, name TEXT, recipes INTEGER, sites INTEGER, per_site TEXT,
    variants TEXT, categories TEXT, top_ingredients TEXT, urls TEXT
);
CREATE TABLE IF NOT EXISTS nutrition (
    source TEXT, grp TEXT, name TEXT, key TEXT,
    kcal REAL, protein REAL, fat REAL, carbs REAL,
    PRIMARY KEY (source, name)
);
"""

_RECIPE_COLS = ("source", "url", "source_id", "title", "description", "categories", "tags", "servings",
                "servings_text", "total_minutes", "time_text", "calories", "calories_text", "image", "author",
                "published", "steps", "extra", "n_ingredients", "parse_method", "scraped_at")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str | Path = "data/recipes.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()

    def has(self, url: str) -> bool:
        with self.lock:
            return self.conn.execute("SELECT 1 FROM recipes WHERE url=?", (url,)).fetchone() is not None

    def known_urls(self, source: str) -> set[str]:
        with self.lock:
            return {r[0] for r in self.conn.execute("SELECT url FROM recipes WHERE source=?", (source,))}

    def save(self, rec: Recipe) -> int:
        values = {
            "source": rec.source, "url": rec.url, "source_id": rec.source_id, "title": rec.title,
            "description": rec.description, "categories": json.dumps(rec.categories, ensure_ascii=False),
            "tags": json.dumps(rec.tags, ensure_ascii=False), "servings": rec.servings,
            "servings_text": rec.servings_text, "total_minutes": rec.total_minutes, "time_text": rec.time_text,
            "calories": rec.calories, "calories_text": rec.calories_text, "image": rec.image, "author": rec.author,
            "published": rec.published, "steps": json.dumps(rec.steps, ensure_ascii=False),
            "extra": json.dumps(rec.extra, ensure_ascii=False), "n_ingredients": len(rec.ingredients),
            "parse_method": rec.parse_method, "scraped_at": _now(),
        }
        cols = ", ".join(_RECIPE_COLS)
        marks = ", ".join(f":{c}" for c in _RECIPE_COLS)
        updates = ", ".join(f"{c}=excluded.{c}" for c in _RECIPE_COLS if c != "url")
        with self.lock, self.conn:
            self.conn.execute(f"INSERT INTO recipes ({cols}) VALUES ({marks}) ON CONFLICT(url) DO UPDATE SET {updates}",
                              values)
            rid = self.conn.execute("SELECT id FROM recipes WHERE url=?", (rec.url,)).fetchone()[0]
            self.conn.execute("DELETE FROM recipe_ingredients WHERE recipe_id=?", (rid,))
            self.conn.executemany(
                "INSERT INTO recipe_ingredients (recipe_id, position, grp, raw, name, canonical, quantity, quantity_max,"
                " quantity_text, unit, unit_norm, grams, ml, note, optional) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(rid, pos, i.group, i.raw, i.name, i.canonical, i.quantity, i.quantity_max, i.quantity_text, i.unit,
                  i.unit_norm, i.grams, i.ml, i.note, int(i.optional)) for pos, i in enumerate(rec.ingredients)],
            )
            self.conn.execute("DELETE FROM failures WHERE url=?", (rec.url,))
        return rid

    def fail(self, url: str, source: str, reason: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO failures (url, source, reason, at) VALUES (?,?,?,?)",
                              (url, source, reason[:500], _now()))

    def save_nutrition(self, rows: list[dict]) -> None:
        with self.lock, self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO nutrition (source, grp, name, key, kcal, protein, fat, carbs)"
                " VALUES (:source, :group, :name, :key, :kcal, :protein, :fat, :carbs)", rows)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def close(self) -> None:
        self.conn.close()
