"""Work out profit for every normal recipe whose output can be sold on the TP.

Profit per craft = net(sell price, items made) - craft cost
ROI              = profit / craft cost

Patient mode sells at the lowest listing. If that listing is far above what
buyers pay (the "spread"), it is probably not a real price, so we skip it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .account import Status
from .pricing import Mode, Quote, net, sell_price
from .solver import Node, Recipe, Solver

CHECK_ROI = 5.0  # above 500% ROI, flag the row: the price may be odd


@dataclass
class ProfitRow:
    recipe: Recipe
    item_id: int
    name: str
    mode: Mode
    craft_cost: int      # copper, for one craft (all items it makes)
    sell_price: int      # copper, per item, before tax
    net: int             # copper, after tax, for all items made by one craft
    profit: int
    roi: float           # 0.12 = 12%
    node: Node
    unlock: Status | None = None                 # status of the recipe itself
    locked_sub: list[tuple[Recipe, Status]] = field(default_factory=list)  # sub-recipes not learned

    @property
    def unlock_text(self) -> str:
        if self.unlock is None:
            return ""
        t = self.unlock.text()
        if self.locked_sub:
            t += f" +{len(self.locked_sub)} sub"
        return t

    @property
    def flags(self) -> list[str]:
        f = []
        if self.node.timegated:
            f.append("timegated")
        if self.node.needs_own:
            f.append("own mats")
        if self.node.uses_vendor:
            f.append("vendor")
        if self.roi > CHECK_ROI:
            f.append("check")
        return f


def trusted_listing(quote: Quote, max_spread: float) -> bool:
    """Is the lowest listing a believable sell price? Needs buy orders, and
    the listing no more than `max_spread` times the top buy order."""
    if quote.buy_price is None or quote.sell_price is None:
        return False
    return quote.sell_price <= quote.buy_price * max_spread


def path_recipes(node: Node) -> list[Recipe]:
    """Every recipe crafted below the top of this tree (each once). These must be learned too."""
    seen: dict[int, Recipe] = {}

    def walk(n: Node) -> None:
        for child, _ in n.children:
            if child.source == "craft" and child.recipe is not None:
                seen.setdefault(child.recipe.id, child.recipe)
                walk(child)

    walk(node)
    return list(seen.values())


def evaluate(solver: Solver, recipe: Recipe,
             status_of: Callable[[Recipe], Status] | None = None) -> ProfitRow | None:
    """Profit for one recipe, with no filters (profit may be negative).

    None if the output can't be sold on the TP, or the craft can't be priced.
    """
    item = solver.items.get(recipe.output_item_id)
    quote = solver.quotes.get(recipe.output_item_id)
    if item is None or quote is None or item.bound:
        return None
    price = sell_price(quote, solver.mode)
    if price is None:
        return None
    node = solver.craft(recipe)
    if not node.ok:
        return None
    cost = round(node.unit_cost * recipe.output_count)
    income = net(price, recipe.output_count)
    profit = income - cost
    roi = profit / cost if cost > 0 else 0.0
    row = ProfitRow(recipe, item.id, item.name, solver.mode, cost, price, income, profit, roi, node)
    if status_of:
        row.unlock = status_of(recipe)
        row.locked_sub = [(r, s) for r in path_recipes(node)
                          if not (s := status_of(r)).ready and r.id != recipe.id]
    return row


def find_profits(
    solver: Solver,
    min_profit: int = 1,
    min_roi: float = 0.0,
    max_cost: int | None = None,
    include_own: bool = False,
    recipe_filter: Callable[[Recipe], bool] | None = None,
    max_spread: float | None = 2.0,
    status_of: Callable[[Recipe], Status] | None = None,
) -> list[ProfitRow]:
    """Profitable recipes, best profit first.

    status_of: if given, each row is tagged with its unlock status and any
    locked sub-recipes in its craft tree (for --potential).
    """
    rows: list[ProfitRow] = []
    for recipe in solver.recipes:
        if not recipe.only_items:
            continue
        if recipe_filter and not recipe_filter(recipe):
            continue
        if not solver.allowed(recipe):
            continue
        quote = solver.quotes.get(recipe.output_item_id)
        if (solver.mode is Mode.PATIENT and max_spread and quote is not None
                and not trusted_listing(quote, max_spread)):
            continue
        row = evaluate(solver, recipe, status_of)
        if row is None:
            continue
        if row.node.needs_own and not include_own:
            continue
        if row.craft_cost <= 0:
            continue  # nothing priced to compare against (all own mats)
        if max_cost is not None and row.craft_cost > max_cost:
            continue
        if row.profit < min_profit or row.roi < min_roi:
            continue
        rows.append(row)
    rows.sort(key=lambda r: r.profit, reverse=True)
    return rows
