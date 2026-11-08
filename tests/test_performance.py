"""FIFO matching, fees, and exact / logged / estimated / loot labels."""
import sqlite3
from datetime import UTC, datetime

import pytest

from gw2craft.performance import item_results
from gw2craft.pricing import net
from gw2craft.sync import SCHEMA as CACHE_SCHEMA
from gw2craft.tracking import ensure_schema

NOW = datetime(2026, 11, 30, tzinfo=UTC)
FLIP, CRAFTED, LOOT, LOGGED = 1, 2, 3, 4


def ts(day: int, hour: int = 0) -> str:
    return datetime(2026, 11, day, hour, tzinfo=UTC).isoformat()


def db_with(history, crafts=()):
    db = sqlite3.connect(":memory:")
    db.executescript(CACHE_SCHEMA)
    ensure_schema(db)
    db.executemany("INSERT INTO items (id, name, flags) VALUES (?,?,'[]')",
                   [(FLIP, "Flip"), (CRAFTED, "Crafted"), (LOOT, "Loot"), (LOGGED, "Logged")])
    db.executemany("INSERT INTO tp_history VALUES (?,?,?,?,?,?,?,0)",
                   [(i, *h) for i, h in enumerate(history, start=1)])
    db.executemany("INSERT INTO craft_runs (time, item_id, quantity, cost_each) VALUES (?,?,?,?)", crafts)
    return db


def by_id(rows):
    return {r.item_id: r for r in rows}


def test_fifo_uses_oldest_buy_first():
    db = db_with([
        ("buy", FLIP, 100, 5, ts(1), ts(1, 1)),     # 5 @ 1s
        ("buy", FLIP, 200, 5, ts(2), ts(2, 1)),     # 5 @ 2s
        ("sell", FLIP, 300, 7, ts(3), ts(5)),       # sell 7 @ 3s, listed for 2 days
    ])
    r = by_id(item_results(db, now=NOW, estimate=lambda i, t: None))[FLIP]
    assert r.sold == 7
    assert r.cost == 5 * 100 + 2 * 200               # 5 from the first lot, 2 from the second
    assert r.revenue == pytest.approx(net(300, 7))
    assert r.profit == pytest.approx(net(300, 7) - 900)
    assert r.held == 3                               # 3 of the 2s lot still held
    assert r.label == "exact"
    assert r.avg_days_to_sell == pytest.approx(2.0)


def test_sell_before_any_buy_is_loot_not_a_negative_cost():
    db = db_with([
        ("sell", LOOT, 50, 10, ts(1), ts(1, 2)),
        ("buy", LOOT, 40, 10, ts(2), ts(2, 1)),      # bought later: can't be what was sold before
    ])
    r = by_id(item_results(db, now=NOW, estimate=lambda i, t: None))[LOOT]
    assert r.cost == 0 and r.basis["loot"] == 10 and r.label == "loot"
    assert r.roi is None and r.held == 10


def test_estimated_craft_cost_when_no_buy():
    db = db_with([("sell", CRAFTED, 1000, 2, ts(1), ts(2))])
    r = by_id(item_results(db, now=NOW, estimate=lambda i, t: 600 if i == CRAFTED else None))[CRAFTED]
    assert r.cost == 1200 and r.label == "estimated"
    assert r.profit == pytest.approx(net(1000, 2) - 1200)


def test_logged_craft_run_beats_estimate_and_mixes_with_buys():
    t_craft = int(datetime(2026, 11, 3, tzinfo=UTC).timestamp())
    db = db_with([
        ("buy", LOGGED, 500, 1, ts(1), ts(1, 1)),
        ("sell", LOGGED, 900, 3, ts(4), ts(5)),
    ], crafts=[(t_craft, LOGGED, 1, 300)])
    r = by_id(item_results(db, now=NOW, estimate=lambda i, t: 700))[LOGGED]
    # 1 matched to the buy (5s), 1 to the logged craft (3s), the 3rd estimated (7s).
    assert r.cost == 500 + 300 + 700
    assert r.basis == {"exact": 1, "logged": 1, "estimated": 1, "loot": 0}
    assert r.label == "exact + logged + estimated"


def test_period_filter_keeps_fifo_over_full_history():
    db = db_with([
        ("buy", FLIP, 100, 2, ts(1), ts(1, 1)),
        ("sell", FLIP, 300, 1, ts(2), ts(2, 5)),    # old sale: uses the first unit
        ("sell", FLIP, 400, 1, ts(28), ts(29)),     # recent sale: uses the second unit
    ])
    r = by_id(item_results(db, days=7, now=NOW, estimate=lambda i, t: None))[FLIP]
    assert r.sold == 1 and r.cost == 100 and r.revenue == pytest.approx(net(400, 1))


def test_results_sorted_by_profit():
    db = db_with([
        ("buy", FLIP, 100, 1, ts(1), ts(1, 1)), ("sell", FLIP, 1000, 1, ts(2), ts(3)),
        ("buy", LOOT, 1000, 1, ts(1), ts(1, 1)), ("sell", LOOT, 100, 1, ts(2), ts(3)),
    ])
    assert [r.item_id for r in item_results(db, now=NOW, estimate=lambda i, t: None)] == [FLIP, LOOT]


def test_loot_share_is_split_from_trade_profit():
    db = db_with([
        ("buy", FLIP, 100, 1, ts(1), ts(1, 1)),
        ("sell", FLIP, 1000, 2, ts(2), ts(3)),      # 1 matched to the buy, 1 is loot
    ])
    r = by_id(item_results(db, now=NOW, estimate=lambda i, t: None))[FLIP]
    assert r.known_units == 1 and r.basis["loot"] == 1
    assert r.loot_revenue == pytest.approx(net(1000, 2) / 2)
    assert r.trade_profit == pytest.approx(net(1000, 2) / 2 - 100)
