"""Work out profit for every normal recipe whose output can be sold on the TP.

Profit per craft = net(sell price, items made) - craft cost
ROI              = profit / craft cost

Patient mode sells at the lowest listing. If that listing is far above what
buyers pay (the "spread"), it is probably not a real price, so we skip it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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
    extra_flags: tuple[str, ...] = ()

    @property
    def flags(self) -> list[str]:
        f = list(self.extra_flags)
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


def find_profits(
    solver: Solver,
    min_profit: int = 1,
    min_roi: float = 0.0,
    max_cost: int | None = None,
    include_own: bool = False,
    recipe_filter: Callable[[Recipe], bool] | None = None,
    max_spread: float | None = 2.0,
) -> list[ProfitRow]:
    """Profitable recipes, best profit first."""
    rows: list[ProfitRow] = []
    for recipe in solver.recipes:
        if not recipe.only_items:
            continue
        if recipe_filter and not recipe_filter(recipe):
            continue
        if not solver.allowed(recipe):
            continue
        item = solver.items.get(recipe.output_item_id)
        quote = solver.quotes.get(recipe.output_item_id)
        if item is None or quote is None or item.bound:
            continue  # output can't be sold on the TP
        price = sell_price(quote, solver.mode)
        if price is None:
            continue
        if solver.mode is Mode.PATIENT and max_spread and not trusted_listing(quote, max_spread):
            continue

        node = solver.craft(recipe)
        if not node.ok or (node.needs_own and not include_own):
            continue
        cost = round(node.unit_cost * recipe.output_count)
        if cost <= 0:
            continue  # nothing priced to compare against (all own mats)
        if max_cost is not None and cost > max_cost:
            continue

        income = net(price, recipe.output_count)
        profit = income - cost
        roi = profit / cost
        if profit < min_profit or roi < min_roi:
            continue
        rows.append(ProfitRow(recipe, item.id, item.name, solver.mode, cost, price,
                              income, profit, roi, node))
    rows.sort(key=lambda r: r.profit, reverse=True)
    return rows
