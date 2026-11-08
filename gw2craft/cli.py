"""Command line entry point: `gw2craft ...`"""

from __future__ import annotations

import asyncio
import os
import time
from enum import Enum
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

from . import sync as sync_mod
from .account import Account, fetch_account, flag_status, load_env
from .hints import is_missing
from .api import GW2Api
from .pricing import Mode, fmt, parse_money, roi_text
from .profit import find_profits
from .report import export, print_rows
from .solver import build_solver

app = typer.Typer(help="Find profitable Guild Wars 2 crafting recipes.", no_args_is_help=True)
console = Console()


@app.command()
def sync(
    full: bool = typer.Option(False, "--full", help="Re-download all recipes and items, not just new ones."),
    prices: bool = typer.Option(False, "--prices", help="Refresh prices even if they are fresh."),
) -> None:
    """Download/refresh recipes, items (rarely) and prices (often)."""
    db = sync_mod.connect()
    start = time.time()
    asyncio.run(sync_mod.sync_all(db, force_static=full, force_prices=prices, log=console.print))
    counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("recipes", "items", "prices")}
    console.print(
        f"[green]Done in {time.time() - start:.1f}s.[/] Cache has "
        f"{counts['recipes']:,} recipes, {counts['items']:,} items, {counts['prices']:,} prices."
    )


class ModeChoice(str, Enum):
    patient = "patient"
    fast = "fast"
    both = "both"


