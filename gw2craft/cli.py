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
from .pricing import Mode, parse_money
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


@app.callback()
def main() -> None:
    load_env()  # reads GW2_API_KEY from .env if present
