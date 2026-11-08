"""GW2 Crafting Profit - Streamlit UI.

Run:  uv run --extra ui streamlit run app.py     (or: uv run gw2craft ui)

Uses the same code as the CLI, so numbers match `gw2craft find / hints / show / legendary`.
Money columns are in gold (1 gold = 100 silver = 10,000 copper) so they sort properly.
"""

from __future__ import annotations

import asyncio
import os

import pandas as pd
import streamlit as st
from rich.console import Console
from rich.text import Text

from gw2craft import sync as sync_mod
from gw2craft.account import Account, fetch_account, fetch_owned, load_env
from gw2craft.api import GW2Api
from gw2craft.hints import build_hints, is_missing, level_part, short_hint, status_label, wiki_url
from gw2craft.legendary import build_report, item_info, legendary_recipes, parse_listings, sellable_legendaries
from gw2craft.pricing import Mode, fmt, parse_money
from gw2craft.profit import CHECK_ROI, evaluate, find_profits, path_recipes, trusted_listing
from gw2craft.report import craft_tree, discipline_text, leaf_prices
from gw2craft.solver import build_solver, load_market, load_timegated, shopping_list
from gw2craft.unlocks import SheetPricer, how_to_get, load_npc_sheets, load_sheet_map

st.set_page_config(page_title="GW2 Crafting Profit", page_icon="⚒️", layout="wide")
load_env()

DISCIPLINES = ["(all)", "Armorsmith", "Artificer", "Chef", "Huntsman", "Jeweler",
               "Leatherworker", "Scribe", "Tailor", "Weaponsmith"]
GOLD = st.column_config.NumberColumn(format="%.2f g")


def g(copper: int | float | None) -> float | None:
    return None if copper is None else round(copper / 10_000, 4)


def plain(markup: str) -> str:
    return Text.from_markup(markup).plain


def render_text(renderable) -> str:
    c = Console(width=160, record=True, color_system=None)
    c.print(renderable)
    return c.export_text()


# ---------- data (cached) ----------

def db():
    return sync_mod.connect()  # one connection per run: sqlite connections are per thread


def refresh(force_prices: bool = False) -> list[str]:
    log: list[str] = []
    asyncio.run(sync_mod.sync_all(db(), force_prices=force_prices, log=log.append))
    st.cache_data.clear()
    return log


@st.cache_data(ttl=300, show_spinner="Reading your account...")
def get_account() -> Account | None:
    if not os.environ.get("GW2_API_KEY"):
        return None

    async def go():
        async with GW2Api() as api:
            return await fetch_account(api)
    try:
        return asyncio.run(go())
    except Exception as e:  # noqa: BLE001 - show any API problem in the UI
        st.sidebar.error(f"Could not read your account: {e}")
        return None


@st.cache_data(ttl=300, show_spinner="Reading your inventory...")
def get_owned() -> dict[int, int]:
    async def go():
        async with GW2Api() as api:
            return await fetch_owned(api)
    try:
        return asyncio.run(go())
    except Exception as e:  # noqa: BLE001
        st.warning(f"Could not read your inventory: {e}")
        return {}


@st.cache_data(ttl=600, show_spinner="Working out profits...")
def profit_table(mode: str, mine: bool, min_profit: int, min_roi: float, max_cost: int | None,
                 discipline: str, max_spread: float, own_mats: bool, _prices_at: str) -> pd.DataFrame:
    acct = get_account()
    m = Mode(mode)
    allowed = acct.can_craft if (mine and acct) else None
    disc = None if discipline == "(all)" else discipline
    rows = find_profits(
        build_solver(db(), m, recipe_allowed=allowed), min_profit=min_profit, min_roi=min_roi / 100,
        max_cost=max_cost, include_own=own_mats, max_spread=max_spread or None,
        recipe_filter=(lambda r: disc in r.disciplines) if disc else None,
        status_of=acct.status if acct else None)
    out = []
    for r in rows:
        missing = is_missing(r)
        out.append({
            "Item": r.name + (f" x{r.recipe.output_count}" if r.recipe.output_count > 1 else ""),
            "Discipline": discipline_text(r),
            "Cost": g(r.craft_cost), "Sell": g(r.sell_price), "Net": g(r.net), "Profit": g(r.profit),
            "ROI %": round(r.roi * 100, 1),
            "Status": status_label(r) if acct else r.unlock_text,
            "Hint": short_hint(r, acct).lstrip("↳ ") if acct else "",
            "Craft on": (r.unlock.character or "") if r.unlock else "",
            "Flags": ", ".join(r.flags),
            "Wiki": wiki_url(r.name) if (missing or not acct) else None,
            "item_id": r.item_id,
        })
    return pd.DataFrame(out)


