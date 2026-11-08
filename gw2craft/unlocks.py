"""How each recipe is learned.

Unlock types (from recipe flags):
- "auto":      AutoLearned - learned automatically at the discipline level.
- "sheet":     LearnedFromItem - needs a recipe sheet item.
- "discovery": no flag - discover it in the Discovery tab at the discipline level
               (free, needs the ingredients once).

Optional sheet price: if the sheet item is on the TP, show its TP price. If you
filled data/recipe_sheets.json yourself, that is shown too. Otherwise "see wiki".
Sheet prices and vendor locations are never guessed.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .pricing import Mode, Quote, buy_cost, fmt
from .solver import Recipe

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SHEETS_PATH = DATA_DIR / "recipe_sheets.json"


def unlock_type(recipe: Recipe) -> str:
    if recipe.mystic_forge:
        return "forge"
    if "AutoLearned" in recipe.flags:
        return "auto"
    if "LearnedFromItem" in recipe.flags:
        return "sheet"
    return "discovery"


def level_text(recipe: Recipe) -> str:
    return " or ".join(f"{d} {recipe.min_rating}" for d in recipe.disciplines)


def how_to_get(recipe: Recipe) -> str:
    """Short text: how this recipe is learned."""
    t = unlock_type(recipe)
    if t == "auto":
        return f"learned automatically at {level_text(recipe)}"
    if t == "sheet":
        return "needs a recipe sheet, see wiki"
    if t == "forge":
        return "Mystic Forge"
    return f"discover it in the Discovery tab at {level_text(recipe)}"


def load_sheet_map(db: sqlite3.Connection) -> dict[int, list[int]]:
    """recipe_id -> sheet item IDs that teach it."""
    out: dict[int, list[int]] = defaultdict(list)
    for r in db.execute("SELECT recipe_id, sheet_item_id FROM recipe_sheets"):
        out[r[0]].append(r[1])
    return dict(out)


def load_npc_sheets(path: Path = SHEETS_PATH) -> dict[int, dict]:
    """{"<sheet item id>": {"currency": "Coin"|"Karma"|..., "amount": n, "source": url}}"""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text() or "{}")
    return {int(k): v for k, v in raw.items() if not k.startswith("_")}


@dataclass(frozen=True)
class SheetCost:
    sheet_item_id: int | None
    gold: int | None        # copper, if paid in coin
    other: str | None       # e.g. "2,100 Karma" if paid in another currency
    where: str              # "TP" | "NPC" | "unknown"

    def text(self) -> str:
        if self.gold is not None:
            return f"{fmt(self.gold)} ({self.where})"
        if self.other:
            return f"{self.other} ({self.where})"
        return "see wiki"


class SheetPricer:
    def __init__(self, sheet_map: dict[int, list[int]], quotes: dict[int, Quote],
                 npc: dict[int, dict], names: dict[int, str], mode: Mode):
        self.sheet_map, self.quotes, self.npc, self.names, self.mode = sheet_map, quotes, npc, names, mode

    def cost(self, recipe_id: int) -> SheetCost:
        """Cheapest known way to get a sheet for this recipe."""
        options: list[SheetCost] = []
        others: list[SheetCost] = []
        for sid in self.sheet_map.get(recipe_id, []):
            q = self.quotes.get(sid)
            price = buy_cost(q, self.mode) if q else None
            if price is not None:
                options.append(SheetCost(sid, price, None, "TP"))
            n = self.npc.get(sid)
            if n:
                cur, amount = n.get("currency", "Coin"), int(n["amount"])
                if cur == "Coin":
                    options.append(SheetCost(sid, amount, None, "NPC"))
                else:
                    others.append(SheetCost(sid, None, f"{amount:,} {cur}", "NPC"))
        if options:
            return min(options, key=lambda c: c.gold)
        if others:
            return others[0]
        sheets = self.sheet_map.get(recipe_id, [])
        return SheetCost(sheets[0] if sheets else None, None, None, "unknown")
