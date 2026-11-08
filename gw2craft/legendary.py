"""Legendary crafting and selling.

- Which legendaries can be sold: from API data only (never a hard-coded list).
- Full report for one legendary: precursor buy vs craft, TP parts, own materials
  (with have/need), sell price after tax, profit with and without your own values,
  and how many competing listings there are.
Mystic Forge recipes come from data/mystic_forge.json (built from the GW2 Wiki).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from .account import Status
from .pricing import Mode, buy_cost, net, sell_price
from .profit import path_recipes
from .solver import BOUND_FLAGS, Node, Recipe, Solver

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


# ---------- full legendary report (step 8) ----------


@dataclass
class Listings:
    sell_listings: int      # how many separate sell listings (competing sellers)
    sell_qty: int           # items listed in total
    at_lowest: int          # items listed at the lowest price
    buy_orders: int


def parse_listings(data: dict) -> Listings:
    sells, buys = data.get("sells", []), data.get("buys", [])
    return Listings(
        sell_listings=sum(s.get("listings", 0) for s in sells),
        sell_qty=sum(s.get("quantity", 0) for s in sells),
        at_lowest=min(sells, key=lambda s: s["unit_price"])["quantity"] if sells else 0,
        buy_orders=sum(b.get("listings", 0) for b in buys),
    )


@dataclass
class LegendaryReport:
    item_id: int
    name: str
    mode: Mode
    recipe: Recipe
    node: Node
    precursor_id: int | None
    precursor_buy: int | None          # TP price for this mode
    precursor_craft: Node | None       # API recipe for the precursor, if any
    tp_parts: list[tuple[str, int, float, float]]  # (source, item_id, qty, unit cost)
    own: list[tuple[int, float, float]]            # (item_id, qty, your value each)
    sell: int | None                   # sell price for this mode
    net: int | None
    locked: list[tuple[Recipe, Status]]
    owned_parts: list[tuple[int, int, float]] = field(default_factory=list)  # crafted parts you own

    @property
    def tp_total(self) -> int:
        return round(sum(q * u for _, _, q, u in self.tp_parts))

    @property
    def own_value(self) -> int:
        return round(sum(q * v for _, q, v in self.own))

    @property
    def profit_own_free(self) -> int | None:
        """Profit if your own materials cost nothing. Not real gold profit."""
        return None if self.net is None else self.net - self.tp_total

    @property
    def profit_with_values(self) -> int | None:
        """Profit after taking off your own_materials.json values."""
        p = self.profit_own_free
        return None if p is None else p - self.own_value


def item_info(db: sqlite3.Connection) -> tuple[dict[int, str], dict[int, str]]:
    """(item_id -> type, item_id -> rarity) from the cache."""
    types, rarity = {}, {}
    for r in db.execute("SELECT id, type, rarity FROM items"):
        types[r[0]], rarity[r[0]] = r[1], r[2]
    return types, rarity


def legendary_recipes(solver: Solver, rarity: dict[int, str]) -> list[Recipe]:
    """Mystic Forge recipes whose output is a Legendary that can be sold on the TP."""
    out = []
    for recipes in solver.by_output.values():
        for rec in recipes:
            item = solver.items.get(rec.output_item_id)
            if (rec.mystic_forge and item and not item.bound and rec.output_item_id in solver.quotes
                    and rarity.get(rec.output_item_id) == "Legendary"):
                out.append(rec)
    return out


def _find_precursor(recipe: Recipe, types: dict[int, str]) -> int | None:
    for ing in recipe.ingredients:
        if types.get(ing.item_id) == "Weapon":
            return ing.item_id
    return None


def build_report(solver: Solver, recipe: Recipe, types: dict[int, str],
                 status_of: Callable[[Recipe], Status] | None = None,
                 owned: dict[int, int] | None = None) -> LegendaryReport:
    node = solver.craft(recipe)  # force the craft: buying the legendary isn't the question
    item = solver.items[recipe.output_item_id]
    quote = solver.quotes.get(recipe.output_item_id)
    sell = sell_price(quote, solver.mode) if quote else None

    pre_id = _find_precursor(recipe, types)
    pre_buy = pre_craft = None
    if pre_id is not None:
        q = solver.quotes.get(pre_id)
        pre_buy = buy_cost(q, solver.mode) if q else None
        api_recipes = [r for r in solver.recipes_for(pre_id) if not r.mystic_forge and r.only_items]
        if api_recipes:
            pre_craft = solver.craft(api_recipes[0])

    # If you already own a crafted part (e.g. a Gift), don't buy its ingredients again.
    owned = owned or {}
    owned_parts: list[tuple[int, int, float]] = []
    leaves: dict[tuple[str, int], float] = {}
    units: dict[tuple[str, int], float] = {}
    left = dict(owned)

    def walk(n: Node, amount: float) -> None:
        if n.source == "craft" and n.recipe is not None and n is not node:
            have = left.get(n.item_id, 0)
            if have > 0:
                use = min(have, amount)
                left[n.item_id] = have - use
                owned_parts.append((n.item_id, int(use), amount))
                amount -= use
                if amount <= 0:
                    return
        if n.source == "craft" and n.recipe is not None:
            crafts = amount / n.recipe.output_count
            for child, count in n.children:
                walk(child, crafts * count)
        else:
            key = (n.source, n.item_id)
            leaves[key] = leaves.get(key, 0.0) + amount
            units[key] = n.unit_cost or 0.0

    walk(node, recipe.output_count)
    tp_parts = [(src, iid, q, units[(src, iid)]) for (src, iid), q in leaves.items() if src in ("buy", "vendor")]
    tp_parts.sort(key=lambda x: -x[2] * x[3])
    own = [(iid, q, float(solver.own_values.get(iid, 0))) for (src, iid), q in leaves.items() if src == "own"]
    own.sort(key=lambda x: solver.items[x[0]].name if x[0] in solver.items else "")

    locked = []
    if status_of:
        locked = [(r, s) for r in path_recipes(node)
                  if not r.mystic_forge and not (s := status_of(r)).ready]
    return LegendaryReport(
        recipe.output_item_id, item.name, solver.mode, recipe, node, pre_id, pre_buy, pre_craft,
        tp_parts, own, sell, net(sell, recipe.output_count) if sell else None, locked, owned_parts)

