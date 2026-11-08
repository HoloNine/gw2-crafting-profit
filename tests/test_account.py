from gw2craft.account import Account, Character, flag_status
from gw2craft.solver import Ingredient, Recipe


def rec(rid, disc, rating, flags=(), source="api"):
    return Recipe(rid, 1, 1, disc, rating, (Ingredient(2, 1),), source=source, flags=flags)


SMITH = Character("Smith", 80, {"Weaponsmith": (500, True), "Chef": (150, False)}, recipes={10})
COOK = Character("Cook", 80, {"Chef": (300, True)}, recipes={20})
ACCT = Account("x", set(), [], sheet_recipes={30}, characters=[SMITH, COOK])


def test_levels_are_best_over_characters():
    assert ACCT.levels == {"Weaponsmith": 500, "Chef": 300}
    assert ACCT.best_character("Chef").name == "Cook"


def test_learned_via_character_discovery_auto_or_sheet():
    s = ACCT.status(rec(10, ("Weaponsmith",), 400))           # discovered on Smith
    assert s.kind == "learned" and s.character == "Smith"
    assert ACCT.status(rec(30, ("Weaponsmith",), 400, ("LearnedFromItem",))).ready  # account sheet
    assert ACCT.status(rec(99, ("Weaponsmith",), 400, ("AutoLearned",))).ready


def test_discovery_is_per_character():
    # Recipe 20 was discovered by Cook (Chef 300). A Chef 200 recipe is learned on Cook.
    assert ACCT.status(rec(20, ("Chef",), 200)).character == "Cook"
    # Recipe 10 needs Chef 200 but only Smith discovered it, and Smith's Chef is 150.
    s = ACCT.status(rec(10, ("Chef",), 200))
    assert s.kind == "discover" and s.character == "Cook"


def test_sheet_and_discover_and_level():
    assert ACCT.status(rec(98, ("Weaponsmith",), 400, ("LearnedFromItem",))).kind == "sheet"
    assert ACCT.status(rec(97, ("Weaponsmith",), 400)).kind == "discover"
    s = ACCT.status(rec(96, ("Chef",), 400))
    assert s.kind == "level" and s.have == 300 and s.text() == "Chef 400 needed (have 300)"
    assert ACCT.status(rec(95, ("Scribe",), 0)).text() == "Scr 0 needed (not unlocked)"


def test_inactive_discipline_flagged():
    s = ACCT.status(rec(94, ("Chef",), 100, ("AutoLearned",)))
    assert s.ready and s.character == "Cook" and not s.inactive  # active one preferred
    smith_only = Account("x", set(), [], set(), [SMITH])
    assert smith_only.status(rec(94, ("Chef",), 100, ("AutoLearned",))).inactive


def test_mystic_forge_always_ready():
    assert ACCT.status(rec(-1, ("Mystic Forge",), 0, source="mystic_forge")).ready


def test_flag_status_without_key():
    assert flag_status(rec(1, ("Chef",), 100, ("LearnedFromItem",))).kind == "sheet"
    assert flag_status(rec(1, ("Chef",), 100)).kind == "discover"
    assert flag_status(rec(1, ("Chef",), 100, ("AutoLearned",))).text() == "auto at Chef 100"
