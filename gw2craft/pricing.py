"""Trading Post fees, buy/sell prices per mode, and money formatting.

All money is in copper (int). 1 gold = 100 silver = 10,000 copper.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    PATIENT = "patient"  # buy with buy orders, sell with listings
    FAST = "fast"        # buy instantly from listings, sell instantly to buy orders


def _pct(price: int, percent: int) -> int:
    """price * percent / 100, rounded half up, using whole numbers only (no float errors)."""
    return (price * percent + 50) // 100


def listing_fee(total: int) -> int:
    """5% of the total sale price, minimum 1c.

    Taken when you list OR sell instantly. Not refunded if you cancel the listing.
    """
    return max(1, _pct(total, 5))


def exchange_fee(total: int) -> int:
    """10% of the total sale price, minimum 1c. Taken from the coins when it sells."""
    return max(1, _pct(total, 10))


def net(price: int, qty: int = 1) -> int:
    """What you actually get for selling `qty` items at `price` each, in one listing.

    Fees are charged on the total (price * qty), not per item, so selling a
    stack of cheap items loses less to rounding and the 1c minimums.
    """
    total = price * qty
    return total - listing_fee(total) - exchange_fee(total)


@dataclass(frozen=True)
class Quote:
    """TP prices for one item. None means there are no orders/listings on that side."""
    buy_price: int | None   # highest buy order
    sell_price: int | None  # lowest listing


def buy_cost(q: Quote, mode: Mode) -> int | None:
    """What one unit costs to buy, or None if you can't buy it on the TP right now."""
    if mode is Mode.FAST:
        return q.sell_price
    # Patient: outbid the top buy order by 1c, but never pay more than the lowest listing.
    if q.buy_price is None:
        return q.sell_price
    bid = q.buy_price + 1
    return min(bid, q.sell_price) if q.sell_price is not None else bid


def sell_price(q: Quote, mode: Mode) -> int | None:
    """The price you sell one unit at (before tax), or None if there's no way to price it."""
    if mode is Mode.FAST:
        return q.buy_price
    # Patient: undercut the lowest listing by 1c, but never go below the top buy order.
    if q.sell_price is None:
        return None
    ask = max(1, q.sell_price - 1)
    return max(ask, q.buy_price) if q.buy_price is not None else ask


# ---------- money formatting ----------

def fmt(copper: int | None) -> str:
    """12345 -> '1g 23s 45c'. Drops leading zero units: 45 -> '45c', 1200 -> '12s 0c'."""
    if copper is None:
        return "-"
    sign = "-" if copper < 0 else ""
    c = abs(copper)
    g, rest = divmod(c, 10_000)
    s, c = divmod(rest, 100)
    if g:
        return f"{sign}{g}g {s}s {c}c"
    if s:
        return f"{sign}{s}s {c}c"
    return f"{sign}{c}c"


_MONEY_RE = re.compile(r"^\s*(?:(\d+)\s*g)?\s*(?:(\d+)\s*s)?\s*(?:(\d+)\s*c?)?\s*$", re.IGNORECASE)


def parse_money(text: str) -> int:
    """'1g20s50c', '1g 20s', '50s', '75c' or plain '12345' (copper) -> copper."""
    m = _MONEY_RE.match(text)
    if not m or not any(m.groups()):
        raise ValueError(f"Can't read money value: {text!r}. Use e.g. 1g20s50c, 50s or 1234.")
    g, s, c = (int(x) if x else 0 for x in m.groups())
    return g * 10_000 + s * 100 + c
