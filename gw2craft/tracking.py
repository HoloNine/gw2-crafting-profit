"""Account Trading Post sync: transactions, open orders, delivery box, wallet, snapshots.

The GW2 API only keeps 90 days of TP history. Every transaction we see is saved in
SQLite (unique on its id), so the local history keeps growing past 90 days.
Sync at least once every few weeks: anything older than 90 days that was never
synced is lost for good.

Each sync:
1. Pull completed buys + sells, open buy orders + listings, delivery box, wallet.
2. Insert new transactions (existing ids are skipped).
3. Refresh TP prices for every item involved.
4. Save a snapshot of the account value.

Needs a key with: account, tradingpost, wallet (inventories optional, for stock value).
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

from .api import GW2Api
from .pricing import exchange_fee, net

SCHEMA = """
CREATE TABLE IF NOT EXISTS tp_history (
    id INTEGER PRIMARY KEY,         -- transaction id from the API
    kind TEXT NOT NULL,             -- 'buy' or 'sell'
    item_id INTEGER NOT NULL,
    price INTEGER NOT NULL,         -- copper per unit
    quantity INTEGER NOT NULL,
    created TEXT NOT NULL,          -- when the order/listing was placed (ISO time)
    purchased TEXT NOT NULL,        -- when it completed
    first_seen INTEGER NOT NULL     -- unix time of the sync that saved it
);
CREATE INDEX IF NOT EXISTS idx_tp_history_item ON tp_history(item_id);
CREATE INDEX IF NOT EXISTS idx_tp_history_purchased ON tp_history(purchased);

CREATE TABLE IF NOT EXISTS tp_current (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,             -- 'buy' (open buy order) or 'sell' (open listing)
    item_id INTEGER NOT NULL,
    price INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    created TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    time INTEGER PRIMARY KEY,       -- unix time
    wallet INTEGER NOT NULL,        -- coins in the wallet
    delivery_coins INTEGER NOT NULL,
    delivery_items INTEGER NOT NULL,
    listings_value INTEGER NOT NULL, -- open listings, after the 10% exchange fee still to pay
    buy_orders INTEGER NOT NULL,    -- coins locked in open buy orders
    stock_value INTEGER,            -- items owned, if sold instantly after tax (NULL = not counted)
    detail TEXT                     -- JSON with extra info
);

-- Prices at each sync, for items we traded (lets us estimate craft costs "at the time").
CREATE TABLE IF NOT EXISTS price_history (
    item_id INTEGER NOT NULL,
    time INTEGER NOT NULL,
    buy_price INTEGER,
    sell_price INTEGER,
    PRIMARY KEY (item_id, time)
);

-- Estimated craft cost of items you sell and can craft, saved at each sync
-- (so a sale can be valued at the craft cost "at the time").
CREATE TABLE IF NOT EXISTS craft_estimates (
    item_id INTEGER NOT NULL,
    time INTEGER NOT NULL,
    cost_each INTEGER NOT NULL,     -- copper per item, patient prices, cheapest recipe you can craft
    PRIMARY KEY (item_id, time)
);

