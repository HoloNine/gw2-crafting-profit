from gw2craft.pricing import Mode, Quote
from gw2craft.profit import find_profits
from gw2craft.solver import Ingredient, Item, Recipe, Solver

ORE, INGOT, BOUND, TROLL, STACK = 1, 2, 3, 4, 5


def rec(rid, out, ings, count=1):
    return Recipe(rid, out, count, ("Weaponsmith",), 400, tuple(Ingredient(i, c) for i, c in ings))


RECIPES = [
    rec(1, INGOT, [(ORE, 2)]),          # cost 2*10 = 20
    rec(2, BOUND, [(ORE, 1)]),          # output can't be sold
    rec(3, TROLL, [(ORE, 1)]),          # troll listing far above buy orders
    rec(4, STACK, [(ORE, 1)], count=5),  # makes 5
]
ITEMS = {
    INGOT: Item(INGOT, "Ingot"), ORE: Item(ORE, "Ore"),
    BOUND: Item(BOUND, "Bound", frozenset({"AccountBound"})),
    TROLL: Item(TROLL, "Troll"), STACK: Item(STACK, "Stack"),
}
QUOTES = {
    ORE: Quote(9, 10), INGOT: Quote(30, 41), BOUND: Quote(100, 200),
    TROLL: Quote(5, 10_000), STACK: Quote(4, 5),
}


def run(mode, **kw):
    s = Solver(RECIPES, ITEMS, QUOTES, mode)
    return {r.item_id: r for r in find_profits(s, **kw)}


def test_patient_profit_and_roi():
    rows = run(Mode.PATIENT)
    ingot = rows[INGOT]
    assert ingot.craft_cost == 20           # 2 ore at 9+1
    assert ingot.sell_price == 40           # 41 - 1
    assert ingot.net == 34                  # 40 - 2 - 4
    assert ingot.profit == 14
    assert round(ingot.roi, 2) == 0.70


def test_bound_output_skipped():
    assert BOUND not in run(Mode.PATIENT)


def test_troll_listing_skipped_in_patient_only_when_spread_on():
    assert TROLL not in run(Mode.PATIENT)
    assert TROLL in run(Mode.PATIENT, max_spread=None)


def test_multi_output_taxed_as_one_stack():
    stack = run(Mode.FAST)[STACK]
    assert stack.craft_cost == 10
    assert stack.net == 20 - 1 - 2          # 5 x 4c, fees on the 20c total


def test_filters():
    assert INGOT not in run(Mode.PATIENT, min_roi=0.8)   # ingot ROI is 70%
    assert INGOT not in run(Mode.PATIENT, max_cost=19)
    assert INGOT not in run(Mode.PATIENT, min_profit=15)


def test_locked_sub_recipes_are_counted():
    from gw2craft.account import Status
    # Sword needs 3 ingots; the ingot recipe (id 1) is crafted in the tree (2 ore = 20 < 30).
    sword = rec(9, 6, [(INGOT, 3)])
    items = {**ITEMS, 6: Item(6, "Sword")}
    quotes = {**QUOTES, 6: Quote(150, 200)}
    s = Solver([*RECIPES, sword], items, quotes, Mode.PATIENT)
    status = lambda r: Status("learned") if r.id == 9 else Status("discover")  # noqa: E731
    row = {r.item_id: r for r in find_profits(s, status_of=status)}[6]
    assert [r.id for r, _ in row.locked_sub] == [1]
    assert row.unlock_text == "learned +1 sub"
