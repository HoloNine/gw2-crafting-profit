"""Command line entry point: `gw2craft ...`"""

from __future__ import annotations

import asyncio
import time

import typer
from rich.console import Console

from . import sync as sync_mod

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


@app.callback()
def main() -> None:
    pass