# ---------- sidebar ----------

with st.sidebar:
    st.title("⚒️ GW2 Crafting Profit")
    conn = db()
    age = sync_mod.age_seconds(conn, "prices_synced_at")
    if age is None:
        st.warning("No data yet. Download it first (about 2-3 minutes the first time).")
        if st.button("Download data", type="primary"):
            with st.spinner("Downloading recipes, items and prices..."):
                for line in refresh():
                    st.write(line)
            st.rerun()
        st.stop()
    st.caption(f"Prices are {age / 60:.0f} min old.")
    if st.button("Refresh prices"):
        with st.spinner("Refreshing prices..."):
            refresh(force_prices=True)
        st.rerun()
    acct = get_account()
    if acct:
        st.success(f"Account: {acct.name}")
        st.caption("Levels: " + ", ".join(f"{d} {r}" for d, r in
                                          sorted(acct.levels.items(), key=lambda x: -x[1])))
    else:
        st.info("No API key: add GW2_API_KEY to .env to see which recipes you're missing.")
    st.divider()
    st.caption("Patient = buy with buy orders, sell with a listing.  \n"
               "Fast = buy and sell instantly.  \nProfit is per craft, after the 15% TP tax.")

prices_at = sync_mod.get_meta(conn, "prices_synced_at") or ""
tab_find, tab_hints, tab_item, tab_leg = st.tabs(["Find", "Missing recipes", "Item details", "Legendaries"])


# ---------- Find ----------

def money_input(label: str, default: str, key: str) -> int | None:
    text = st.text_input(label, default, key=key, help="e.g. 50s, 1g20s, 20g or plain copper. Empty = no limit.")
    if not text.strip():
        return None
    try:
        return parse_money(text)
    except ValueError as e:
        st.error(str(e))
        return None


with tab_find:
    c1, c2, c3, c4 = st.columns(4)
    mode = c1.radio("Mode", ["patient", "fast"], horizontal=True)
    view = c1.radio("Recipes", ["All (shows what's missing)", "Only mine"], horizontal=True,
                    disabled=acct is None)
    discipline = c2.selectbox("Discipline", DISCIPLINES)
    own_mats = c2.checkbox("Include recipes that need own (unbuyable) materials")
    min_profit = money_input("Min profit per craft", "1c", "fp") or 1
    with c3:
        min_roi = st.number_input("Min ROI %", value=10.0, step=5.0)
        max_spread = st.number_input("Patient: max listing / buy order", value=2.0, step=0.5,
                                     help="Skips troll listings. 0 = off.")
    with c4:
        max_cost = money_input("Max craft cost", "", "fc")
        search = st.text_input("Search item name")

    df = profit_table(mode, view == "Only mine" and acct is not None, min_profit, min_roi, max_cost,
                      discipline, max_spread, own_mats, prices_at)
    if search and not df.empty:
        df = df[df["Item"].str.contains(search, case=False, regex=False)]
    if df.empty:
        st.info("Nothing matches these filters.")
    else:
        st.caption(f"{len(df)} profitable recipes ({mode} mode).")
        if acct and view.startswith("All"):
            n_missing = int((df["Status"] != "✅ have it").sum())
            st.caption(f"⚠ {n_missing} good recipes you don't have yet. See **Missing recipes**.")
        st.dataframe(
            df.drop(columns=["item_id"]), hide_index=True, height=600,
            column_config={"Cost": GOLD, "Sell": GOLD, "Net": GOLD, "Profit": GOLD,
                           "ROI %": st.column_config.NumberColumn(format="%.0f%%"),
                           "Wiki": st.column_config.LinkColumn(display_text="wiki")})
        st.download_button("Download CSV", df.to_csv(index=False), "gw2craft_find.csv", "text/csv")


