"""Download recipes, items and prices into a local SQLite cache.

Recipes and items rarely change: refresh weekly or on demand.
Prices go stale fast: refresh when older than ~10 minutes.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .api import GW2Api

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "gw2.sqlite"

STATIC_MAX_AGE = 7 * 24 * 3600  # one week
PRICE_MAX_AGE = 10 * 60  # ten minutes

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY,
    type TEXT,
    output_item_id INTEGER NOT NULL,
    output_item_count INTEGER NOT NULL,
    disciplines TEXT NOT NULL,     -- JSON list
    min_rating INTEGER NOT NULL,
    flags TEXT NOT NULL,           -- JSON list
    ingredients TEXT NOT NULL,     -- JSON list of {type, id, count}
    only_items INTEGER NOT NULL    -- 1 if every ingredient is an Item
);
CREATE INDEX IF NOT EXISTS idx_recipes_output ON recipes(output_item_id);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT,
    rarity TEXT,
    level INTEGER,
    vendor_value INTEGER,          -- what an NPC PAYS you, not a buy price
    flags TEXT NOT NULL            -- JSON list
);

CREATE TABLE IF NOT EXISTS prices (
    item_id INTEGER PRIMARY KEY,
    buy_price INTEGER,             -- highest buy order
    buy_qty INTEGER,
    sell_price INTEGER,            -- lowest listing
    sell_qty INTEGER,
    fetched_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def get_meta(db: sqlite3.Connection, key: str) -> str | None:
    row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))


def age_seconds(db: sqlite3.Connection, key: str) -> float | None:
    value = get_meta(db, key)
    return None if value is None else time.time() - float(value)


def normalise_ingredients(recipe: dict) -> list[dict]:
    """Return ingredients as {type, id, count}. Older responses use item_id."""
    out = []
    for ing in recipe.get("ingredients", []):
        if "item_id" in ing:
            out.append({"type": "Item", "id": ing["item_id"], "count": ing["count"]})
        else:
            out.append({"type": ing.get("type", "Item"), "id": ing["id"], "count": ing["count"]})
    # Guild ingredients sit in a separate list in the new schema.
    for ing in recipe.get("guild_ingredients", []):
        out.append({"type": "GuildUpgrade", "id": ing.get("upgrade_id"), "count": ing["count"]})
    return out


async def sync_recipes(api: GW2Api, db: sqlite3.Connection, full: bool = False) -> int:
    """Fetch recipes. By default only fetches IDs we do not have yet."""
    all_ids = await api.get("/recipes")
    have = {r[0] for r in db.execute("SELECT id FROM recipes")}
    todo = all_ids if full else [i for i in all_ids if i not in have]
    recipes = await api.get_many("/recipes", todo)
    rows = []
    for r in recipes:
        ings = normalise_ingredients(r)
        rows.append((
            r["id"], r.get("type"), r["output_item_id"], r["output_item_count"],
            json.dumps(r.get("disciplines", [])), r.get("min_rating", 0),
            json.dumps(r.get("flags", [])), json.dumps(ings),
            int(all(i["type"] == "Item" for i in ings)),
        ))
    db.executemany("INSERT OR REPLACE INTO recipes VALUES (?,?,?,?,?,?,?,?,?)", rows)
    set_meta(db, "recipes_synced_at", str(time.time()))
    db.commit()
    return len(rows)


def needed_item_ids(db: sqlite3.Connection) -> set[int]:
    """Every item that is a recipe output or an Item ingredient."""
    ids: set[int] = set()
    for row in db.execute("SELECT output_item_id, ingredients FROM recipes"):
        ids.add(row["output_item_id"])
        for ing in json.loads(row["ingredients"]):
            if ing["type"] == "Item":
                ids.add(ing["id"])
    return ids


async def sync_items(api: GW2Api, db: sqlite3.Connection, full: bool = False) -> int:
    want = needed_item_ids(db)
    have = {r[0] for r in db.execute("SELECT id FROM items")}
    todo = want if full else want - have
    items = await api.get_many("/items", todo)
    rows = [
        (it["id"], it["name"], it.get("type"), it.get("rarity"), it.get("level"),
         it.get("vendor_value"), json.dumps(it.get("flags", [])))
        for it in items
    ]
    db.executemany("INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?,?)", rows)
    set_meta(db, "items_synced_at", str(time.time()))
    db.commit()
    return len(rows)


async def sync_prices(api: GW2Api, db: sqlite3.Connection) -> int:
    """Fetch TP prices for every tradable item we care about."""
    tradable = set(await api.get("/commerce/prices"))
    want = needed_item_ids(db) & tradable
    prices = await api.get_many("/commerce/prices", want)
    now = int(time.time())
    db.execute("DELETE FROM prices")
    rows = [
        (p["id"], p["buys"]["unit_price"] or None, p["buys"]["quantity"],
         p["sells"]["unit_price"] or None, p["sells"]["quantity"], now)
        for p in prices
    ]
    db.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?)", rows)
    set_meta(db, "prices_synced_at", str(now))
    db.commit()
    return len(rows)


async def sync_all(db: sqlite3.Connection, force_static: bool = False, force_prices: bool = False,
                   log=print) -> None:
    async with GW2Api() as api:
        rec_age = age_seconds(db, "recipes_synced_at")
        if force_static or rec_age is None or rec_age > STATIC_MAX_AGE:
            log(f"Recipes: fetched {await sync_recipes(api, db, full=force_static)} new/updated")
            log(f"Items:   fetched {await sync_items(api, db, full=force_static)} new/updated")
        else:
            log(f"Recipes and items are fresh ({rec_age / 3600:.1f}h old). Skipping.")

        price_age = age_seconds(db, "prices_synced_at")
        if force_prices or price_age is None or price_age > PRICE_MAX_AGE:
            log(f"Prices:  fetched {await sync_prices(api, db)}")
        else:
            log(f"Prices are fresh ({price_age / 60:.1f} min old). Skipping.")
