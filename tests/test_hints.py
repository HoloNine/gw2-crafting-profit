from gw2craft.account import Account, Character, Status
from gw2craft.hints import build_hint, is_missing, short_hint, status_label, wiki_url
from gw2craft.pricing import Mode, Quote
from gw2craft.profit import ProfitRow
from gw2craft.solver import Ingredient, Node, Recipe
from gw2craft.unlocks import SheetPricer, how_to_get, unlock_type

SMITH = Character("Smith", 80, {"Weaponsmith": (500, True), "Artificer": (150, True)})
ACCT = Account("x", set(), [], set(), [SMITH])


def rec(rid, disc="Artificer", rating=400, flags=()):
    return Recipe(rid, 100 + rid, 1, (disc,), rating, (Ingredient(1, 1),), flags=flags)


def row(recipe, status, subs=()):
    return ProfitRow(recipe, recipe.output_item_id, "Superior Sigil of X", Mode.PATIENT,
                     10_000, 15_000, 12_750, 2_750, 0.275, Node(recipe.output_item_id, "craft", 1.0),
                     unlock=status, locked_sub=list(subs))


def test_wiki_url_encodes_name():
    assert wiki_url("Berserker's Charged Stormcaller Greatsword") == \
        "https://wiki.guildwars2.com/index.php?search=Berserker%27s+Charged+Stormcaller+Greatsword"
    assert wiki_url("+17 Agony Infusion").endswith("search=%2B17+Agony+Infusion")


def test_unlock_type_and_text():
    assert unlock_type(rec(1, flags=("AutoLearned",))) == "auto"
    assert how_to_get(rec(1, flags=("AutoLearned",))) == "learned automatically at Artificer 400"
    assert how_to_get(rec(1, flags=("LearnedFromItem",))) == "needs a recipe sheet, see wiki"
    assert how_to_get(rec(1)) == "discover it in the Discovery tab at Artificer 400"


def test_status_labels():
    r = rec(1)
    assert status_label(row(r, Status("learned", "Smith"))) == "✅ have it"
    assert status_label(row(r, Status("level", discipline="Artificer", have=150, need=400))) == "⚠ level too low"
    assert status_label(row(r, Status("sheet", "Smith"))) == "⚠ missing recipe"
    sub = (rec(2), Status("discover", "Smith"))
    assert status_label(row(r, Status("learned", "Smith"), [sub])) == "⚠ missing sub-recipe"
    assert not is_missing(row(r, Status("learned", "Smith")))


def test_level_hint_says_which_character():
    r = rec(1, flags=("LearnedFromItem",))
    pr = row(r, Status("level", discipline="Artificer", have=150, need=400))
    assert short_hint(pr, ACCT) == "↳ recipe sheet, needs Artificer 400 (you: 150 on Smith)"
    pricer = SheetPricer({1: [900]}, {900: Quote(100, 250)}, {}, {900: "Recipe: Sigil of X"}, Mode.PATIENT)
    h = build_hint(pr, None, ACCT, pricer, {})
    text = "\n".join(h.lines)
    assert "don't have this recipe yet" not in text and "level is too low" in text
    assert "sheet 1s 1c (TP)" in text
    assert "search=Recipe%3A+Sigil+of+X" in text           # links the sheet page
    assert "needs Artificer 400 (you: 150 on Smith)" in text


def test_missing_sub_recipe_gets_own_link():
    r = rec(1)
    sub = (rec(2, "Weaponsmith", 300), Status("discover", "Smith", "Weaponsmith"))
    h = build_hint(row(r, Status("learned", "Smith"), [sub]), None, ACCT, None, {102: "Sub Part"})
    text = "\n".join(h.lines)
    assert "Missing sub-recipe: Sub Part" in text and "search=Sub+Part" in text
