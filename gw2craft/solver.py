"""Find the cheapest way to get an item: buy on the TP, buy from an NPC, or craft it.

How it works (in plain terms):
- For each item we list every way to get it and pick the cheapest.
- Crafting cost = sum of (cheapest cost of each ingredient * count) / items made.
- Results are saved (memoised) so each item is worked out once.
- Loops (A needs B, B needs A) are cut: a path that loops back is treated as impossible.
- Items you can't buy, vendor or craft (often account bound) become "own materials":
  gold cost 0 plus a flag, so the report can say "needs own mats" instead of guessing.
  A route that needs own materials is only used when no fully priced route exists.
- If you gave your own value for a part (own_materials.json), it is added up
  separately in `own_value`, so the report can show profit with and without it.
- Mystic Forge recipes (mystic_forge.json) are used only when asked for
  (legendaries). They need no discipline.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .pricing import Mode, Quote, buy_cost

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Flags that stop an item being traded on the TP.
# NOT "NoSell": that only means an NPC merchant won't buy it (Twilight has NoSell
# and is on the TP). "AccountBindOnUse" is fine too: it binds only once used.
BOUND_FLAGS = {"AccountBound", "SoulbindOnAcquire"}


@dataclass(frozen=True)
class Ingredient:
    item_id: int
    count: int


@dataclass(frozen=True)
class Recipe:
    id: int
    output_item_id: int
    output_count: int
    disciplines: tuple[str, ...]
    min_rating: int
    ingredients: tuple[Ingredient, ...]
    only_items: bool = True  # False if it uses Currency / GuildUpgrade ingredients
    source: str = "api"      # "api" or "mystic_forge"
    random: bool = False     # Mystic Forge output count is random (e.g. Mystic Clovers)
    url: str | None = None   # wiki page the recipe came from

    @property
    def mystic_forge(self) -> bool:
        return self.source == "mystic_forge"


@dataclass(frozen=True)
class Item:
    id: int
    name: str
    flags: frozenset[str] = frozenset()

    @property
    def bound(self) -> bool:
        return bool(self.flags & BOUND_FLAGS)


@dataclass
class Node:
    """The chosen way to get one unit of an item."""
    item_id: int
    source: str                      # "buy" | "vendor" | "craft" | "own" | "none"
    unit_cost: float | None          # gold cost in copper per unit; None if impossible
    recipe: Recipe | None = None     # set when source == "craft"
    children: list[tuple[Node, int]] = field(default_factory=list)  # (ingredient node, count per craft)
    needs_own: bool = False          # somewhere below needs materials you must already have
    uses_vendor: bool = False
    timegated: bool = False
    own_value: float = 0.0           # your own value for own materials below (copper per unit)
    uses_mystic_forge: bool = False

    @property
    def ok(self) -> bool:
        return self.unit_cost is not None


NONE = "none"


class Solver:
    def __init__(
        self,
        recipes: list[Recipe],
        items: dict[int, Item],
        quotes: dict[int, Quote],
        mode: Mode,
        vendor_prices: dict[int, int] | None = None,
        timegated: set[int] | None = None,
        recipe_allowed: Callable[[Recipe], bool] | None = None,
        own_values: dict[int, int] | None = None,
        mystic_forge: list[Recipe] | None = None,
    ):
        self.items = items
        self.quotes = quotes
        self.mode = mode
        self.vendor = vendor_prices or {}
        self.timegated = timegated or set()
        self.allowed = recipe_allowed or (lambda r: True)
        self.own_values = own_values or {}
        self.by_output: dict[int, list[Recipe]] = defaultdict(list)
        for r in [*recipes, *(mystic_forge or [])]:
            self.by_output[r.output_item_id].append(r)
        self._memo: dict[int, Node] = {}
        self._in_progress: set[int] = set()

    # ----- public -----

    def cost(self, item_id: int) -> Node:
        """Cheapest way to get one unit of item_id."""
        node, _ = self._solve(item_id)
        return node

    def craft(self, recipe: Recipe) -> Node:
        """Cost of crafting with this exact recipe (ingredients chosen cheapest way).

        This is what the profit finder uses for the output item: we want the cost
        of *crafting* it, even if buying it would be cheaper.
        """
        node, _ = self._craft(recipe)
        return node

    def recipes_for(self, item_id: int) -> list[Recipe]:
        return self.by_output.get(item_id, [])

    # ----- internals -----

    def _solve(self, item_id: int) -> tuple[Node, set[int]]:
        """Returns (node, cut) where `cut` is the set of items whose loop was cut below here.

        A result is only memoised when nothing below it was cut by a loop that is still
        open (otherwise the answer could depend on where we started).
        """
        if item_id in self._memo:
            return self._memo[item_id], set()
        if item_id in self._in_progress:
            return Node(item_id, NONE, None), {item_id}

        self._in_progress.add(item_id)
        options: list[Node] = []
        cut: set[int] = set()

        quote = self.quotes.get(item_id)
        if quote is not None:
            price = buy_cost(quote, self.mode)
            if price is not None:
                options.append(Node(item_id, "buy", float(price)))

        if item_id in self.vendor:
            options.append(Node(item_id, "vendor", float(self.vendor[item_id]), uses_vendor=True))

        for recipe in self.recipes_for(item_id):
            if not recipe.only_items or recipe.random:
                continue  # random outputs are skipped unless an average count is confirmed
            if not recipe.mystic_forge and not self.allowed(recipe):
                continue
            node, sub_cut = self._craft(recipe)
            cut |= sub_cut
            if node.ok:
                options.append(node)

        self._in_progress.discard(item_id)
        cut.discard(item_id)

        best = self._pick(options)
        if best is None:
            if cut:
                best = Node(item_id, NONE, None)  # only route loops back on itself
            else:
                best = Node(item_id, "own", 0.0, needs_own=True,
                            own_value=float(self.own_values.get(item_id, 0)))

        if not cut:
            self._memo[item_id] = best
        return best, cut

    def _craft(self, recipe: Recipe) -> tuple[Node, set[int]]:
        node = Node(recipe.output_item_id, "craft", None, recipe=recipe,
                    timegated=recipe.output_item_id in self.timegated,
                    uses_mystic_forge=recipe.mystic_forge)
        total = 0.0
        own = 0.0
        cut: set[int] = set()
        for ing in recipe.ingredients:
            child, sub_cut = self._solve(ing.item_id)
            cut |= sub_cut
            if not child.ok:
                return Node(recipe.output_item_id, NONE, None, recipe=recipe), cut
            total += child.unit_cost * ing.count
            own += child.own_value * ing.count
            node.children.append((child, ing.count))
            node.uses_mystic_forge |= child.uses_mystic_forge
            node.needs_own |= child.needs_own
            node.uses_vendor |= child.uses_vendor
            node.timegated |= child.timegated
        node.unit_cost = total / recipe.output_count
        node.own_value = own / recipe.output_count
        return node, cut

    @staticmethod
    def _pick(options: list[Node]) -> Node | None:
        """Cheapest fully priced option; fall back to the cheapest that needs own mats."""
        priced = [o for o in options if not o.needs_own]
        pool = priced or options
        return min(pool, key=lambda o: o.unit_cost) if pool else None


# ---------- shopping list ----------

def shopping_list(node: Node, qty: float = 1.0) -> dict[tuple[str, int], float]:
    """Total amount of each leaf to get, keyed by (source, item_id).

    Amounts can be fractional when a sub-recipe makes more than 1 item
    (e.g. you need 2 of something that is crafted 5 at a time).
    """
    out: dict[tuple[str, int], float] = defaultdict(float)

    def walk(n: Node, amount: float) -> None:
        if n.source == "craft" and n.recipe is not None:
            crafts = amount / n.recipe.output_count
            for child, count in n.children:
                walk(child, crafts * count)
        else:
            out[(n.source, n.item_id)] += amount

    walk(node, qty)
    return dict(out)


# ---------- loading real data ----------

def load_json_ids(path: Path) -> dict[int, object]:
    """Read {"<item id>": value} files. Keys starting with '_' are treated as comments."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text() or "{}")
    return {int(k): v for k, v in raw.items() if not k.startswith("_")}


