"""Download recipe / dish photos.

Photos belong to the sites (or their authors). Check each site's terms or ask for permission
before showing them in an app; the URLs and files are kept with their source for that reason.
"""
from __future__ import annotations

import hashlib
import io
import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .db import DB
from .http import Fetcher, FetchError

log = logging.getLogger(__name__)

EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "image/avif": ".avif"}


def _targets(db: DB, per_dish: bool) -> list[dict]:
    rows = [dict(r) for r in db.query(
        "SELECT id, source, source_id, dish_key, image FROM recipes WHERE image IS NOT NULL AND image != '' "
        "ORDER BY id")]
    if not per_dish:
        return rows
    seen: set[str] = set()
    out = []
    for r in rows:  # first recipe of each dish (by id) supplies the dish photo
        key = r["dish_key"] or f"recipe:{r['id']}"
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def download_images(db: DB, fx: Fetcher, out_dir: str | Path = "data/images", per_dish: bool = False,
                    thumb: int | None = None, limit: int | None = None) -> dict[str, int]:
    out = Path(out_dir)
    have = {r["recipe_id"]: r["path"] for r in db.query("SELECT recipe_id, path FROM images")}
    stats = {"downloaded": 0, "already_had": 0, "failed": 0}
    todo = _targets(db, per_dish)
    if limit:
        todo = todo[:limit]
    for n, r in enumerate(todo, 1):
        if r["id"] in have and Path(have[r["id"]]).exists():
            stats["already_had"] += 1
            continue
        try:
            data, ctype = fx.get_bytes(r["image"])
        except FetchError as exc:
            log.warning("image %s: %s", r["image"], exc)
            stats["failed"] += 1
            continue
        if not ctype.startswith("image/"):
            log.warning("image %s: not an image (%s)", r["image"], ctype or "no content type")
            stats["failed"] += 1
            continue
        ext = EXT.get(ctype) or Path(urlsplit(r["image"]).path).suffix.lower() or mimetypes.guess_extension(ctype) or ".img"
        name = f"{r['source_id'] or r['id']}".replace("/", "_")[:120]
        path = out / r["source"] / f"{name}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        width = height = None
        thumb_path = None
        try:
            from PIL import Image  # optional
            with Image.open(io.BytesIO(data)) as im:
                width, height = im.size
                if thumb:
                    im = im.convert("RGB")
                    im.thumbnail((thumb, thumb))
                    thumb_path = out / "thumbs" / r["source"] / f"{name}.jpg"
                    thumb_path.parent.mkdir(parents=True, exist_ok=True)
                    im.save(thumb_path, "JPEG", quality=85)
        except ImportError:
            if thumb:
                log.warning("pip install pillow for --thumb")
        except Exception as exc:  # noqa: BLE001 - a corrupt image shouldn't stop the run
            log.warning("image %s: cannot read (%s)", r["image"], exc)
        with db.lock, db.conn:
            db.conn.execute(
                "INSERT OR REPLACE INTO images (recipe_id, url, path, content_type, bytes, sha1, width, height,"
                " thumb_path, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (r["id"], r["image"], str(path), ctype, len(data), hashlib.sha1(data).hexdigest(), width, height,
                 str(thumb_path) if thumb_path else None, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        stats["downloaded"] += 1
        if n % 50 == 0:
            log.info("images: %d/%d", n, len(todo))
    return stats
