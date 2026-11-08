"""Your account: characters, discipline levels, learned recipes, expansions (needs an API key).

What the live API shows (checked 2026-11-08, see README):
- /account/recipes      -> recipes learned from recipe SHEETS (account-wide).
- characters[].recipes  -> per character: auto-learned + DISCOVERED recipes (and some sheets).
So a discovered recipe only counts on the character that discovered it.
- Discipline levels are per character. A character has at most 2 active disciplines;
  switching to an inactive one costs a small fee at a crafting station.

Recipe status, for one recipe:
- "learned":  a character with the right discipline level knows it
              (account sheet, that character's recipes, or AutoLearned).
- "discover": a character has the level, but must still discover it (free, needs ingredients once).
- "sheet":    a character has the level, but it needs a recipe sheet not learned yet.
- "level":    no character has the discipline at the needed level.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .api import GW2Api
from .solver import Recipe

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
NEEDED_PERMISSIONS = {"account", "unlocks", "characters"}

SHORT = {
    "Armorsmith": "Arm", "Artificer": "Art", "Chef": "Chef", "Huntsman": "Hunt",
    "Jeweler": "Jwl", "Leatherworker": "Lthr", "Scribe": "Scr", "Tailor": "Tail",
    "Weaponsmith": "Wpn", "Mystic Forge": "Forge",
}


def load_env(path: Path = ENV_PATH) -> None:
    """Read KEY=value lines from .env into os.environ (without overwriting)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Character:
    name: str
    level: int
    crafting: dict[str, tuple[int, bool]]  # discipline -> (rating, active)
    recipes: set[int] = field(default_factory=set)

    def rating(self, discipline: str) -> int | None:
        c = self.crafting.get(discipline)
        return c[0] if c else None


@dataclass(frozen=True)
class Status:
    kind: str                    # "learned" | "discover" | "sheet" | "level" | "auto" (no key)
    character: str | None = None  # who should craft it
    discipline: str | None = None
    inactive: bool = False       # discipline is inactive on that character (switch fee)
    have: int | None = None      # for "level": best current rating
    need: int = 0

    @property
    def ready(self) -> bool:
        return self.kind == "learned"

    def text(self) -> str:
        if self.kind == "level":
            d = SHORT.get(self.discipline or "", self.discipline)
            return f"{d} {self.need} needed" + (f" (have {self.have})" if self.have is not None else " (not unlocked)")
        if self.kind == "auto":
            return f"auto at {SHORT.get(self.discipline or '', self.discipline)} {self.need}"
        word = {"learned": "learned", "discover": "discover", "sheet": "sheet needed"}[self.kind]
        return word + (" (switch)" if self.inactive else "")


_RANK = {"learned": 0, "discover": 1, "sheet": 2}


def flag_status(recipe: Recipe) -> Status:
    """Status from recipe flags alone, when there is no API key."""
    d = recipe.disciplines[0] if recipe.disciplines else None
    if recipe.mystic_forge:
        return Status("learned")
    if "LearnedFromItem" in recipe.flags:
        return Status("sheet", discipline=d, need=recipe.min_rating)
    if "AutoLearned" in recipe.flags:
        return Status("auto", discipline=d, need=recipe.min_rating)
    return Status("discover", discipline=d, need=recipe.min_rating)


@dataclass
class Account:
    name: str
    permissions: set[str]
    access: list[str]                # expansions owned
    sheet_recipes: set[int]          # /account/recipes
    characters: list[Character]

    @property
    def levels(self) -> dict[str, int]:
        """Best rating per discipline over all characters."""
        out: dict[str, int] = {}
        for c in self.characters:
            for d, (r, _) in c.crafting.items():
                out[d] = max(out.get(d, 0), r)
        return out

    def best_character(self, discipline: str) -> Character | None:
        chars = [c for c in self.characters if c.rating(discipline) is not None]
        return max(chars, key=lambda c: c.rating(discipline)) if chars else None

    def status(self, recipe: Recipe) -> Status:
        if recipe.mystic_forge:
            return Status("learned")
        best: Status | None = None
        for char in self.characters:
            for d in recipe.disciplines:
                rating = char.rating(d)
                if rating is None or rating < recipe.min_rating:
                    continue
                inactive = not char.crafting[d][1]
                if (recipe.id in self.sheet_recipes or recipe.id in char.recipes
                        or "AutoLearned" in recipe.flags):
                    kind = "learned"
                elif "LearnedFromItem" in recipe.flags:
                    kind = "sheet"
                else:
                    kind = "discover"
                s = Status(kind, char.name, d, inactive, rating, recipe.min_rating)
                # Prefer learned > discover > sheet, then an active discipline.
                if best is None or (_RANK[kind], inactive) < (_RANK[best.kind], best.inactive):
                    best = s
        if best:
            return best
        # Nobody has the level: report the closest discipline.
        have = [(c.rating(d), d) for c in self.characters for d in recipe.disciplines
                if c.rating(d) is not None]
        if have:
            r, d = max(have)
            return Status("level", discipline=d, have=r, need=recipe.min_rating)
        d = recipe.disciplines[0] if recipe.disciplines else None
        return Status("level", discipline=d, need=recipe.min_rating)

    def can_craft(self, recipe: Recipe) -> bool:
        return self.status(recipe).ready


def parse_character(c: dict) -> Character:
    return Character(
        name=c.get("name", "?"),
        level=c.get("level", 0),
        crafting={x["discipline"]: (x["rating"], bool(x.get("active"))) for x in c.get("crafting", [])},
        recipes=set(c.get("recipes", [])),
    )


async def fetch_owned(api: GW2Api) -> dict[int, int]:
    """Item counts you own: bank + material storage + shared slots + every character's bags.

    Needs the `inventories` and `characters` permissions. Equipped gear is not counted.
    """
    counts: dict[int, int] = {}

    def add(slot: dict | None) -> None:
        if slot and slot.get("id"):
            counts[slot["id"]] = counts.get(slot["id"], 0) + slot.get("count", 1)

    for path in ("/account/bank", "/account/materials", "/account/inventory"):
        for slot in await api.get(path, auth=True):
            add(slot)
    for char in await api.get("/characters", {"ids": "all"}, auth=True):
        for bag in char.get("bags") or []:
            for slot in (bag or {}).get("inventory", []):
                add(slot)
    return counts


async def fetch_account(api: GW2Api) -> Account:
    token = await api.get("/tokeninfo", auth=True)
    perms = set(token.get("permissions", []))
    missing = NEEDED_PERMISSIONS - perms
    if missing:
        raise RuntimeError(
            f"Your API key is missing these permissions: {', '.join(sorted(missing))}. "
            "Make a new key at https://account.arena.net/applications with them ticked."
        )
    info = await api.get("/account", auth=True)
    sheets = set(await api.get("/account/recipes", auth=True))
    chars = await api.get("/characters", {"ids": "all"}, auth=True)
    return Account(info.get("name", "?"), perms, info.get("access", []), sheets,
                   [parse_character(c) for c in chars])