-- Optional: craft runs you logged yourself, with their real cost.
CREATE TABLE IF NOT EXISTS craft_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    cost_each INTEGER NOT NULL,     -- copper per item made
    note TEXT
);
"""

COIN = 1  # currency id for coins in /account/wallet


def ensure_schema(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)


@dataclass
class SyncResult:
    new_buys: int
    new_sells: int
    open_buys: int
    open_sells: int
    snapshot: dict
    seconds: float


def listing_value(price: int, qty: int) -> int:
    """What an open listing pays out when it sells: the 5% listing fee is already paid,
    only the 10% exchange fee is still to come."""
    total = price * qty
    return total - exchange_fee(total)


async def fetch_stock(api: GW2Api) -> dict[int, int]:
    from .account import fetch_owned
    return await fetch_owned(api)


async def sync_account(api: GW2Api, db: sqlite3.Connection, with_stock: bool = True) -> SyncResult:
    start = time.time()
    ensure_schema(db)
    now = int(start)

    token = await api.get("/tokeninfo", auth=True)
    perms = set(token.get("permissions", []))
    missing = {"account", "tradingpost", "wallet"} - perms
    if missing:
        raise RuntimeError(f"Your API key is missing: {', '.join(sorted(missing))}. "
                           "Make a new key at https://account.arena.net/applications.")

    hist_buys = await api.get_pages("/commerce/transactions/history/buys", auth=True)
    hist_sells = await api.get_pages("/commerce/transactions/history/sells", auth=True)
    cur_buys = await api.get_pages("/commerce/transactions/current/buys", auth=True)
    cur_sells = await api.get_pages("/commerce/transactions/current/sells", auth=True)
    delivery = await api.get("/commerce/delivery", auth=True)
    wallet = await api.get("/account/wallet", auth=True)

    # 2. Save new completed transactions (INSERT OR IGNORE skips ids we already have).
    before = {k: db.execute("SELECT COUNT(*) FROM tp_history WHERE kind=?", (k,)).fetchone()[0]
              for k in ("buy", "sell")}
    for kind, rows in (("buy", hist_buys), ("sell", hist_sells)):
        db.executemany("INSERT OR IGNORE INTO tp_history VALUES (?,?,?,?,?,?,?,?)", [
            (t["id"], kind, t["item_id"], t["price"], t["quantity"], t["created"], t["purchased"], now)
            for t in rows])
    after = {k: db.execute("SELECT COUNT(*) FROM tp_history WHERE kind=?", (k,)).fetchone()[0]
             for k in ("buy", "sell")}

    # Open orders and listings: replace with what's open now.
    db.execute("DELETE FROM tp_current")
    for kind, rows in (("buy", cur_buys), ("sell", cur_sells)):
        db.executemany("INSERT INTO tp_current VALUES (?,?,?,?,?,?)", [
            (t["id"], kind, t["item_id"], t["price"], t["quantity"], t["created"]) for t in rows])

    # 3. Fresh prices for every item involved (plus your stock, if counted).
    stock: dict[int, int] = {}
    if with_stock and "inventories" in perms and "characters" in perms:
        stock = await fetch_stock(api)
    tradable = set(await api.get("/commerce/prices"))
    involved = ({t["item_id"] for t in hist_buys + hist_sells + cur_buys + cur_sells}
                | {i["id"] for i in delivery.get("items", [])} | set(stock)) & tradable
    prices = await api.get_many("/commerce/prices", involved)
    db.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?)", [
        (p["id"], p["buys"]["unit_price"] or None, p["buys"]["quantity"],
         p["sells"]["unit_price"] or None, p["sells"]["quantity"], now) for p in prices])
    traded = {t["item_id"] for t in hist_buys + hist_sells + cur_buys + cur_sells}
    db.executemany("INSERT OR REPLACE INTO price_history VALUES (?,?,?,?)", [
        (p["id"], now, p["buys"]["unit_price"] or None, p["sells"]["unit_price"] or None)
        for p in prices if p["id"] in traded])
    # Names for items we've never cached (e.g. loot you sold).
    have = {r[0] for r in db.execute("SELECT id FROM items")}
    new_items = await api.get_many("/items", (traded | {i["id"] for i in delivery.get("items", [])}) - have)
    db.executemany("INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?,?)", [
        (it["id"], it["name"], it.get("type"), it.get("rarity"), it.get("level"),
         it.get("vendor_value"), json.dumps(it.get("flags", []))) for it in new_items])

    # 3b. Craft-cost estimates for items you sell and can craft right now.
    if {"unlocks", "characters"} <= perms:
        save_craft_estimates(db, await fetch_account_for(api), traded, now)

    # 4. Snapshot.
    buy_price = {p["id"]: p["buys"]["unit_price"] for p in prices}
    stock_value = None
    if stock:
        stock_value = sum(net(buy_price[i], n) for i, n in stock.items() if buy_price.get(i))
    snap = {
        "time": now,
        "wallet": next((w["value"] for w in wallet if w["id"] == COIN), 0),
        "delivery_coins": delivery.get("coins", 0),
        "delivery_items": sum(i.get("count", 0) for i in delivery.get("items", [])),
        "listings_value": sum(listing_value(t["price"], t["quantity"]) for t in cur_sells),
        "buy_orders": sum(t["price"] * t["quantity"] for t in cur_buys),
        "stock_value": stock_value,
    }
    detail = {"history_in_api": {"buys": len(hist_buys), "sells": len(hist_sells)},
              "stock_items": len(stock)}
    db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?,?,?)",
               (*snap.values(), json.dumps(detail)))
    db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('account_synced_at', ?)", (str(now),))
    db.commit()
    return SyncResult(after["buy"] - before["buy"], after["sell"] - before["sell"],
                      len(cur_buys), len(cur_sells), snap, time.time() - start)


async def fetch_account_for(api: GW2Api):
    from .account import fetch_account
    return await fetch_account(api)


def craft_cost_now(solver, item_id: int) -> int | None:
    """Cheapest craft cost (copper each) over the recipes the solver may use. None if
    it can't be crafted or needs unbuyable (own) materials."""
    best = None
    for r in solver.recipes_for(item_id):
        if r.mystic_forge or not r.only_items or not solver.allowed(r):
            continue
        node = solver.craft(r)
        if node.ok and not node.needs_own:
            each = node.unit_cost
            best = each if best is None else min(best, each)
    return None if best is None else round(best)


def save_craft_estimates(db: sqlite3.Connection, acct, item_ids: set[int], now: int) -> int:
    from .pricing import Mode
    from .solver import build_solver
    solver = build_solver(db, Mode.PATIENT, recipe_allowed=acct.can_craft)
    rows = [(i, now, c) for i in item_ids if (c := craft_cost_now(solver, i)) is not None]
    db.executemany("INSERT OR REPLACE INTO craft_estimates VALUES (?,?,?)", rows)
    return len(rows)


def account_value(snap: dict) -> int:
    """Wallet + delivery box + open listings (after fee) + coins in buy orders (+ stock)."""
    return (snap["wallet"] + snap["delivery_coins"] + snap["listings_value"] + snap["buy_orders"]
            + (snap.get("stock_value") or 0))


def snapshots(db: sqlite3.Connection) -> list[dict]:
    ensure_schema(db)
    cols = ["time", "wallet", "delivery_coins", "delivery_items", "listings_value", "buy_orders", "stock_value"]
    return [dict(zip(cols, r)) for r in db.execute(f"SELECT {', '.join(cols)} FROM snapshots ORDER BY time")]
