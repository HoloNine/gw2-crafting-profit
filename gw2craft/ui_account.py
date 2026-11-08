"""Account performance dashboard (Streamlit tab): Overview, Items, Unsold, Orders.

Every number is labelled with how exact it is (see performance.py):
- Profit (realized): sells after fees minus their cost; loot sales count as revenue only.
- Cash flow (exact): sells after fees minus coins spent on buys. Not profit.
- Account value: from snapshots saved at each sync.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import sync as sync_mod
from .api import GW2Api
from .hints import wiki_url
from .performance import cash_flow, daily_cash_flow, item_results, open_listings, open_orders
from .pricing import fmt
from .tracking import account_value, ensure_schema, snapshots, sync_account

SYNC_COOLDOWN = 60  # seconds between syncs
GREEN, RED, BLUE, GREY = "#3fb950", "#f85149", "#58a6ff", "#8b949e"
GOLD = st.column_config.NumberColumn(format="%.2f g")
PERIODS = {"7 days": 7, "30 days": 30, "90 days": 90, "All": None}


def g(copper) -> float | None:
    return None if copper is None else round(copper / 10_000, 4)


def db():
    conn = sync_mod.connect()
    ensure_schema(conn)
    return conn


def ago(ts: int | None) -> str:
    if not ts:
        return "never"
    mins = (time.time() - ts) / 60
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins:.0f} min ago"
    if mins < 48 * 60:
        return f"{mins / 60:.1f} h ago"
    return f"{mins / 1440:.0f} days ago"


def style(fig: go.Figure, height: int = 320) -> go.Figure:
    fig.update_layout(template="plotly_dark", height=height, margin=dict(l=10, r=10, t=40, b=10),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      legend=dict(orientation="h", y=-0.15), hovermode="x unified")
    return fig


# ---------- cached data ----------

@st.cache_data(ttl=600, show_spinner=False)
def load_items(days: int | None, _synced: str) -> list[dict]:
    rows = item_results(db(), days=days)
    return [{"Item": r.name, "Sold": r.known_units, "Revenue": g(r.revenue - r.loot_revenue),
             "Cost": g(r.cost), "Profit": g(r.trade_profit),
             "ROI %": round(r.trade_profit / r.cost * 100, 1) if r.cost else None,
             "Avg days to sell": round(r.avg_days_to_sell, 1) if r.avg_days_to_sell is not None else None,
             "Basis": r.label.replace(" + loot", "").replace("loot + ", ""),
             "Held": r.held or None, "Wiki": wiki_url(r.name),
             "_loot_units": r.basis["loot"], "_loot_rev": g(r.loot_revenue), "_known": r.known_units,
             "_total": g(r.profit)}
            for r in rows]


def realized(days: int | None, synced: str) -> dict:
    rows = load_items(days, synced)
    trade = sum(r["Profit"] or 0 for r in rows if r["_known"])
    loot = sum(r["_loot_rev"] or 0 for r in rows)
    return {"trade": trade, "loot": loot, "total": trade + loot}


# ---------- sync ----------

def sync_box(synced_at: int | None) -> None:
    left, right = st.columns([1, 3], vertical_alignment="center")
    wait = SYNC_COOLDOWN - (time.time() - (synced_at or 0))
    with left:
        clicked = st.button("🔄 Sync account", type="primary", width="stretch",
                            disabled=wait > 0, help="Pulls your TP history, open orders, delivery box and "
                            "wallet, and saves a snapshot. Max once per minute.")
    with right:
        st.markdown(f"Last synced **{ago(synced_at)}**"
                    + (f" · next sync possible in {int(wait) + 1}s" if wait > 0 else ""))
    if clicked:
        with st.spinner("Syncing your Trading Post data..."):
            async def go_sync():
                async with GW2Api() as api:
                    return await sync_account(api, db())
            try:
                res = asyncio.run(go_sync())
            except Exception as e:  # noqa: BLE001 - show any API problem in the UI
                st.error(f"Sync failed: {e}")
                return
        st.cache_data.clear()
        st.toast(f"Synced: {res.new_sells} new sells, {res.new_buys} new buys.", icon="✅")
        st.rerun()


# ---------- pages ----------

def overview(conn, synced: str) -> None:
    snaps = snapshots(conn)
    last = snaps[-1]
    value = account_value(last)
    r7, r30, r90 = (realized(d, synced) for d in (7, 30, 90))
    daily = daily_cash_flow(conn, 90)

    c = st.columns(4)
    for col, (label, r) in zip(c[:3], (("7 days", r7), ("30 days", r30), ("90 days", r90))):
        col.metric(f"Profit · last {label}", fmt(round(r["total"] * 10_000)), border=True,
                   help=f"Realized: sells after fees minus their cost. Trading "
                        f"{fmt(round(r['trade'] * 10_000))} + loot sales {fmt(round(r['loot'] * 10_000))} "
                        "(loot = no known cost, revenue only).")
    delta = None
    if len(snaps) > 1:
        change = value - account_value(snaps[0])
        delta = f"{'+' if change >= 0 else '-'}{fmt(abs(change))} since {datetime.fromtimestamp(snaps[0]['time'], UTC):%d %b}"
    c[3].metric("Account value now", fmt(value), delta=delta, border=True,
                chart_data=[account_value(s) / 10_000 for s in snaps] if len(snaps) > 1 else None,
                chart_type="area",
                help="Wallet + delivery box + open listings (after the 10% fee still to pay) + coins in "
                     "buy orders + stock (items you own, if sold instantly, after tax).")

    c = st.columns(4)
    c[0].metric("Gold per day (90 days)", fmt(round(r90["total"] * 10_000 / 90)), border=True,
                help="Realized profit over the last 90 days, divided by 90.")
    cf30 = cash_flow(conn, 30)
    c[1].metric("Cash flow · 30 days", fmt(cf30.profit), border=True,
                help="Exact: sells after fees minus coins spent on buys. Not profit: coins turned into "
                     "items you still hold count as spent.")
    c[2].metric("Wallet", fmt(last["wallet"]), border=True)
    pick = last["delivery_items"]
    c[3].metric("Waiting in delivery", f"{fmt(last['delivery_coins'])} · {pick} items", border=True,
                help="Pick these up at any Black Lion Trader.")

    left, right = st.columns(2)
    with left:
        df = pd.DataFrame([{**s, "value": account_value(s),
                            "when": datetime.fromtimestamp(s["time"], UTC)} for s in snaps])
        fig = go.Figure()
        parts = [("wallet", "Wallet"), ("listings_value", "Open listings"), ("buy_orders", "Buy orders"),
                 ("stock_value", "Stock"), ("delivery_coins", "Delivery")]
        for key, name in parts:
            fig.add_trace(go.Scatter(x=df["when"], y=df[key].fillna(0) / 10_000, name=name, stackgroup="v",
                                     mode="lines", line=dict(width=0.5),
                                     hovertemplate="%{y:,.2f} g"))
        fig.add_trace(go.Scatter(x=df["when"], y=df["value"] / 10_000, name="Total", mode="lines+markers",
                                 line=dict(color="#e3b341", width=2), hovertemplate="%{y:,.2f} g"))
        style(fig).update_layout(title="Account value (gold)")
        st.plotly_chart(fig, key="value_chart")
        if len(snaps) < 3:
            st.caption("The line grows with each sync. Sync every day or two for a useful chart.")
    with right:
        dd = pd.DataFrame(daily)
        if dd.empty:
            st.info("No sales or buys in the last 90 days.")
        else:
            dd["gold"] = dd["profit"] / 10_000
            fig = go.Figure(go.Bar(x=pd.to_datetime(dd["date"]), y=dd["gold"],
                                   marker_color=[GREEN if v >= 0 else RED for v in dd["gold"]],
                                   customdata=dd[["sold_net", "spent"]].to_numpy() / 10_000,
                                   hovertemplate="%{y:,.2f} g<br>sold %{customdata[0]:,.2f} g · "
                                                 "spent %{customdata[1]:,.2f} g<extra></extra>"))
            style(fig).update_layout(title="Daily cash flow (gold) — exact, not profit", hovermode="closest")
            st.plotly_chart(fig, key="daily_chart")

    hist = conn.execute("SELECT MIN(purchased), COUNT(*) FROM tp_history").fetchone()
    st.caption(f"Local history: {hist[1]:,} transactions since {(hist[0] or '-')[:10]}. "
               "The GW2 API only keeps 90 days — sync at least every few weeks so nothing is lost.")


def items_page(synced: str) -> None:
    label = st.segmented_control("Period", list(PERIODS), default="30 days", key="items_period") or "30 days"
    rows = load_items(PERIODS[label], synced)
    traded = sorted([r for r in rows if r["_known"]], key=lambda r: r["Profit"], reverse=True)
    loot = sorted([r for r in rows if r["_loot_units"]], key=lambda r: r["_loot_rev"], reverse=True)
    trade_total = sum(r["Profit"] for r in traded)
    loot_total = sum(r["_loot_rev"] for r in loot)

    c = st.columns(3)
    c[0].metric("Trading profit", fmt(round(trade_total * 10_000)), border=True,
                help="Items with a known cost: matched to your TP buys (exact), logged crafts, or "
                     "estimated craft cost.")
    c[1].metric("Loot sales", fmt(round(loot_total * 10_000)), border=True,
                help="Sold items with no known cost (drops, gathering, bought before your history). "
                     "Revenue only.")
    est = sum(1 for r in traded if "estimated" in r["Basis"])
    c[2].metric("Items with estimated cost", f"{est} of {len(traded)}", border=True,
                help="Cost = craft cost saved at sync time. We don't know if you really crafted them. "
                     "Log craft runs with `gw2craft track craft` to make them exact.")

    if traded:
        best = traded[:10]
        worst = [r for r in reversed(traded) if r["Profit"] < 0][:10]
        both = pd.DataFrame(list(reversed(worst)) + list(reversed(best))).drop_duplicates("Item")
        fig = go.Figure(go.Bar(y=both["Item"], x=both["Profit"], orientation="h",
                               marker_color=[GREEN if v >= 0 else RED for v in both["Profit"]],
                               hovertemplate="%{x:,.2f} g<extra></extra>"))
        style(fig, height=max(300, 26 * len(both))).update_layout(
            title="Best 10 and worst 10 by trading profit (gold)", hovermode="closest")
        st.plotly_chart(fig, key="items_chart")

        st.markdown("**What performed well — and what didn't**")
        table = pd.DataFrame(traded).drop(columns=[c for c in traded[0] if c.startswith("_")])
        st.dataframe(table, hide_index=True, height=420, column_config={
            "Revenue": GOLD, "Cost": GOLD, "Profit": GOLD,
            "ROI %": st.column_config.NumberColumn(format="%.0f%%"),
            "Avg days to sell": st.column_config.NumberColumn(help="0 = sold instantly to a buy order"),
            "Basis": st.column_config.TextColumn(help="exact = matched to your TP buys (FIFO) · logged = "
                                                 "your craft log · estimated = craft cost at sync time"),
            "Held": st.column_config.NumberColumn(help="Units bought but not sold yet"),
            "Wiki": st.column_config.LinkColumn(display_text="wiki")})
    else:
        st.info("No sales with a known cost in this period.")

    if loot:
        with st.expander(f"Loot sales ({len(loot)} items, revenue only)"):
            st.dataframe(pd.DataFrame([{"Item": r["Item"], "Sold": r["_loot_units"], "Revenue": r["_loot_rev"],
                                        "Avg days to sell": r["Avg days to sell"], "Wiki": r["Wiki"]}
                                       for r in loot]),
                         hide_index=True, column_config={"Revenue": GOLD,
                                                         "Wiki": st.column_config.LinkColumn(display_text="wiki")})


def unsold_page(conn) -> None:
    lst = open_listings(conn)
    if not lst:
        st.success("No open listings.")
        return
    undercut = [x for x in lst if x.status.startswith("⚠")]
    old = [x for x in lst if "old" in x.status]
    c = st.columns(3)
    c[0].metric("Open listings", len(lst), border=True)
    c[1].metric("Undercut", len(undercut), border=True, help="Someone lists the same item cheaper than you.")
    c[2].metric("Older than 7 days", len(old), border=True)

    left, right = st.columns([3, 1])
    with left:
        st.dataframe(pd.DataFrame([{
            "Item": x.name, "Qty": x.qty, "My price": g(x.my_price), "Lowest now": g(x.lowest),
            "Age (days)": round(x.age_days, 1), "Status": x.status, "Suggestion": x.suggestion,
            "Wiki": wiki_url(x.name)} for x in lst]), hide_index=True,
            column_config={"My price": GOLD, "Lowest now": GOLD,
                           "Suggestion": st.column_config.TextColumn(width="large"),
                           "Wiki": st.column_config.LinkColumn(display_text="wiki")})
        st.caption("Relisting means cancelling (the old 5% listing fee is not refunded) and paying "
                   "a new 5% fee. When that costs more than the gap, the suggestion says wait.")
    with right:
        buckets = {"<1d": 0, "1–3d": 0, "3–7d": 0, ">7d": 0}
        for x in lst:
            d = x.age_days
            buckets["<1d" if d < 1 else "1–3d" if d < 3 else "3–7d" if d < 7 else ">7d"] += 1
        fig = go.Figure(go.Bar(x=list(buckets), y=list(buckets.values()),
                               marker_color=[GREEN, BLUE, "#e3b341", RED]))
        style(fig, 300).update_layout(title="Listings by age", hovermode="closest")
        st.plotly_chart(fig, key="age_chart")


def orders_page(conn) -> None:
    last = snapshots(conn)[-1]
    if last["delivery_coins"] or last["delivery_items"]:
        st.warning(f"📦 You have {fmt(last['delivery_coins'])} and {last['delivery_items']} items to pick up "
                   "at a Black Lion Trader.")
    else:
        st.success("📦 Delivery box is empty.")
    orders = open_orders(conn)
    if not orders:
        st.info("No open buy orders.")
        return
    outbid = [o for o in orders if o.status.startswith("⚠")]
    c = st.columns(3)
    c[0].metric("Open buy orders", len(orders), border=True)
    c[1].metric("Outbid", len(outbid), border=True, help="Someone offers more than you, so your order waits.")
    c[2].metric("Coins locked in orders", fmt(sum(o.my_price * o.qty for o in orders)), border=True)
    st.dataframe(pd.DataFrame([{
        "Item": o.name, "Qty": o.qty, "My price": g(o.my_price), "Highest now": g(o.highest),
        "Age (days)": round(o.age_days, 1), "Status": o.status, "Wiki": wiki_url(o.name)} for o in orders]),
        hide_index=True, column_config={"My price": GOLD, "Highest now": GOLD,
                                        "Wiki": st.column_config.LinkColumn(display_text="wiki")})


def render(has_key: bool) -> None:
    """The whole Account tab."""
    if not has_key:
        st.info("Add your API key (GW2_API_KEY in .env, with account, tradingpost and wallet permissions) "
                "to track your Trading Post results.")
        return
    conn = db()
    synced_at = sync_mod.get_meta(conn, "account_synced_at")
    sync_box(int(synced_at) if synced_at else None)
    if not snapshots(conn):
        st.info("Click **Sync account** to pull your Trading Post data for the first time.")
        return
    synced = synced_at or ""
    t_over, t_items, t_unsold, t_orders = st.tabs(["Overview", "Items", "Unsold", "Orders"])
    with t_over:
        overview(conn, synced)
    with t_items:
        items_page(synced)
    with t_unsold:
        unsold_page(conn)
    with t_orders:
        orders_page(conn)