def _money(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return parse_money(value)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


@app.command()
def find(
    mode: ModeChoice = typer.Option(ModeChoice.both, help="Price mode."),
    mine: bool = typer.Option(False, "--mine", help="Only recipes you have learned and can craft at your current levels (needs API key)."),
    potential: bool = typer.Option(True, "--potential", help="Default: every recipe, tagged with what is still missing (level, sheet, discovery)."),
    min_profit: str = typer.Option("1c", help="Minimum profit per craft, e.g. 50s or 1g20s."),
    min_roi: float = typer.Option(10.0, help="Minimum return on cost, in %."),
    max_cost: str | None = typer.Option(None, help="Maximum craft cost, e.g. 20g."),
    discipline: str | None = typer.Option(None, help="Only this discipline, e.g. Weaponsmith."),
    own_mats: bool = typer.Option(False, "--own-mats", help="Also show recipes that need your own (unbuyable) materials. Their cost leaves those out."),
    max_spread: float = typer.Option(2.0, help="Patient mode: skip items whose lowest listing is more than this many times the top buy order (0 = off)."),
    top: int = typer.Option(50, help="How many rows to show per mode."),
    export_path: Path | None = typer.Option(None, "--export", help="Save results to .csv or .json."),
) -> None:
    """List profitable recipes."""
    db = sync_mod.connect()
    asyncio.run(sync_mod.sync_all(db, log=lambda *_: None))  # refresh prices if older than 10 min
    age = sync_mod.age_seconds(db, "prices_synced_at") or 0

    acct: Account | None = None
    if mine:
        acct = _load_account()
        console.print(f"[dim]--mine: only recipes you know, at your levels: {_levels_text(acct.levels)}[/]")
    elif os.environ.get("GW2_API_KEY"):
        acct = _load_account(required=False)
    if not mine:
        how = "checked against your account" if acct else "from recipe flags only (no API key)"
        console.print(f"[dim]--potential: all recipes, status {how}.[/]")

    allowed = acct.can_craft if mine and acct else None
    status_of = acct.status if acct else flag_status

    disc = discipline.lower() if discipline else None
    recipe_filter = (lambda r: any(d.lower() == disc for d in r.disciplines)) if disc else None
    modes = [Mode.PATIENT, Mode.FAST] if mode is ModeChoice.both else [Mode(mode.value)]

    all_rows = []
    for m in modes:
        rows = find_profits(
            build_solver(db, m, recipe_allowed=allowed),
            min_profit=_money(min_profit) or 1,
            min_roi=min_roi / 100,
            max_cost=_money(max_cost),
            include_own=own_mats,
            recipe_filter=recipe_filter,
            max_spread=max_spread or None,
            status_of=status_of,
        )
        all_rows += rows
        label = "Patient (buy orders in, listings out)" if m is Mode.PATIENT else "Fast (buy instantly, sell instantly)"
        shown = rows[:top]
        view = "mine" if mine else ("key" if acct else "nokey")
        print_rows(console, shown, f"{label}: {len(rows)} profitable, showing {len(shown)}",
                   view=view, acct=acct)
        console.print()

    console.print(f"[dim]Prices are {age / 60:.0f} min old. Profit is per craft, after the 15% TP tax.[/]")
    if not mine and acct:
        missing = len({r.recipe.id for r in all_rows if is_missing(r)})
        console.print(f"[yellow]{missing} good recipes you don't have yet.[/] "
                      "Run [bold]gw2craft hints[/] for details and wiki links.")
    elif not acct:
        console.print("[yellow]Add your API key (GW2_API_KEY in .env) to see which recipes you're missing.[/]")
    if export_path:
        export(all_rows, export_path)
        console.print(f"Saved {len(all_rows)} rows to {export_path}")


def _levels_text(levels: dict[str, int]) -> str:
    return ", ".join(f"{d} {r}" for d, r in sorted(levels.items(), key=lambda x: -x[1])) or "none"


def _load_account(required: bool = True) -> Account | None:
    async def go() -> Account:
        async with GW2Api() as api:
            return await fetch_account(api)
    try:
        return asyncio.run(go())
    except (RuntimeError, httpx.HTTPError) as e:
        if not required:
            console.print(f"[yellow]Could not read your account ({e}). Using recipe flags only.[/]")
            return None
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from e


@app.command()
def account() -> None:
    """Check your API key: characters, discipline levels, learned recipes, expansions."""
    acct = _load_account()
    console.print(f"Account: [bold]{acct.name}[/]")
    console.print(f"Key permissions: {', '.join(sorted(acct.permissions))}")
    console.print(f"Expansions/access: {', '.join(acct.access)}")
    console.print(f"Recipe sheets learned (account-wide): {len(acct.sheet_recipes):,}")

    t = Table(title="Characters with crafting", title_justify="left", header_style="bold")
    t.add_column("Character")
    t.add_column("Level", justify="right")
    t.add_column("Disciplines (rating)")
    t.add_column("Known recipes", justify="right")
    for c in acct.characters:
        if not c.crafting and not c.recipes:
            continue
        disc = ", ".join(f"{d} {r}" + ("" if active else " (inactive)")
                         for d, (r, active) in sorted(c.crafting.items(), key=lambda x: -x[1][0]))
        t.add_row(c.name, str(c.level), disc or "-", f"{len(c.recipes):,}")
    console.print(t)

    console.print("Use this character for each discipline:")
    for d, r in sorted(acct.levels.items(), key=lambda x: -x[1]):
        c = acct.best_character(d)
        active = c.crafting[d][1]
        note = "" if active else "  [dim](inactive: switching costs a small fee)[/]"
        console.print(f"  {d:14} {r:>3}  on {c.name}{note}")
    console.print("[dim]A character can have 2 active disciplines at a time. "
                  "Discovered recipes are per character; recipe sheets are account-wide.[/]")


@app.command()
def hints(
    discipline: str | None = typer.Option(None, help="Only this discipline, e.g. Weaponsmith."),
    min_profit: str = typer.Option("1c", help="Minimum profit per craft."),
    min_roi: float = typer.Option(10.0, help="Minimum return on cost, in %."),
    max_cost: str | None = typer.Option(None, help="Maximum craft cost, e.g. 20g."),
    max_spread: float = typer.Option(2.0, help="Patient mode troll-listing filter (0 = off)."),
    top: int = typer.Option(30, help="How many hints to show."),
) -> None:
    """Profitable recipes you don't have yet (or can't craft yet), with wiki links."""
    from .hints import build_hints
    from .solver import load_market
    from .unlocks import SheetPricer, load_npc_sheets, load_sheet_map

    if not os.environ.get("GW2_API_KEY"):
        console.print("[yellow]Add your API key (GW2_API_KEY in .env) to see which recipes you're missing.[/]")
        raise typer.Exit(1)
    db = sync_mod.connect()
    asyncio.run(sync_mod.sync_all(db, log=lambda *_: None))
    acct = _load_account()
    disc = discipline.lower() if discipline else None
    recipe_filter = (lambda r: any(d.lower() == disc for d in r.disciplines)) if disc else None
    _, items, quotes = load_market(db)
    names = {i: it.name for i, it in items.items()}
    pricer = SheetPricer(load_sheet_map(db), quotes, load_npc_sheets(), names, Mode.PATIENT)

    rows = {}
    for m in (Mode.PATIENT, Mode.FAST):
        rows[m] = find_profits(build_solver(db, m), min_profit=_money(min_profit) or 1,
                               min_roi=min_roi / 100, max_cost=_money(max_cost),
                               recipe_filter=recipe_filter, max_spread=max_spread or None,
                               status_of=acct.status)
    found = build_hints(rows[Mode.PATIENT], rows[Mode.FAST], acct, pricer, names)
    console.print(f"[bold]{len(found)} good recipes you don't have yet (or can't craft yet).[/] "
                  f"Showing {min(top, len(found))}, most profitable first.\n")
    for h in found[:top]:
        for line in h.lines:
            console.print(line, soft_wrap=True)
        console.print()
    console.print("[dim]Levelling a discipline: see https://gw2crafts.net. Prices move; check the TP before buying.[/]")


def _find_item(db, query: str) -> int:
    """Item ID from an ID or a name. Exits with a short list if the name is unclear."""
    q = query.strip()
    if q.isdigit():
        if db.execute("SELECT 1 FROM items WHERE id = ?", (int(q),)).fetchone():
            return int(q)
        console.print(f"[red]No item with ID {q} in the cache.[/]")
        raise typer.Exit(1)
    exact = db.execute("SELECT id FROM items WHERE name = ? COLLATE NOCASE", (q,)).fetchall()
    craftable = {r[0] for r in db.execute("SELECT DISTINCT output_item_id FROM recipes")}
    if exact:
        ids = [r[0] for r in exact]
        best = [i for i in ids if i in craftable] or ids
        return best[0]
    like = db.execute("SELECT id, name FROM items WHERE name LIKE ? ORDER BY length(name) LIMIT 50",
                      (f"%{q}%",)).fetchall()
    like = [r for r in like if r[0] in craftable] or like
    if len(like) == 1:
        return like[0][0]
    if not like:
        console.print(f"[red]No item matches {q!r}.[/]")
    else:
        console.print(f"[yellow]{q!r} matches several items. Use the exact name or the ID:[/]")
        for iid, name in like[:12]:
            console.print(f"  {iid:>7}  {name}")
    raise typer.Exit(1)


@app.command()
def show(
    item: str = typer.Argument(..., help="Item name or ID."),
    mode: ModeChoice = typer.Option(ModeChoice.patient, help="Mode for the tree and shopping list (patient or fast)."),
    qty: int = typer.Option(1, help="How many crafts to make (shopping list)."),
    recipe_id: int | None = typer.Option(None, "--recipe", help="Use this recipe ID if several make the item."),
    mine: bool = typer.Option(False, "--mine", help="Only use recipes you know; buy parts you can't craft."),
) -> None:
    """Full craft tree, shopping list and what's still locked, for one item."""
    from .hints import level_part, wiki_url
    from .pricing import net
    from .profit import CHECK_ROI, evaluate, path_recipes, trusted_listing
    from .report import craft_tree, leaf_prices, shopping_table
    from .solver import load_timegated, shopping_list
    from .unlocks import SheetPricer, how_to_get, load_npc_sheets, load_sheet_map, unlock_type

    if mode is ModeChoice.both:
        raise typer.BadParameter("show draws one tree: use patient or fast (both modes are in the summary)")
    db = sync_mod.connect()
    asyncio.run(sync_mod.sync_all(db, log=lambda *_: None))
    acct = _load_account() if mine else (_load_account(required=False) if os.environ.get("GW2_API_KEY") else None)
    status_of = acct.status if acct else None
    allowed = acct.can_craft if (mine and acct) else None

    item_id = _find_item(db, item)
    solvers = {m: build_solver(db, m, recipe_allowed=allowed) for m in (Mode.PATIENT, Mode.FAST)}
    main_mode = Mode(mode.value)
    s = solvers[main_mode]
    names = {i: it.name for i, it in s.items.items()}
    name = names.get(item_id, str(item_id))
    quote = s.quotes.get(item_id)
    recipes = [r for r in s.recipes_for(item_id) if not r.mystic_forge and r.only_items]

    console.print(f"[bold]{name}[/] (item {item_id})")
    if quote:
        console.print(f"  TP: top buy order {fmt(quote.buy_price)}, lowest listing {fmt(quote.sell_price)}")
    else:
        console.print("  Not on the TP.")
    if not recipes:
        console.print("[yellow]No normal crafting recipe makes this item.[/] "
                      f"Wiki: {wiki_url(name)}")
        raise typer.Exit(0)

    rows = {m: {r.id: evaluate(solvers[m], r, status_of) for r in recipes} for m in solvers}
    if recipe_id is not None:
        if recipe_id not in rows[main_mode]:
            raise typer.BadParameter(f"Recipe {recipe_id} doesn't make {name}. "
                                     f"Options: {', '.join(str(r.id) for r in recipes)}")
        recipe = next(r for r in recipes if r.id == recipe_id)
    else:
        def cost_of(r):
            n = s.craft(r)
            return n.unit_cost if n.ok else float("inf")
        recipe = min(recipes, key=cost_of)
    if len(recipes) > 1:
        console.print(f"  {len(recipes)} recipes make this. Showing recipe {recipe.id} "
                      f"(cheapest in {main_mode.value} mode). Others: "
                      f"{', '.join(str(r.id) for r in recipes if r.id != recipe.id)} (use --recipe).")
    console.print(f"  Recipe {recipe.id}: {discipline_text_for(recipe)} — {how_to_get(recipe)}")
    if acct:
        st = acct.status(recipe)
        lp = level_part(recipe, st, acct)
        console.print(f"  Your status: {'✅ have it' if st.ready else '⚠ ' + st.text()}" + (f" — {lp}" if lp else ""))

    # Profit in both modes.
    t = Table(title="Profit per craft (after 15% tax)", title_justify="left", header_style="bold")
    for col in ("Mode", "Craft cost", "Sell at", "Net", "Profit", "ROI", "Note"):
        t.add_column(col, justify="left" if col in ("Mode", "Note") else "right")
    for m in (Mode.PATIENT, Mode.FAST):
        r = rows[m].get(recipe.id)
        if r is None:
            t.add_row(m.value, "-", "-", "-", "-", "-", "can't price (no TP price or parts)")
            continue
        notes = []
        if m is Mode.PATIENT and quote and not trusted_listing(quote, 2.0):
            notes.append("listing far above buy orders: price may not be real")
        if r.roi > CHECK_ROI:
            notes.append("check: very high ROI")
        if r.node.needs_own:
            notes.append("cost leaves out own materials")
        colour = "green" if r.profit > 0 else "red"
        t.add_row(m.value, fmt(r.craft_cost), fmt(r.sell_price), fmt(r.net),
                  f"[{colour}]{fmt(r.profit)}[/]", roi_text(r.roi), "; ".join(notes))
    console.print(t)

    # Tree + shopping list (main mode).
    node = s.craft(recipe)
    if not node.ok:
        console.print("[red]Can't work out a craft cost: some part can't be bought or crafted.[/]")
        raise typer.Exit(1)
    amount = recipe.output_count * qty
    console.print(craft_tree(node, amount, names, f"Craft tree ({main_mode.value}, {qty} craft{'s' if qty > 1 else ''})",
                             status_of, load_timegated()))
    console.print(shopping_table(shopping_list(node, amount), leaf_prices(node), names,
                                 f"Shopping list ({main_mode.value}, {qty} craft{'s' if qty > 1 else ''})"))
    r = rows[main_mode].get(recipe.id)
    if r:
        console.print(f"  Selling {amount} at {fmt(r.sell_price)} each nets "
                      f"{fmt(net(r.sell_price, amount))} → profit {fmt(net(r.sell_price, amount) - r.craft_cost * qty)}")

    # What's locked.
    if acct:
        pricer = SheetPricer(load_sheet_map(db), s.quotes, load_npc_sheets(), names, main_mode)
        locked = [(rc, st) for rc in [recipe, *path_recipes(node)] if not (st := acct.status(rc)).ready]
        if not locked:
            console.print("[green]✅ You can craft everything in this tree.[/]")
        else:
            console.print(f"[yellow]⚠ Still locked ({len(locked)}):[/]")
            for rc, st in locked:
                n = names.get(rc.output_item_id, str(rc.output_item_id))
                bits = [how_to_get(rc)]
                link = wiki_url(n)
                if unlock_type(rc) == "sheet":
                    c = pricer.cost(rc.id)
                    if c.gold is not None:
                        bits.append(f"sheet {c.text()}")
                    if c.sheet_item_id and c.sheet_item_id in names:
                        link = wiki_url(names[c.sheet_item_id])
                if lp := level_part(rc, st, acct):
                    bits.append(lp)
                console.print(f"  - {n}: {'  |  '.join(bits)}", soft_wrap=True)
                console.print(f"    {link}", soft_wrap=True)
    else:
        console.print("[dim]Add your API key to see which recipes in this tree you're missing.[/]")


def discipline_text_for(recipe) -> str:
    return " / ".join(f"{d} {recipe.min_rating}" for d in recipe.disciplines)


@app.command()
def legendary(
    item: str | None = typer.Argument(None, help="Legendary name or ID. Leave out to list them."),
    mine: bool = typer.Option(False, "--mine", help="Show what you already own (needs inventories permission)."),
) -> None:
    """Sellable legendaries: TP parts, own materials, profit after tax, competition."""
    from .account import fetch_owned
    from .hints import level_part, wiki_url
    from .legendary import build_report, item_info, legendary_recipes, parse_listings, sellable_legendaries
    from .solver import load_timegated
    from .report import craft_tree
    from .unlocks import how_to_get

    db = sync_mod.connect()
    asyncio.run(sync_mod.sync_all(db, log=lambda *_: None))
    has_key = bool(os.environ.get("GW2_API_KEY"))
    acct = _load_account() if mine else (_load_account(required=False) if has_key else None)
    owned: dict[int, int] = {}
    if mine and acct:
        async def get_owned():
            async with GW2Api() as api:
                return await fetch_owned(api)
        owned = asyncio.run(get_owned())

    types, rarity = item_info(db)
    solvers = {m: build_solver(db, m, with_mystic_forge=True) for m in (Mode.PATIENT, Mode.FAST)}
    sp = solvers[Mode.PATIENT]
    names = {i: it.name for i, it in sp.items.items()}
    recipes = {r.output_item_id: r for r in legendary_recipes(sp, rarity)}

    async def get_listings(ids):
        async with GW2Api() as api:
            return {x["id"]: parse_listings(x) for x in await api.get_many("/commerce/listings", ids)}
    listings = asyncio.run(get_listings(list(recipes))) if recipes else {}
    status_of = acct.status if acct else None

    if item is None:
        t = Table(title="Sellable legendaries with a Mystic Forge recipe (patient mode)",
                  title_justify="left", header_style="bold")
        for col in ("Legendary", "Precursor (TP)", "TP parts total", "Own mats", "Sell at", "Net",
                    "Profit (own mats free)", "Profit (your values)", "Sell listings"):
            t.add_column(col, justify="left" if col in ("Legendary",) else "right")
        for iid, rec in sorted(recipes.items(), key=lambda kv: names.get(kv[0], "")):
            rep = build_report(sp, rec, types, status_of, owned)
            li = listings.get(iid)
            t.add_row(rep.name, fmt(rep.precursor_buy), fmt(rep.tp_total), f"{len(rep.own)} kinds",
                      fmt(rep.sell), fmt(rep.net), fmt(rep.profit_own_free),
                      fmt(rep.profit_with_values) if sp.own_values else "-",
                      f"{li.sell_listings}" if li else "?")
        console.print(t)
        missing = [lg for lg in sellable_legendaries(db) if lg.id not in recipes]
        console.print(f"[dim]{len(missing)} other sellable legendaries have no recipe in "
                      "data/mystic_forge.json yet. They are added one at a time from the wiki.[/]")
        console.print("[yellow]'Own mats free' is not real gold profit: it counts your account-bound "
                      "parts (map completion, WvW, clovers...) as free.[/]")
        return

    item_id = _find_item(db, item)
    if item_id not in recipes:
        console.print(f"[yellow]{names.get(item_id, item)} has no Mystic Forge recipe in data/mystic_forge.json yet.[/]")
        raise typer.Exit(1)
    rec = recipes[item_id]
    reps = {m: build_report(solvers[m], rec, types, status_of, owned) for m in solvers}
    rp = reps[Mode.PATIENT]
    li = listings.get(item_id)

    console.print(f"[bold]{rp.name}[/] (item {item_id}) — recipe source: {rec.url}")
    q = sp.quotes.get(item_id)
    if q:
        console.print(f"  TP: top buy order {fmt(q.buy_price)}, lowest listing {fmt(q.sell_price)}")
    if li:
        console.print(f"  Competition: {li.sell_listings} sell listings ({li.sell_qty} items, "
                      f"{li.at_lowest} at the lowest price), {li.buy_orders} buy orders")

    # Precursor
    if rp.precursor_id:
        pname = names.get(rp.precursor_id, str(rp.precursor_id))
        line = f"  Precursor {pname}: buy {fmt(rp.precursor_buy)} (patient) / {fmt(reps[Mode.FAST].precursor_buy)} (fast)"
        pc = rp.precursor_craft
        if pc is not None and pc.ok:
            line += f"  |  craft: {fmt(round(pc.unit_cost))} in TP parts"
            if pc.needs_own:
                line += " + own materials (collections)"
        console.print(line)

    # Summary per mode
    t = Table(title="Profit (one legendary, after 15% tax)", title_justify="left", header_style="bold")
    for col in ("Mode", "TP parts", "Sell at", "Net", "Profit (own mats free)", "Profit (your values)"):
        t.add_column(col, justify="left" if col == "Mode" else "right")
    for m, r in reps.items():
        pf = r.profit_own_free
        colour = "green" if pf and pf > 0 else "red"
        t.add_row(m.value, fmt(r.tp_total), fmt(r.sell), fmt(r.net),
                  f"[{colour}]{fmt(pf)}[/]", fmt(r.profit_with_values) if sp.own_values else "-")
    console.print(t)

    # TP shopping list
    t = Table(title="Buy on the TP (patient)", title_justify="left", header_style="bold")
    for col in ("Item", "Qty", "Each", "Total"):
        t.add_column(col, justify="left" if col == "Item" else "right")
    for src, iid, qn, unit in rp.tp_parts:
        t.add_row(names.get(iid, str(iid)) + (" (NPC)" if src == "vendor" else ""),
                  f"{qn:g}", fmt(round(unit)), fmt(round(qn * unit)))
    t.add_section()
    t.add_row("[bold]Total[/]", "", "", f"[bold]{fmt(rp.tp_total)}[/]")
    console.print(t)

    # Own materials checklist
    t = Table(title="You need these (can't buy on the TP)", title_justify="left", header_style="bold")
    t.add_column("Item")
    t.add_column("Need", justify="right")
    t.add_column("Have", justify="right")
    t.add_column("Your value each", justify="right")
    t.add_column("Wiki")
    for iid, need, value in rp.own:
        n = names.get(iid, str(iid))
        if mine:
            have_n = owned.get(iid, 0)
            have = f"[green]{have_n}[/]" if have_n >= need else f"[red]{have_n}[/]"
        else:
            have = "[dim]--mine[/]"
        t.add_row(n, f"{need:g}", have, fmt(round(value)) if value else "-", f"[link={wiki_url(n)}]wiki[/link]")
    console.print(t)
    if rp.owned_parts:
        console.print("  Already owned crafted parts (their ingredients are not counted): "
                      + ", ".join(f"{u}× {names.get(i, i)}" for i, u, _ in rp.owned_parts))

    # Locked recipes in the tree
    if acct:
        if rp.locked:
            console.print(f"[yellow]⚠ Recipes you still need ({len(rp.locked)}):[/]")
            for r, st in rp.locked:
                n = names.get(r.output_item_id, str(r.output_item_id))
                bits = [how_to_get(r)]
                if lp := level_part(r, st, acct):
                    bits.append(lp)
                console.print(f"  - {n}: {'  |  '.join(bits)}  {wiki_url(n)}", soft_wrap=True)
        else:
            console.print("[green]✅ You know every crafting recipe in this tree.[/]")

    console.print(craft_tree(rp.node, 1, names, "Craft tree (patient)", status_of, load_timegated()))
    console.print(
        "[yellow]Before you commit gold:[/]\n"
        f"  - Tax: about {fmt(round((rp.sell or 0) * 0.15))} of the sale goes to fees. The 5% listing fee "
        f"(~{fmt(round((rp.sell or 0) * 0.05))}) is paid again every time you cancel and relist.\n"
        "  - Few buyers: a listing can wait days. Undercut by 1c, or wait.\n"
        "  - The price can drop while you gather parts (often after patches).\n"
        "  - 'Own mats free' profit is not real gold: most of the value is your own time.")


@app.callback()
def main() -> None:
    load_env()  # reads GW2_API_KEY from .env if present
