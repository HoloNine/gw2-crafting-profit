"""Solver tests on a tiny fake recipe graph."""

import pytest

from gw2craft.pricing import Mode, Quote
from gw2craft.solver import Ingredient, Item, Recipe, Solver, shopping_list

ORE, INGOT, THREAD, SWORD = 1, 2, 3, 4
A, B = 10, 11
LUMP, PLANK = 20, 21
BOUND_GEM, RING = 30, 31
RARE = 40


def rec(rid, out, ings, count=1, only_items=True):
    return Recipe(rid, out, count, ("Weaponsmith",), 0,
                  tuple(Ingredient(i, c) for i, c in ings), only_items)


RECIPES = [
    rec(100, INGOT, [(ORE, 2)]),                   # 2 ore -> 1 ingot
    rec(101, SWORD, [(INGOT, 3), (THREAD, 1)]),
    rec(102, A, [(B, 1)]),                         # loop: A needs B ...
    rec(103, B, [(A, 1)]),                         # ... and B needs A
    rec(104, PLANK, [(LUMP, 1)], count=5),         # 1 lump -> 5 planks
    rec(105, RING, [(BOUND_GEM, 1), (INGOT, 1)]),
    rec(106, RARE, [(ORE, 1)], only_items=False),  # uses a currency: must be skipped
]

ITEMS = {
    BOUND_GEM: Item(BOUND_GEM, "Bound Gem", frozenset({"AccountBound"})),
}

# Fast mode buys at sell_price, so only sell_price matters for these tests.
QUOTES = {
    ORE: Quote(8, 10),
    INGOT: Quote(25, 30),
    THREAD: Quote(9, 10),
    A: Quote(90, 100),
    LUMP: Quote(40, 50),
    PLANK: Quote(15, 20),
    RING: Quote(400, 500),
}


def make(**kw):
    defaults = dict(recipes=RECIPES, items=ITEMS, quotes=QUOTES, mode=Mode.FAST,
                    vendor_prices={THREAD: 8})
    defaults.update(kw)
    return Solver(**defaults)


def test_craft_beats_buy():
    node = make().cost(INGOT)
    assert node.source == "craft"
    assert node.unit_cost == 20  # 2 ore * 10 < 30 on TP


def test_vendor_beats_tp():
    node = make().cost(THREAD)
    assert node.source == "vendor"
    assert node.unit_cost == 8
    assert node.uses_vendor


def test_nested_craft_and_flags():
    node = make().cost(SWORD)
    assert node.source == "craft"
    assert node.unit_cost == 3 * 20 + 8
    assert node.uses_vendor and not node.needs_own


def test_recipe_not_allowed_falls_back_to_buy():
    s = make(recipe_allowed=lambda r: r.id != 100)
    assert s.cost(INGOT).source == "buy"
    assert s.cost(SWORD).unit_cost == 3 * 30 + 8


def test_output_count_spreads_cost():
    node = make().cost(PLANK)
    assert node.source == "craft"
    assert node.unit_cost == 10  # 50 / 5 planks, cheaper than 20 on TP


@pytest.mark.parametrize("first", [A, B])
def test_cycle_is_cut_and_answer_does_not_depend_on_start(first):
    s = make()
    s.cost(first)
    a, b = s.cost(A), s.cost(B)
    assert a.source == "buy" and a.unit_cost == 100
    assert b.source == "craft" and b.unit_cost == 100  # B only via A


@pytest.mark.parametrize("first", [A, B])
def test_closed_loop_with_no_way_in_needs_own_materials(first):
    # Neither A nor B can be bought: you can only use them if you already have one.
    s = make(quotes={})
    s.cost(first)
    assert s.cost(A).needs_own
    assert s.cost(B).needs_own


def test_bound_item_becomes_own_materials():
    node = make().cost(BOUND_GEM)
    assert node.source == "own" and node.needs_own and node.unit_cost == 0


def test_priced_route_preferred_over_own_mats():
    # Ring can be bought (500) or crafted from a bound gem + ingot (20, "free" gem).
    node = make().cost(RING)
    assert node.source == "buy" and node.unit_cost == 500


def test_craft_forces_recipe_even_if_buying_is_cheaper():
    s = make()
    node = s.craft(RECIPES[5])  # ring recipe
    assert node.source == "craft" and node.needs_own
    assert node.unit_cost == 20


def test_currency_recipes_are_skipped():
    node = make().cost(RARE)
    assert node.source == "own"  # no TP, no vendor, recipe skipped


def test_timegated_flag_bubbles_up():
    s = make(timegated={INGOT})
    assert s.cost(SWORD).timegated


def test_shopping_list():
    s = make()
    sword = s.cost(SWORD)
    assert shopping_list(sword) == {("buy", ORE): 6, ("vendor", THREAD): 1}
    assert shopping_list(sword, qty=2) == {("buy", ORE): 12, ("vendor", THREAD): 2}


def test_shopping_list_fractional_for_multi_output():
    plank = make().cost(PLANK)
    assert shopping_list(plank, qty=2) == {("buy", LUMP): pytest.approx(0.4)}


# ---------- Mystic Forge + own material values ----------

GIFT, PRECURSOR, LEGEND, CLOVER = 50, 51, 52, 53


def mf(rid, out, ings, count=1, random=False):
    return Recipe(rid, out, count, ("Mystic Forge",), 0,
                  tuple(Ingredient(i, c) for i, c in ings), source="mystic_forge", random=random)


MF = [
    mf(-1, LEGEND, [(PRECURSOR, 1), (GIFT, 1), (INGOT, 10)]),
    mf(-2, CLOVER, [(ORE, 1)], random=True),
]
MF_QUOTES = {**QUOTES, PRECURSOR: Quote(9000, 10000)}
MF_ITEMS = {**ITEMS, GIFT: Item(GIFT, "Gift", frozenset({"AccountBound"}))}


def test_mystic_forge_recipe_is_used_and_ignores_discipline_filter():
    s = make(quotes=MF_QUOTES, items=MF_ITEMS, mystic_forge=MF,
             recipe_allowed=lambda r: False)  # no normal recipe allowed
    node = s.cost(LEGEND)
    assert node.source == "craft" and node.uses_mystic_forge
    assert node.unit_cost == 10000 + 0 + 10 * 30  # ingot bought, since its recipe isn't allowed
    assert node.needs_own  # the gift


def test_mystic_forge_off_by_default():
    assert make(quotes=MF_QUOTES).cost(LEGEND).source == "own"


def test_random_mystic_forge_output_is_skipped():
    assert make(mystic_forge=MF).cost(CLOVER).source == "own"


def test_own_values_are_kept_separate_from_gold_cost():
    s = make(quotes=MF_QUOTES, items=MF_ITEMS, mystic_forge=MF, own_values={GIFT: 50_000})
    node = s.cost(LEGEND)
    assert node.unit_cost == 10000 + 10 * 20
    assert node.own_value == 50_000
