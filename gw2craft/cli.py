"""Command line entry point: `gw2craft ...`"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from pathlib import Path

import typer
from rich.console import Console

from . import sync as sync_mod
from .account import Account, fetch_account, load_env
from .api import GW2Api
from .pricing import Mode, fmt, parse_money
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
    min_profit: str = typer.Option("1c", help="Minimum profit per craft, e.g. 50s or 1g20s."),
    min_roi: float = typer.Option(10.0, help="Minimum return on cost, in %."),
    max_cost: str | None = typer.Option(None, help="Maximum craft cost, e.g. 20g."),
    discipline: str | None = typer.Option(None, help="Only this discipline, e.g. Weaponsmith."),
    mine: bool = typer.Option(False, "--mine", help="Only recipes you can craft at your discipline levels: learned, auto-learned or discoverable (needs API key)."),
    no_discover: bool = typer.Option(False, "--no-discover", help="With --mine: hide recipes you would still need to discover."),
    own_mats: bool = typer.Option(False, "--own-mats", help="Also show recipes that need your own (unbuyable) materials. Their cost leaves those out."),
    max_spread: float = typer.Option(2.0, help="Patient mode: skip items whose lowest listing is more than this many times the top buy order (0 = off)."),
    top: int = typer.Option(50, help="How many rows to show per mode."),
    export_path: Path | None = typer.Option(None, "--export", help="Save results to .csv or .json."),
) -> None:
    """List profitable recipes."""
    db = sync_mod.connect()
    asyncio.run(sync_mod.sync_all(db, log=lambda *_: None))  # refresh prices if older than 10 min
    age = sync_mod.age_seconds(db, "prices_synced_at") or 0

    allowed = None
    if mine:
        acct = _load_account()
        allowed = lambda r: acct.can_craft(r, allow_discover=not no_discover)  # noqa: E731
        console.print(f"[dim]Your levels: {_levels_text(acct.levels)}. "
                      f"{len(acct.learned):,} learned recipes{'' if no_discover else ' + discoverable ones'}.[/]")

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
        )
        if mine:
            for r in rows:
                if acct.status(r.recipe) == "discover":
                    r.extra_flags = ("discover",)
        all_rows += rows
        label = "Patient (buy orders in, listings out)" if m is Mode.PATIENT else "Fast (buy instantly, sell instantly)"
        shown = rows[:top]
        print_rows(console, shown, f"{label}: {len(rows)} profitable, showing {len(shown)}")
        console.print()

    console.print(f"[dim]Prices are {age / 60:.0f} min old. Profit is per craft, after the 15% TP tax.[/]")
    if export_path:
        export(all_rows, export_path)
        console.print(f"Saved {len(all_rows)} rows to {export_path}")


def _levels_text(levels: dict[str, int]) -> str:
    return ", ".join(f"{d} {r}" for d, r in sorted(levels.items(), key=lambda x: -x[1])) or "none"


def _load_account() -> Account:
    async def go() -> Account:
        async with GW2Api() as api:
            return await fetch_account(api)
    try:
        return asyncio.run(go())
    except RuntimeError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from e


@app.command()
def account() -> None:
    """Check your API key and show your learned recipes and discipline levels."""
    acct = _load_account()
    console.print(f"Account: [bold]{acct.name}[/]")
    console.print(f"Key permissions: {', '.join(sorted(acct.permissions))}")
    console.print(f"Learned recipes: {len(acct.learned):,}")
    console.print("Best discipline levels (any character):")
    for d, r in sorted(acct.levels.items(), key=lambda x: -x[1]):
        active = acct.active_levels.get(d)
        note = "" if active == r else "  [dim](not active on that character)[/]"
        console.print(f"  {d:14} {r}{note}")


@app.callback()
def main() -> None:
    load_env()  # reads GW2_API_KEY from .env if present
