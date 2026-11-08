from gw2craft.legendary import build_report, legendary_recipes, parse_listings
from gw2craft.pricing import Mode, Quote
from gw2craft.solver import Ingredient, Item, Recipe, Solver

LEG, PRE, GIFT, MAT, BOUND, PRE_PART = 1, 2, 3, 4, 5, 6


def mf(out, ings):
    return Recipe(-out, out, 1, ("Mystic Forge",), 0,
                  tuple(Ingredient(i, c) for i, c in ings), source="mystic_forge")


MF = [mf(LEG, [(PRE, 1), (GIFT, 1)]), mf(GIFT, [(MAT, 250), (BOUND, 77)])]
API = [Recipe(9, PRE, 1, ("Weaponsmith",), 500, (Ingredient(PRE_PART, 1),), flags=("LearnedFromItem",))]
ITEMS = {LEG: Item(LEG, "Legend"), PRE: Item(PRE, "Precursor"), GIFT: Item(GIFT, "Gift", frozenset({"AccountBound"})),
         MAT: Item(MAT, "Mat"), BOUND: Item(BOUND, "Clover", frozenset({"AccountBound"})),
         PRE_PART: Item(PRE_PART, "Collection Part", frozenset({"AccountBound"}))}
QUOTES = {LEG: Quote(1_500_000, 1_800_000), PRE: Quote(900_000, 1_100_000), MAT: Quote(1000, 1100)}
TYPES = {PRE: "Weapon"}


def solver(**kw):
    return Solver(API, ITEMS, QUOTES, Mode.PATIENT, mystic_forge=MF, **kw)


def test_finds_sellable_legendary_recipes_only():
    s = solver()
    assert [r.output_item_id for r in legendary_recipes(s, {LEG: "Legendary", GIFT: "Legendary"})] == [LEG]


def test_report_totals_and_profit():
    rep = build_report(solver(own_values={BOUND: 10_000}), MF[0], TYPES)
    assert rep.precursor_id == PRE
    assert rep.precursor_buy == 900_001
    assert rep.precursor_craft is not None and rep.precursor_craft.needs_own
    assert rep.tp_total == 900_001 + 250 * 1001
    assert rep.own == [(BOUND, 77, 10_000.0)]
    assert rep.sell == 1_799_999
    assert rep.net == 1_799_999 - 90_000 - 180_000
    assert rep.profit_own_free == rep.net - rep.tp_total
    assert rep.profit_with_values == rep.profit_own_free - 770_000


def test_owned_crafted_part_skips_its_ingredients():
    rep = build_report(solver(), MF[0], TYPES, owned={GIFT: 1})
    assert rep.tp_total == 900_001          # no mats bought for the gift
    assert rep.own == []
    assert rep.owned_parts == [(GIFT, 1, 1)]


def test_parse_listings():
    li = parse_listings({"sells": [{"listings": 3, "unit_price": 100, "quantity": 3},
                                   {"listings": 1, "unit_price": 90, "quantity": 2}],
                         "buys": [{"listings": 5, "unit_price": 80, "quantity": 9}]})
    assert (li.sell_listings, li.sell_qty, li.at_lowest, li.buy_orders) == (4, 5, 2, 5)