# ---------- Missing recipes (hints) ----------

with tab_hints:
    if acct is None:
        st.info("Add your API key (GW2_API_KEY in .env) to see which recipes you're missing.")
    else:
        h1, h2, h3 = st.columns(3)
        h_disc = h1.selectbox("Discipline", DISCIPLINES, key="hd")
        h_top = h2.number_input("How many", value=30, min_value=1, max_value=500, step=10)
        h_max_cost = money_input("Max craft cost", "", "hc")

        @st.cache_data(ttl=600, show_spinner="Finding recipes you don't have...")
        def hint_lines(disc: str, max_cost: int | None, _prices_at: str) -> list[list[str]]:
            a = get_account()
            d = None if disc == "(all)" else disc
            _, items, quotes = load_market(db())
            names = {i: it.name for i, it in items.items()}
            pricer = SheetPricer(load_sheet_map(db()), quotes, load_npc_sheets(), names, Mode.PATIENT)
            rows = {m: find_profits(build_solver(db(), m), min_roi=0.10, max_cost=max_cost,
                                    recipe_filter=(lambda r: d in r.disciplines) if d else None,
                                    status_of=a.status) for m in (Mode.PATIENT, Mode.FAST)}
            return [[plain(x) for x in h.lines]
                    for h in build_hints(rows[Mode.PATIENT], rows[Mode.FAST], a, pricer, names)]

        found = hint_lines(h_disc, h_max_cost, prices_at)
        st.caption(f"{len(found)} good recipes you don't have yet (or can't craft yet). "
                   "Levelling: see [GW2Crafts](https://gw2crafts.net).")
        for lines in found[: int(h_top)]:
            with st.container(border=True):
                st.markdown(f"**{lines[0]}**  \n" + "  \n".join(x.strip() for x in lines[1:]))


# ---------- Item details (show) ----------

