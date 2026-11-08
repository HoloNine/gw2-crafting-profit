"""Terminal tables and CSV/JSON export."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .pricing import fmt
from .profit import ProfitRow

SHORT = {
    "Armorsmith": "Arm", "Artificer": "Art", "Chef": "Chef", "Huntsman": "Hunt",
    "Jeweler": "Jwl", "Leatherworker": "Lthr", "Scribe": "Scr", "Tailor": "Tail",
    "Weaponsmith": "Wpn",
}


def discipline_text(row: ProfitRow) -> str:
    names = "/".join(SHORT.get(d, d) for d in row.recipe.disciplines)
    return f"{names} {row.recipe.min_rating}"


def roi_text(roi: float) -> str:
    return ">999%" if roi > 9.99 else f"{roi:.0%}"


def profit_table(rows: list[ProfitRow], title: str) -> Table:
    t = Table(title=title, title_justify="left", header_style="bold")
    t.add_column("Item", overflow="fold", max_width=32)
    t.add_column("Discipline")
    t.add_column("Cost", justify="right")
    t.add_column("Sell", justify="right")
    t.add_column("Net", justify="right")
    t.add_column("Profit", justify="right", style="green")
    t.add_column("ROI", justify="right")
    t.add_column("Flags", style="yellow")
    for r in rows:
        made = f" x{r.recipe.output_count}" if r.recipe.output_count > 1 else ""
        t.add_row(r.name + made, discipline_text(r), fmt(r.craft_cost), fmt(r.sell_price),
                  fmt(r.net), fmt(r.profit), roi_text(r.roi), ", ".join(r.flags))
    return t


def print_rows(console: Console, rows: list[ProfitRow], title: str) -> None:
    if not rows:
        console.print(f"[yellow]{title}: nothing matches these filters.[/]")
        return
    console.print(profit_table(rows, title))


def _as_dict(r: ProfitRow) -> dict:
    return {
        "item_id": r.item_id, "name": r.name, "mode": r.mode.value,
        "recipe_id": r.recipe.id, "disciplines": list(r.recipe.disciplines),
        "min_rating": r.recipe.min_rating, "output_count": r.recipe.output_count,
        "craft_cost_c": r.craft_cost, "sell_price_c": r.sell_price, "net_c": r.net,
        "profit_c": r.profit, "roi_pct": round(r.roi * 100, 1), "flags": r.flags,
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
