"""Your account: learned recipes and crafting discipline levels (needs an API key).

A recipe counts as "mine" when at least one of your characters has unlocked a
discipline from the recipe at or above its min_rating (inactive disciplines count:
you can switch at a crafting station for a small fee), AND one of these is true:
- "known":    you have learned it (/account/recipes), or it is AutoLearned
- "discover": it has no flags, so you can unlock it for free in the Discovery
              panel by putting its ingredients in. The API only lists recipes
              you already discovered, so these would be missed otherwise.
Recipes flagged LearnedFromItem need a recipe sheet: only "known" if learned.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .api import GW2Api
from .solver import Recipe

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
NEEDED_PERMISSIONS = {"account", "unlocks", "characters"}


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
class Account:
    name: str
    permissions: set[str]
    learned: set[int]
    levels: dict[str, int]          # best rating per discipline, over all characters
    active_levels: dict[str, int]   # same, active disciplines only

    @property
    def missing_permissions(self) -> set[str]:
        return NEEDED_PERMISSIONS - self.permissions

    def status(self, recipe: Recipe) -> str:
        """'known', 'discover' or 'no'."""
        if recipe.mystic_forge:
            return "known"
        if not any(d in self.levels and self.levels[d] >= recipe.min_rating
                   for d in recipe.disciplines):
            return "no"
        if recipe.id in self.learned or "AutoLearned" in recipe.flags:
            return "known"
        if not recipe.flags:
            return "discover"
        return "no"

    def can_craft(self, recipe: Recipe, allow_discover: bool = True) -> bool:
        s = self.status(recipe)
        return s == "known" or (allow_discover and s == "discover")


def best_levels(characters: list[dict], active_only: bool = False) -> dict[str, int]:
    levels: dict[str, int] = {}
    for char in characters:
        for c in char.get("crafting", []):
            if active_only and not c.get("active"):
                continue
            levels[c["discipline"]] = max(levels.get(c["discipline"], 0), c["rating"])
    return levels


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
    learned = set(await api.get("/account/recipes", auth=True))
    chars = await api.get("/characters", {"ids": "all"}, auth=True)
    return Account(info.get("name", "?"), perms, learned,
                   best_levels(chars), best_levels(chars, active_only=True))