with tab_item:
    q = st.text_input("Item name or ID", placeholder="e.g. Deldrimor Steel Ingot")
    if q:
        conn = db()
        craftable = {r[0] for r in conn.execute("SELECT DISTINCT output_item_id FROM recipes")}
        if q.strip().isdigit():
            matches = conn.execute("SELECT id, name FROM items WHERE id = ?", (int(q),)).fetchall()
        else:
            matches = conn.execute("SELECT id, name FROM items WHERE name LIKE ? ORDER BY length(name) LIMIT 50",
                                   (f"%{q.strip()}%",)).fetchall()
            matches = [r for r in matches if r[0] in craftable] or matches
        matches = [(r[0], r[1]) for r in matches]  # plain tuples: widgets must be copyable
        if not matches:
            st.warning("No item matches.")
        else:
            pick = st.selectbox("Item", matches, format_func=lambda r: f"{r[1]} ({r[0]})")
            item_id = pick[0]
            i_mode = Mode(st.radio("Mode for tree and shopping list", ["patient", "fast"], horizontal=True, key="im"))
            qty = st.number_input("Crafts", value=1, min_value=1, max_value=1000)
            solvers = {m: build_solver(conn, m) for m in Mode}
            s = solvers[i_mode]
            names = {i: it.name for i, it in s.items.items()}
            quote = s.quotes.get(item_id)
            recipes = [r for r in s.recipes_for(item_id) if not r.mystic_forge and r.only_items]
            st.subheader(names.get(item_id, str(item_id)))
            if quote:
                st.write(f"TP: top buy order **{fmt(quote.buy_price)}**, lowest listing **{fmt(quote.sell_price)}**")
            if not recipes:
                st.info(f"No normal crafting recipe makes this item. [Wiki]({wiki_url(names.get(item_id, q))})")
            else:
                def craft_cost(r):
                    n = s.craft(r)
                    return n.unit_cost if n.ok else float("inf")
                recipe = min(recipes, key=craft_cost)
                if len(recipes) > 1:
                    recipe = st.selectbox("Recipe", recipes, index=recipes.index(recipe),
                                          format_func=lambda r: f"{r.id}: {', '.join(r.disciplines)} {r.min_rating}")
                status_of = acct.status if acct else None
                line = f"Recipe {recipe.id}: {' / '.join(f'{d} {recipe.min_rating}' for d in recipe.disciplines)} — {how_to_get(recipe)}"
                if acct:
                    stt = acct.status(recipe)
                    lp = level_part(recipe, stt, acct)
                    line += f"  \nYour status: {'✅ have it' if stt.ready else '⚠ ' + stt.text()}" + (f" — {lp}" if lp else "")
                st.markdown(line)

                summary = []
                for m in Mode:
                    r = evaluate(solvers[m], recipe, status_of)
                    if r is None:
                        summary.append({"Mode": m.value, "Note": "can't price"})
                        continue
                    notes = []
                    if m is Mode.PATIENT and quote and not trusted_listing(quote, 2.0):
                        notes.append("listing far above buy orders")
                    if r.roi > CHECK_ROI:
                        notes.append("check: very high ROI")
                    if r.node.needs_own:
                        notes.append("leaves out own materials")
                    summary.append({"Mode": m.value, "Cost": g(r.craft_cost), "Sell": g(r.sell_price),
                                    "Net": g(r.net), "Profit": g(r.profit), "ROI %": round(r.roi * 100, 1),
                                    "Note": "; ".join(notes)})
                st.dataframe(pd.DataFrame(summary), hide_index=True,
                             column_config={"Cost": GOLD, "Sell": GOLD, "Net": GOLD, "Profit": GOLD})

                node = s.craft(recipe)
                if node.ok:
                    amount = recipe.output_count * qty
                    left, right = st.columns([3, 2])
                    with left:
                        st.markdown("**Craft tree**")
                        st.code(render_text(craft_tree(node, amount, names, "Tree", status_of, load_timegated())),
                                language=None)
                    with right:
                        st.markdown("**Shopping list**")
                        units = leaf_prices(node)
                        shop = [{"Item": names.get(iid, str(iid)), "Qty": round(qn, 2),
                                 "Where": {"buy": "TP", "vendor": "NPC", "own": "own mats"}.get(src, src),
                                 "Each": g(units[(src, iid)]) if src in ("buy", "vendor") else None,
                                 "Total": g(units[(src, iid)] * qn) if src in ("buy", "vendor") else None}
                                for (src, iid), qn in shopping_list(node, amount).items()]
                        shop_df = pd.DataFrame(shop).sort_values("Total", ascending=False, na_position="last")
                        st.dataframe(shop_df, hide_index=True, column_config={"Each": GOLD, "Total": GOLD})
                        st.write(f"Total to spend: **{fmt(round(sum(x['Total'] or 0 for x in shop) * 10_000))}**")
                    if acct:
                        locked = [(rc, stt) for rc in [recipe, *path_recipes(node)] if not (stt := acct.status(rc)).ready]
                        if locked:
                            st.warning(f"Still locked ({len(locked)}):")
                            for rc, stt in locked:
                                n = names.get(rc.output_item_id, str(rc.output_item_id))
                                bits = [how_to_get(rc)] + ([lp] if (lp := level_part(rc, stt, acct)) else [])
                                st.markdown(f"- **{n}**: {' | '.join(bits)} — [wiki]({wiki_url(n)})")
                        else:
                            st.success("✅ You can craft everything in this tree.")


# ---------- Legendaries ----------

