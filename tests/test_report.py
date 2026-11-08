from rich.console import Console

from gw2craft.pricing import Mode, Quote
from gw2craft.report import craft_tree, leaf_prices, qty_text, shopping_table
from gw2craft.solver import Ingredient, Item, Recipe, Solver, shopping_list

ORE, INGOT, SWORD = 1, 2, 3
RECIPES = [Recipe(10, INGOT, 1, ("Weaponsmith",), 0, (Ingredient(ORE, 2),)),
           Recipe(11, SWORD, 1, ("Weaponsmith",), 0, (Ingredient(INGOT, 3),))]
NAMES = {ORE: "Ore", INGOT: "Ingot", SWORD: "Sword"}
ITEMS = {i: Item(i, n) for i, n in NAMES.items()}
QUOTES = {ORE: Quote(8, 10), INGOT: Quote(25, 30), SWORD: Quote(100, 120)}


def render(x) -> str:
    c = Console(width=120, record=True)
    c.print(x)
    return c.export_text()


def test_qty_text():
    assert qty_text(3.0) == "3" and qty_text(0.4) == "0.40"


def test_tree_and_shopping_list_for_two_crafts():
    s = Solver(RECIPES, ITEMS, QUOTES, Mode.FAST)
    node = s.craft(RECIPES[1])
    tree = render(craft_tree(node, 2, NAMES, "Tree"))
    assert "2 × Sword — craft (Wpn 0) 1s 20c" in tree
    assert "6 × Ingot — craft" in tree and "12 × Ore — buy on TP @ 10c = 1s 20c" in tree
    shop = render(shopping_table(shopping_list(node, 2), leaf_prices(node), NAMES, "Shop"))
    assert "Ore" in shop and "12" in shop and "1s 20c" in shop