def load_vendor_prices(path: Path = DATA_DIR / "vendor_prices.json") -> dict[int, int]:
    return {k: int(v) for k, v in load_json_ids(path).items()}


def load_timegated(path: Path = DATA_DIR / "timegated.json") -> set[int]:
    return set(load_json_ids(path))


def load_own_values(path: Path = DATA_DIR / "own_materials.json") -> dict[int, int]:
    return {k: int(v) for k, v in load_json_ids(path).items()}


def load_mystic_forge(path: Path = DATA_DIR / "mystic_forge.json") -> list[Recipe]:
    """Read hand-kept Mystic Forge recipes. Random outputs need a confirmed avg_count."""
    out = []
    for n, (out_id, r) in enumerate(load_json_ids(path).items(), start=1):
        random = bool(r.get("random")) and "avg_count" not in r
        count = r.get("avg_count", r.get("output_count", 1))
        out.append(Recipe(
            id=-n,  # negative so it never clashes with real API recipe IDs
            output_item_id=out_id,
            output_count=count,
            disciplines=("Mystic Forge",),
            min_rating=0,
            ingredients=tuple(Ingredient(int(i["id"]), int(i["count"])) for i in r["ingredients"]),
            source="mystic_forge",
            random=random,
            url=r.get("source"),
        ))
    return out


def load_market(db: sqlite3.Connection) -> tuple[list[Recipe], dict[int, Item], dict[int, Quote]]:
    recipes = []
    for r in db.execute("SELECT * FROM recipes"):
        ings = json.loads(r["ingredients"])
        recipes.append(Recipe(
            id=r["id"],
            output_item_id=r["output_item_id"],
            output_count=r["output_item_count"],
            disciplines=tuple(json.loads(r["disciplines"])),
            min_rating=r["min_rating"],
            ingredients=tuple(Ingredient(i["id"], i["count"]) for i in ings if i["type"] == "Item"),
            only_items=bool(r["only_items"]),
        ))
    items = {
        r["id"]: Item(r["id"], r["name"], frozenset(json.loads(r["flags"])))
        for r in db.execute("SELECT id, name, flags FROM items")
    }
    quotes = {
        r["item_id"]: Quote(r["buy_price"], r["sell_price"])
        for r in db.execute("SELECT item_id, buy_price, sell_price FROM prices")
    }
    return recipes, items, quotes


def build_solver(db: sqlite3.Connection, mode: Mode,
                 recipe_allowed: Callable[[Recipe], bool] | None = None,
                 with_mystic_forge: bool = False) -> Solver:
    """with_mystic_forge=False for normal crafting, True for legendaries."""
    recipes, items, quotes = load_market(db)
    return Solver(recipes, items, quotes, mode,
                  vendor_prices=load_vendor_prices(),
                  timegated=load_timegated(),
                  recipe_allowed=recipe_allowed,
                  own_values=load_own_values(),
                  mystic_forge=load_mystic_forge() if with_mystic_forge else None)