with tab_leg:
    conn = db()
    types, rarity = item_info(conn)
    solvers = {m: build_solver(conn, m, with_mystic_forge=True) for m in Mode}
    sp = solvers[Mode.PATIENT]
    names = {i: it.name for i, it in sp.items.items()}
    recipes = {r.output_item_id: r for r in legendary_recipes(sp, rarity)}
    use_owned = st.checkbox("Show what I already own (needs inventories permission)", disabled=acct is None)
    owned = get_owned() if (use_owned and acct) else {}
    status_of = acct.status if acct else None

    @st.cache_data(ttl=600, show_spinner="Reading TP listings...")
    def listings_for(ids: tuple[int, ...], _t: str):
        async def go():
            async with GW2Api() as api:
                return {x["id"]: parse_listings(x) for x in await api.get_many("/commerce/listings", ids)}
        return asyncio.run(go())

    lst = listings_for(tuple(sorted(recipes)), prices_at) if recipes else {}
    table = []
    for iid, rec in sorted(recipes.items(), key=lambda kv: names.get(kv[0], "")):
        rep = build_report(sp, rec, types, status_of, owned)
        li = lst.get(iid)
        table.append({"Legendary": rep.name, "Precursor": g(rep.precursor_buy), "TP parts": g(rep.tp_total),
                      "Own mats": len(rep.own), "Sell": g(rep.sell), "Net": g(rep.net),
                      "Profit (own mats free)": g(rep.profit_own_free),
                      "Sell listings": li.sell_listings if li else None})
    st.dataframe(pd.DataFrame(table), hide_index=True,
                 column_config={k: GOLD for k in ("Precursor", "TP parts", "Sell", "Net", "Profit (own mats free)")})
    others = len([lg for lg in sellable_legendaries(conn) if lg.id not in recipes])
    st.caption(f"{others} other sellable legendaries have no Mystic Forge recipe yet (added one at a time from the wiki).")
    st.warning("'Own mats free' is not real gold profit: it counts your account-bound parts "
               "(map completion, WvW, clovers...) as free.")

    if recipes:
        pick = st.selectbox("Details for", sorted(recipes), format_func=lambda i: names.get(i, str(i)))
        reps = {m: build_report(solvers[m], recipes[pick], types, status_of, owned) for m in Mode}
        rp = reps[Mode.PATIENT]
        st.markdown(f"Recipe source: {recipes[pick].url}")
        if rp.precursor_id:
            pc = rp.precursor_craft
            craft = (f" — craft: {fmt(round(pc.unit_cost))} in TP parts"
                     + (" + own materials (collections)" if pc.needs_own else "")) if pc and pc.ok else ""
            st.write(f"Precursor **{names.get(rp.precursor_id)}**: buy {fmt(rp.precursor_buy)} (patient) / "
                     f"{fmt(reps[Mode.FAST].precursor_buy)} (fast){craft}")
        st.dataframe(pd.DataFrame([{"Mode": m.value, "TP parts": g(r.tp_total), "Sell": g(r.sell), "Net": g(r.net),
                                    "Profit (own mats free)": g(r.profit_own_free),
                                    "Profit (your values)": g(r.profit_with_values) if sp.own_values else None}
                                   for m, r in reps.items()]), hide_index=True,
                     column_config={k: GOLD for k in ("TP parts", "Sell", "Net", "Profit (own mats free)", "Profit (your values)")})
        a, b = st.columns(2)
        with a:
            st.markdown("**Buy on the TP (patient)**")
            st.dataframe(pd.DataFrame([{"Item": names.get(i, str(i)), "Qty": qn, "Each": g(u), "Total": g(qn * u)}
                                       for _, i, qn, u in rp.tp_parts]), hide_index=True,
                         column_config={"Each": GOLD, "Total": GOLD})
        with b:
            st.markdown("**You need these (can't buy on the TP)**")
            st.dataframe(pd.DataFrame([{"Item": names.get(i, str(i)), "Need": need,
                                        "Have": owned.get(i, 0) if use_owned else None,
                                        "Wiki": wiki_url(names.get(i, str(i)))} for i, need, _ in rp.own]),
                         hide_index=True, column_config={"Wiki": st.column_config.LinkColumn(display_text="wiki")})
        if rp.locked:
            st.warning("Recipes you still need: " + ", ".join(
                f"[{names.get(r.output_item_id)}]({wiki_url(names.get(r.output_item_id, ''))})" for r, _ in rp.locked))
        with st.expander("Craft tree"):
            st.code(render_text(craft_tree(rp.node, 1, names, "Tree", status_of, load_timegated())), language=None)
        sell = rp.sell or 0
        st.info(f"Fees on the sale: about {fmt(round(sell * 0.15))}. The 5% listing fee (~{fmt(round(sell * 0.05))}) "
                "is paid again every time you cancel and relist. Few buyers: a listing can wait days.")

