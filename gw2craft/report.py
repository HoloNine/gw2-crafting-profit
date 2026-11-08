"""Terminal tables and CSV/JSON export."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .account import SHORT, Account
from .hints import is_missing, short_hint, status_label, wiki_url
from .pricing import fmt, roi_text
from .profit import ProfitRow
from .unlocks import how_to_get


def discipline_text(row: ProfitRow) -> str:
    names = "/".join(SHORT.get(d, d) for d in row.recipe.disciplines)
    return f"{names} {row.recipe.min_rating}"


def profit_table(rows: list[ProfitRow], title: str, view: str = "mine",
                 acct: Account | None = None) -> Table:
    """view: "mine" (no status), "key" (status + wiki + hint line), "nokey" (how to get it)."""
    t = Table(title=title, title_justify="left", header_style="bold")
    t.add_column("Item", overflow="fold", max_width=40)
    t.add_column("Discipline")
    t.add_column("Cost", justify="right")
    t.add_column("Sell", justify="right")
    t.add_column("Net", justify="right")
    t.add_column("Profit", justify="right", style="green")
    t.add_column("ROI", justify="right")
    if view == "key":
        t.add_column("Status")
        t.add_column("Wiki")
    elif view == "nokey":
        t.add_column("How to get it", style="cyan")
    t.add_column("Flags", style="yellow")
    for r in rows:
        made = f" x{r.recipe.output_count}" if r.recipe.output_count > 1 else ""
        cells = [r.name + made, discipline_text(r), fmt(r.craft_cost), fmt(r.sell_price),
                 fmt(r.net), fmt(r.profit), roi_text(r.roi)]
        missing = view == "key" and is_missing(r)
        if view == "key":
            cells += [status_label(r), f"[link={wiki_url(r.name)}]wiki[/link]" if missing else ""]
        elif view == "nokey":
            cells.append(r.unlock_text)
        t.add_row(*cells, ", ".join(r.flags))
        if missing:
            t.add_row(f"[dim]{short_hint(r, acct)}[/]", *[""] * (len(t.columns) - 1))
    return t


def print_rows(console: Console, rows: list[ProfitRow], title: str, view: str = "mine",
               acct: Account | None = None) -> None:
    if not rows:
        console.print(f"[yellow]{title}: nothing matches these filters.[/]")
        return
    console.print(profit_table(rows, title, view, acct))


def _as_dict(r: ProfitRow) -> dict:
    missing = is_missing(r)
    return {
        "item_id": r.item_id, "name": r.name, "mode": r.mode.value,
        "recipe_id": r.recipe.id, "disciplines": list(r.recipe.disciplines),
        "min_rating": r.recipe.min_rating, "output_count": r.recipe.output_count,
        "craft_cost_c": r.craft_cost, "sell_price_c": r.sell_price, "net_c": r.net,
        "profit_c": r.profit, "roi_pct": round(r.roi * 100, 1), "flags": r.flags,
        "status": status_label(r).lstrip("✅⚠ ") if r.unlock else "",
        "how_to_get": how_to_get(r.recipe),
        "craft_on": r.unlock.character if r.unlock else None,
        "missing_sub_recipes": len(r.locked_sub),
        "wiki": wiki_url(r.name) if missing else "",
    }


def export(rows: list[ProfitRow], path: Path) -> None:
    """Write rows to .csv or .json. Money is in copper."""
    data = [_as_dict(r) for r in rows]
    if path.suffix == ".json":
        path.write_text(json.dumps(data, indent=2))
    elif path.suffix == ".csv":
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0]) if data else ["item_id"])
            w.writeheader()
            for d in data:
                w.writerow({**d, "disciplines": "/".join(d["disciplines"]),
                            "flags": "/".join(d["flags"])})
    else:
        raise ValueError("Export file must end in .csv or .json")
