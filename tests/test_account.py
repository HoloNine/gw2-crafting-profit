from gw2craft.account import Account, best_levels
from gw2craft.solver import Ingredient, Recipe


def rec(rid, disc, rating, flags=(), source="api"):
    return Recipe(rid, 1, 1, disc, rating, (Ingredient(2, 1),), source=source, flags=flags)


CHARS = [
    {"crafting": [{"discipline": "Weaponsmith", "rating": 400, "active": True},
                  {"discipline": "Chef", "rating": 150, "active": False}]},
    {"crafting": [{"discipline": "Chef", "rating": 300, "active": True}]},
]


def test_best_levels():
    assert best_levels(CHARS) == {"Weaponsmith": 400, "Chef": 300}
    assert best_levels(CHARS, active_only=True) == {"Weaponsmith": 400, "Chef": 300}
    assert best_levels([CHARS[0]], active_only=True) == {"Weaponsmith": 400}


def test_can_craft():
    acct = Account("x", set(), learned={1, 2, 3}, levels=best_levels(CHARS), active_levels={})
    assert acct.can_craft(rec(1, ("Weaponsmith",), 400))
    assert not acct.can_craft(rec(9, ("Weaponsmith",), 400, flags=("LearnedFromItem",)))  # no sheet
    assert acct.status(rec(9, ("Weaponsmith",), 400)) == "discover"                 # no flags
    assert not acct.can_craft(rec(9, ("Weaponsmith",), 400), allow_discover=False)
    assert not acct.can_craft(rec(1, ("Scribe",), 0))                               # discipline not unlocked
    assert acct.can_craft(rec(9, ("Weaponsmith",), 50, flags=("AutoLearned",)))     # auto-learned
    assert not acct.can_craft(rec(2, ("Chef",), 400))                               # level too low
    assert acct.can_craft(rec(3, ("Tailor", "Chef"), 300))                          # any listed discipline
    assert acct.can_craft(rec(-1, ("Mystic Forge",), 0, source="mystic_forge"))
