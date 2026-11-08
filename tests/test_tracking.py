import sqlite3
from datetime import UTC, datetime

from gw2craft.performance import cash_flow, daily_cash_flow, listing_status, open_listings, open_orders
from gw2craft.sync import SCHEMA as CACHE_SCHEMA
from gw2craft.tracking import account_value, ensure_schema, listing_value

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def make_db():
    db = sqlite3.connect(":memory:")
    db.executescript(CACHE_SCHEMA)
    ensure_schema(db)
    db.executemany("INSERT INTO tp_history VALUES (?,?,?,?,?,?,?,?)", [
        (1, "buy", 10, 100, 10, "2026-11-01T00:00:00+00:00", "2026-11-01T01:00:00+00:00", 0),   # -1000
        (2, "sell", 10, 200, 5, "2026-11-05T00:00:00+00:00", "2026-11-06T00:00:00+00:00", 0),   # net(200,5)=850
        (3, "sell", 11, 1000, 1, "2026-09-01T00:00:00+00:00", "2026-09-02T00:00:00+00:00", 0),  # 60+ days ago
    ])
    db.executemany("INSERT INTO tp_current VALUES (?,?,?,?,?,?)", [
        (7, "sell", 10, 210, 3, "2026-10-20T00:00:00+00:00"),
        (8, "buy", 11, 900, 1, "2026-11-08T00:00:00+00:00"),
    ])
    db.executemany("INSERT INTO items (id, name, flags) VALUES (?,?,?)", [(10, "Thing", "[]"), (11, "Big", "[]")])
    db.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?)", [(10, 150, 1, 205, 1, 0), (11, 950, 1, 1200, 1, 0)])
    return db


def test_cash_flow_periods():
    db = make_db()
    cf = cash_flow(db, 7, NOW)
    assert (cf.sold_net, cf.spent, cf.profit) == (850, 0, 850)    # buy on Nov 1 is 7.5 days ago
    cf30 = cash_flow(db, 30, NOW)
    assert (cf30.sold_net, cf30.spent, cf30.profit) == (850, 1000, -150)
    assert cash_flow(db, 90, NOW).sold_net == 850 + 850             # net(1000) = 850
    days = daily_cash_flow(db, 30, NOW)
    assert [d["date"] for d in days] == ["2026-11-01", "2026-11-06"]


def test_open_listings_and_orders():
    db = make_db()
    (lst,) = open_listings(db, NOW)
    assert lst.lowest == 205 and lst.status.startswith("⚠ undercut by 5c") and "old" in lst.status
    (order,) = open_orders(db, NOW)
    assert order.status == "⚠ outbid by 50c"


def test_listing_suggestions():
    assert listing_status(100, 100, 1, 1)[0] == "✅ lowest"
    # Big item, tiny gap: relisting fee is bigger than what you'd give up -> wait.
    s, sug = listing_status(170_000, 169_896, 1, 5)
    assert sug.startswith("wait: the gap is small")
    # Big gap on a stack: relisting is worth it.
    s, sug = listing_status(1009, 759, 20, 3)
    assert sug.startswith("undercut by 1c: relist at 7s 58c")


def test_listing_value_and_account_value():
    assert listing_value(1000, 2) == 2000 - 200           # only the 10% exchange fee is left
    snap = {"wallet": 1, "delivery_coins": 2, "listings_value": 3, "buy_orders": 4, "stock_value": None}
    assert account_value(snap) == 10
    assert account_value({**snap, "stock_value": 5}) == 15
