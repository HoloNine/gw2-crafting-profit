""""Works well, but you don't have it" hints, with GW2 Wiki links.

Notify, don't plan: this module only says what is missing and links the wiki.
It does not plan levelling (use GW2Crafts for that).

Wiki links use the search URL with the English item name. The wiki jumps straight
to the page when the name matches exactly; otherwise you land on search results
(e.g. stat-prefixed weapons like "Sentinel's Destroyer Greatsword"). For recipe
sheets we link the sheet item ("Recipe: ..."), which has its own page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote_plus

from rich.markup import escape

from .account import Account, Status
from .pricing import fmt, roi_text
from .profit import ProfitRow
from .solver import Recipe
from .unlocks import SheetPricer, how_to_get, unlock_type

WIKI_SEARCH = "https://wiki.guildwars2.com/index.php?search="
MAX_SUBS = 3  # keep hints short: show at most this many missing sub-recipes


def wiki_url(name: str) -> str:
    return WIKI_SEARCH + quote_plus(name)


def status_label(row: ProfitRow) -> str:
    """✅ have it / ⚠ missing recipe / ⚠ level too low / ⚠ missing sub-recipe."""
    u = row.unlock
    if u is None:
        return ""
    if u.kind == "level":
        return "⚠ level too low"
    if u.kind in ("discover", "sheet", "auto"):
        return "⚠ missing recipe"
    if row.locked_sub:
        return "⚠ missing sub-recipe"
    return "✅ have it"


def is_missing(row: ProfitRow) -> bool:
    return row.unlock is not None and (not row.unlock.ready or bool(row.locked_sub))


def _you(acct: Account, s: Status) -> str:
    """'you: 150 on Char' / 'you: not unlocked'."""
    d = s.discipline or ""
    c = acct.best_character(d)
    if c is None:
        return "you: not unlocked"
    return f"you: {c.rating(d)} on {c.name}"


def level_part(recipe: Recipe, s: Status, acct: Account | None) -> str:
    if s.kind == "level":
        need = f"needs {s.discipline} {recipe.min_rating}"
        return f"{need} ({_you(acct, s)})" if acct else need
    if s.character:
        return f"craft on {s.character}" + (" (switch discipline)" if s.inactive else "")
    return ""


def short_hint(row: ProfitRow, acct: Account | None) -> str:
    """One short line for the find table."""
    u = row.unlock
    if u is None or not is_missing(row):
        return ""
    parts = []
    if not u.ready:
        kind = {"sheet": "recipe sheet", "discovery": "discovery", "auto": "auto-learned",
                "forge": "Mystic Forge"}[unlock_type(row.recipe)]
        lp = level_part(row.recipe, u, acct)
        parts.append(f"{kind}, {lp}" if lp else kind)
    if row.locked_sub:
        n = len(row.locked_sub)
        parts.append(f"{n} sub-recipe{'s' if n > 1 else ''} missing")
    return "↳ " + "; ".join(parts)


@dataclass
class Hint:
    row: ProfitRow                     # the mode the hint is ranked by
    other: ProfitRow | None            # same recipe in the other mode, if profitable
    lines: list[str] = field(default_factory=list)


def _mode_text(r: ProfitRow) -> str:
    return f"{fmt(r.profit)} per craft (ROI {roi_text(r.roi)}, {r.mode.value})"


def _sheet_bits(recipe: Recipe, pricer: SheetPricer | None) -> tuple[str, str | None]:
    """(price text, sheet item name) for a sheet recipe."""
    if pricer is None:
        return "", None
    c = pricer.cost(recipe.id)
    name = pricer.names.get(c.sheet_item_id) if c.sheet_item_id else None
    price = f"sheet {c.text()}" if c.gold is not None or c.other else ""
    return price, name


def build_hint(row: ProfitRow, other: ProfitRow | None, acct: Account | None,
               pricer: SheetPricer | None, names: dict[int, str]) -> Hint:
    u = row.unlock
    h = Hint(row, other)
    if u.kind == "level":
        h.lines.append("[yellow]⚠ Works well, but your discipline level is too low.[/]")
    elif not u.ready:
        h.lines.append("[yellow]⚠ Works well, but you don't have this recipe yet.[/]")
    else:
        h.lines.append("[yellow]⚠ Works well, but a recipe in its craft tree is missing.[/]")

    money = _mode_text(row)
    money += f"  |  {_mode_text(other)}" if other else "  |  not profitable in the other mode"
    if row.flags:
        money += "  [yellow]" + escape(f"[{', '.join(row.flags)}]") + "[/]"
    h.lines.append(f"  [bold]{row.name}[/] — {money}")

    link = wiki_url(row.name)
    if not u.ready:
        how = [how_to_get(row.recipe)]
        if unlock_type(row.recipe) == "sheet":
            price, sheet_name = _sheet_bits(row.recipe, pricer)
            if price:
                how.append(price)
            if sheet_name:
                link = wiki_url(sheet_name)
        lp = level_part(row.recipe, u, acct)
        if lp:
            how.append(lp)
        h.lines.append("  How to get it: " + "  |  ".join(how))
    else:
        h.lines.append(f"  You have it: craft on {u.character}")
    h.lines.append(f"  Learn it / find out more: {link}")

    for sub, s in row.locked_sub[:MAX_SUBS]:
        sub_name = names.get(sub.output_item_id, f"recipe {sub.id}")
        bits = [how_to_get(sub)]
        lp = level_part(sub, s, acct)
        if lp:
            bits.append(lp)
        sub_link = wiki_url(sub_name)
        if unlock_type(sub) == "sheet":
            price, sheet_name = _sheet_bits(sub, pricer)
            if price:
                bits.append(price)
            if sheet_name:
                sub_link = wiki_url(sheet_name)
        h.lines.append(f"  Missing sub-recipe: {sub_name} — {'  |  '.join(bits)}")
        h.lines.append(f"    {sub_link}")
    if len(row.locked_sub) > MAX_SUBS:
        h.lines.append(f"  ...and {len(row.locked_sub) - MAX_SUBS} more missing sub-recipes")
    return h


def build_hints(primary: list[ProfitRow], secondary: list[ProfitRow], acct: Account | None,
                pricer: SheetPricer | None, names: dict[int, str]) -> list[Hint]:
    """Hints for missing recipes, most profitable first.

    primary/secondary are the same search in two modes (patient, fast). A recipe
    only profitable in the secondary mode is still included.
    """
    by_recipe = {r.recipe.id: r for r in secondary}
    seen: set[int] = set()
    hints: list[Hint] = []
    for r in primary:
        if is_missing(r) and r.recipe.id not in seen:
            seen.add(r.recipe.id)
            hints.append(build_hint(r, by_recipe.get(r.recipe.id), acct, pricer, names))
    primary_ids = {r.recipe.id for r in primary}
    for r in secondary:
        if is_missing(r) and r.recipe.id not in seen and r.recipe.id not in primary_ids:
            seen.add(r.recipe.id)
            hints.append(build_hint(r, None, acct, pricer, names))
    hints.sort(key=lambda h: h.row.profit, reverse=True)
    return hints
