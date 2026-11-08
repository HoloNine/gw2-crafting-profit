"""Legendary crafting and selling.

For now: find which legendaries can be sold, using only the API data
(never a hard-coded list). The full cost tree and report come in step 7.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .solver import BOUND_FLAGS

# Item types that have Legendary rarity but are materials/trophies, not legendary gear.
NOT_GEAR = {"Consumable", "Trophy", "CraftingMaterial"}


@dataclass(frozen=True)
class Legendary:
    id: int
    name: str
    type: str
    buy_price: int | None   # highest buy order
    sell_price: int | None  # lowest listing
    sell_qty: int


def sellable_legendaries(db: sqlite3.Connection) -> list[Legendary]:
    """Legendary items that are on the TP and not account/soul bound."""
    rows = db.execute("""
        SELECT i.id, i.name, i.type, i.flags, p.buy_price, p.sell_price, p.sell_qty
        FROM items i JOIN prices p ON p.item_id = i.id
        WHERE i.rarity = 'Legendary'
        ORDER BY i.type, i.name
    """)
    out = []
    for r in rows:
        if set(json.loads(r["flags"])) & BOUND_FLAGS or r["type"] in NOT_GEAR:
            continue
        out.append(Legendary(r["id"], r["name"], r["type"], r["buy_price"],
                             r["sell_price"], r["sell_qty"] or 0))
    return out
