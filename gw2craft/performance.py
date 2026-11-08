"""Profit maths for the account dashboard. Every number says how exact it is.

- Cash flow (exact): net from sells (price*qty minus the 15% fees) minus coins spent
  on buys. Not counted: listing fees of listings you cancelled (the API doesn't show them).
- Open listings: age, my price vs the current lowest, status and a plain suggestion.
- Open buy orders: my price vs the current highest buy order.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .pricing import fmt, listing_fee, net
from .tracking import ensure_schema

OLD_DAYS = 7


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def now_utc() -> datetime:
    return datetime.now(UTC)


# ---------- cash flow ----------

@dataclass
class CashFlow:
    days: int
    sold_net: int        # coins received from sells, after fees
    spent: int           # coins paid for buys
    sells: int           # number of sell transactions
    buys: int

    @property
    def profit(self) -> int:
        return self.sold_net - self.spent


def cash_flow(db: sqlite3.Connection, days: int, now: datetime | None = None) -> CashFlow:
    ensure_schema(db)
    since = ((now or now_utc()) - timedelta(days=days)).replace(microsecond=0).isoformat()
    sold = spent = n_sell = n_buy = 0
    for kind, price, qty in db.execute(
            "SELECT kind, price, quantity FROM tp_history WHERE purchased >= ?", (since,)):
        if kind == "sell":
            sold += net(price, qty)
            n_sell += 1
        else:
            spent += price * qty
            n_buy += 1
    return CashFlow(days, sold, spent, n_sell, n_buy)


def daily_cash_flow(db: sqlite3.Connection, days: int = 90, now: datetime | None = None) -> list[dict]:
    """[{date, sold_net, spent, profit}] for each day with activity (UTC days)."""
    ensure_schema(db)
    since = ((now or now_utc()) - timedelta(days=days)).replace(microsecond=0).isoformat()
    by_day: dict[str, list[int]] = {}
    for kind, price, qty, purchased in db.execute(
            "SELECT kind, price, quantity, purchased FROM tp_history WHERE purchased >= ?", (since,)):
        d = by_day.setdefault(purchased[:10], [0, 0])
        if kind == "sell":
            d[0] += net(price, qty)
        else:
            d[1] += price * qty
    return [{"date": k, "sold_net": v[0], "spent": v[1], "profit": v[0] - v[1]}
            for k, v in sorted(by_day.items())]


# ---------- open listings and orders ----------

@dataclass
class OpenListing:
    id: int
    item_id: int
    name: str
    qty: int
    my_price: int
    lowest: int | None       # current lowest listing on the TP (may be mine)
    age_days: float
    status: str
    suggestion: str


def _name(db: sqlite3.Connection, item_id: int) -> str:
    r = db.execute("SELECT name FROM items WHERE id = ?", (item_id,)).fetchone()
    return r[0] if r else f"item {item_id}"


def listing_status(my_price: int, lowest: int | None, qty: int, age_days: float) -> tuple[str, str]:
    """(status, suggestion) in plain words."""
    old = age_days > OLD_DAYS
    if lowest is None or my_price <= lowest:
        status = "✅ lowest" + (" · ⏳ old" if old else "")
        suggestion = "wait (you are the lowest price)" if not old else \
            f"wait; it's been {age_days:.0f} days — few buyers, or check the price trend"
        return status, suggestion
    gap = my_price - lowest
    new_price = lowest - 1
    fee = listing_fee(new_price * qty)
    status = f"⚠ undercut by {fmt(gap)}" + (" · ⏳ old" if old else "")
    # Relisting gives up (gap+1c) per item AND pays a new 5% fee.
    give_up = (gap + 1) * qty
    if fee > give_up:
        suggestion = (f"wait: the gap is small ({fmt(give_up)} in total) and relisting at "
                      f"{fmt(new_price)} costs another 5% fee ({fmt(fee)})")
    else:
        suggestion = (f"undercut by 1c: relist at {fmt(new_price)} — costs another 5% listing fee "
                      f"({fmt(fee)}), or wait")
    return status, suggestion


def open_listings(db: sqlite3.Connection, now: datetime | None = None) -> list[OpenListing]:
    ensure_schema(db)
    now = now or now_utc()
    out = []
    for lid, item_id, price, qty, created in db.execute(
            "SELECT id, item_id, price, quantity, created FROM tp_current WHERE kind='sell' ORDER BY created"):
        row = db.execute("SELECT sell_price FROM prices WHERE item_id=?", (item_id,)).fetchone()
        lowest = row[0] if row else None
        age = (now - parse_time(created)).total_seconds() / 86400
        status, suggestion = listing_status(price, lowest, qty, age)
        out.append(OpenListing(lid, item_id, _name(db, item_id), qty, price, lowest, age, status, suggestion))
    return out


@dataclass
class OpenOrder:
    id: int
    item_id: int
    name: str
    qty: int
    my_price: int
    highest: int | None      # current highest buy order (may be mine)
    age_days: float
    status: str


def open_orders(db: sqlite3.Connection, now: datetime | None = None) -> list[OpenOrder]:
    ensure_schema(db)
    now = now or now_utc()
    out = []
    for oid, item_id, price, qty, created in db.execute(
            "SELECT id, item_id, price, quantity, created FROM tp_current WHERE kind='buy' ORDER BY created"):
        row = db.execute("SELECT buy_price FROM prices WHERE item_id=?", (item_id,)).fetchone()
        highest = row[0] if row else None
        if highest is None or price >= highest:
            status = "✅ top"
        else:
            status = f"⚠ outbid by {fmt(highest - price)}"
        age = (now - parse_time(created)).total_seconds() / 86400
        out.append(OpenOrder(oid, item_id, _name(db, item_id), qty, price, highest, age, status))
    return out


# ---------- per-item profit (FIFO) ----------
#
# How each unit sold gets its cost, in this order:
#   1. "exact"     - matched to an earlier TP buy of the same item, first bought = first sold (FIFO).
#   2. "logged"    - matched to a craft run you logged (gw2craft track craft), with its real cost.
#   3. "estimated" - no buy or logged craft, but you can craft it: cost = solver craft cost
#                    (patient prices) saved at the sync nearest the sale.
#   4. "loot"      - none of the above (drops, gathering, bought before your history): revenue only.

BASES = ("exact", "logged", "estimated", "loot")


@dataclass
class ItemResult:
    item_id: int
    name: str
    sold: int = 0
    revenue: float = 0.0        # after the 15% fees
    cost: float = 0.0
    days_to_sell_sum: float = 0.0
    sells: int = 0              # sell transactions (for the average)
    held: int = 0               # units bought/crafted but not sold yet
    basis: dict = None          # units sold per basis

    def __post_init__(self):
        if self.basis is None:
            self.basis = {b: 0 for b in BASES}

    @property
    def profit(self) -> float:
        return self.revenue - self.cost

    @property
    def roi(self) -> float | None:
        return self.profit / self.cost if self.cost > 0 else None

    @property
    def avg_days_to_sell(self) -> float | None:
        return self.days_to_sell_sum / self.sells if self.sells else None

    @property
    def loot_revenue(self) -> float:
        """Revenue from units with no known cost (share of revenue, by units)."""
        return self.revenue * self.basis["loot"] / self.sold if self.sold else 0.0

    @property
    def known_units(self) -> int:
        return self.sold - self.basis["loot"]

    @property
    def trade_profit(self) -> float:
        """Profit on units with a known cost only (exact + logged + estimated)."""
        return self.profit - self.loot_revenue

    @property
    def label(self) -> str:
        """'exact', 'estimated', 'loot', or a mix like 'exact + estimated'."""
        used = [b for b in BASES if self.basis[b]]
        return " + ".join(used) if used else "-"


def craft_estimate_lookup(db: sqlite3.Connection):
    """item_id, unix time -> estimated craft cost each (nearest saved estimate), or None."""
    ensure_schema(db)
    table: dict[int, list[tuple[int, int]]] = {}
    for item_id, t, cost in db.execute("SELECT item_id, time, cost_each FROM craft_estimates ORDER BY time"):
        table.setdefault(item_id, []).append((t, cost))

    def lookup(item_id: int, when: int) -> int | None:
        rows = table.get(item_id)
        if not rows:
            return None
        before = [c for t, c in rows if t <= when]
        return before[-1] if before else rows[0][1]  # else: the earliest one after the sale
    return lookup


def item_results(db: sqlite3.Connection, days: int | None = None, now: datetime | None = None,
                 estimate=None) -> list[ItemResult]:
    """Per-item results for sells in the last `days` days (None = all local history).

    FIFO runs over the whole history, so a sale in the period can match an older buy.
    """
    ensure_schema(db)
    now = now or now_utc()
    since = (now - timedelta(days=days)).timestamp() if days else None
    estimate = estimate or craft_estimate_lookup(db)

    events: dict[int, list[tuple]] = {}
    for kind, item_id, price, qty, created, purchased in db.execute(
            "SELECT kind, item_id, price, quantity, created, purchased FROM tp_history"):
        t = parse_time(purchased).timestamp()
        events.setdefault(item_id, []).append((t, kind, price, qty, parse_time(created).timestamp()))
    for item_id, t, qty, cost_each in db.execute("SELECT item_id, time, quantity, cost_each FROM craft_runs"):
        events.setdefault(item_id, []).append((float(t), "craft", cost_each, qty, float(t)))

    results = []
    for item_id, evs in events.items():
        evs.sort(key=lambda e: (e[0], e[1] != "buy"))  # same moment: buy before sell
        lots: list[list] = []                           # [qty_left, unit_cost, basis]
        res = ItemResult(item_id, _name(db, item_id))
        for t, kind, price, qty, created in evs:
            if kind in ("buy", "craft"):
                lots.append([qty, float(price), "exact" if kind == "buy" else "logged"])
                continue
            in_period = since is None or t >= since
            unit_rev = net(price, qty) / qty
            left = qty
            while left and lots:
                take = min(left, lots[0][0])
                if in_period:
                    res.cost += take * lots[0][1]
                    res.basis[lots[0][2]] += take
                lots[0][0] -= take
                left -= take
                if lots[0][0] == 0:
                    lots.pop(0)
            if left and in_period:
                est = estimate(item_id, int(t))
                if est is not None:
                    res.cost += left * est
                    res.basis["estimated"] += left
                else:
                    res.basis["loot"] += left
            if in_period:
                res.sold += qty
                res.revenue += unit_rev * qty
                res.sells += 1
                res.days_to_sell_sum += (t - created) / 86400
        res.held = sum(lot[0] for lot in lots)
        if res.sold:
            results.append(res)
    results.sort(key=lambda r: r.profit, reverse=True)
    return results
