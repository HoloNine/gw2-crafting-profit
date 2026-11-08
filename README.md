GW2 Crafting Profit
===================

Finds Guild Wars 2 recipes where the cost to craft is lower than the Trading Post
sell price after the 15% tax. Uses the official GW2 API. All money is in copper
inside the code and shown as `1g 23s 45c`.

## Setup

```
uv sync
cp .env.example .env      # then put your API key in .env (never commit it)
uv run gw2craft sync      # first run downloads recipes, items and prices (~1.5 min)
```

API key permissions needed: `account`, `unlocks`, `characters`
(later: `inventories` and `wallet` for legendary "have / need" checks).

## Commands

```
gw2craft sync [--full] [--prices]   refresh cache (recipes/items weekly, prices every 10 min)
gw2craft account                    your characters, discipline levels, expansions
gw2craft find                       profitable recipes, both price modes
  --potential (default)             all recipes, Unlock column says what is missing
  --mine                            only recipes you know, at your current levels
  --mode patient|fast|both          --min-profit 50s  --min-roi 10  --max-cost 20g
  --discipline Weaponsmith          --top 50          --export out.csv|out.json
  --own-mats                        include recipes that need unbuyable materials
  --max-spread 2                    Patient: skip troll listings (0 = off)
gw2craft show <item name or ID>     one item: profit in both modes, craft tree,
  --mode patient|fast               shopping list and what's still locked (+ wiki links)
  --qty 5  --recipe ID  --mine      --mine: only use recipes you know, buy the rest
gw2craft legendary [name]           sellable legendaries with a Mystic Forge recipe:
  --mine                            TP parts, own materials (have / need), profit, competition
gw2craft hints                      good recipes you don't have yet (or can't craft yet),
  --discipline Weaponsmith          with how to get them and a GW2 Wiki link
  --top 30
```

## Account tracking (Trading Post results)

```
gw2craft track sync       # pull TP history, open orders, delivery box, wallet; save a snapshot
gw2craft track summary    # account value, cash flow 7/30/90 days, open listings, buy orders
gw2craft track items --period 30   # per item: what performed well / badly, loot sales
gw2craft track craft "<item>" 10 [--cost 1g20s]   # log a craft run with its real cost
```

**Per-item profit:** each unit sold gets its cost from, in order:
1. **exact**: an earlier TP buy of the same item, matched first-bought = first-sold (FIFO).
2. **logged**: a craft run you logged with `track craft`.
3. **estimated**: you can craft it, so its craft cost (patient prices, saved at each
   sync) is used. The tool can't know if you really crafted it.
4. **loot**: none of the above (drops, gathering, bought before your history).
   Revenue only. Loot is shown separately so it doesn't look like trading profit.

Needs key permissions `account`, `tradingpost`, `wallet` (`inventories` + `characters`
to value your stock).

**The GW2 API only keeps 90 days of TP history.** Every sync saves all transactions
locally (unique on id), so your history keeps growing. Sync at least every few weeks:
anything older than 90 days that was never synced is lost.

What the numbers mean:
- **Cash flow (exact):** sells after the 15% fees, minus coins spent on buys. It is
  not profit: coins spent on items you still hold or used in crafts count as spent.
  Listing fees of cancelled listings are not in the API, so they're not counted.
- **Account value:** wallet + delivery box + open listings (after the 10% exchange fee
  still to pay; the 5% listing fee is already paid) + coins locked in buy orders +
  stock (items you own, valued as if sold instantly to buy orders, after tax).
- **Open listings:** ✅ lowest / ⚠ undercut / ⏳ old (> 7 days), with a suggestion.
  If relisting costs more than the gap, it says "wait".

## Web UI (Streamlit)

```
uv sync --extra ui
uv run gw2craft ui          # or: uv run --extra ui streamlit run app.py
```

Dark theme, local only (bound to `127.0.0.1` in `.streamlit/config.toml`; your API
key never leaves this machine). Charts use Plotly.

**📈 Account** is the TP performance dashboard: a **Sync account** button (max once
per minute) and four views:
- **Overview**: profit for 7 / 30 / 90 days (trading + loot, explained on hover),
  account value with its trend, gold per day, 30-day cash flow, wallet, delivery box;
  a stacked account-value chart and a green/red daily cash-flow chart.
- **Items**: period picker; best 10 and worst 10 by trading profit; full table with
  sold, revenue, cost, profit, ROI, days to sell, cost basis and wiki links; loot sales apart.
- **Unsold**: open listings with status and a suggestion, plus listings by age.
- **Orders**: open buy orders (top / outbid) and the delivery box.

The other tabs: **Find** (filters, both modes, CSV download),
**Missing recipes** (hints with wiki links), **Item details** (profit, craft tree,
shopping list, what's locked) and **Legendaries**. It uses the same code as the
CLI, so the numbers match. Money columns are in gold so they sort properly.

With an API key, `find` shows a Status column (✅ have it / ⚠ missing recipe /
⚠ level too low / ⚠ missing sub-recipe), a clickable "wiki" link, and a short
hint line under each missing recipe. Without a key it shows "How to get it" from
the recipe flags.

The tool tells you **what** is missing. It does not plan levelling: use
[GW2Crafts](https://gw2crafts.net) for that.

**Patient** = buy ingredients with buy orders (+1c), sell with a listing (−1c).
**Fast** = buy instantly from listings, sell instantly to buy orders.

## Things checked against the live API (2026-11-08)

- **Where learned recipes show up.**
  - `/account/recipes` (account-wide) holds recipes learned from **recipe sheets**.
    It also returned 5 recipe IDs that `/v2/recipes` does not publish at all (ignored).
  - `/characters[].recipes` (per character) holds **auto-learned** and
    **discovered** recipes, plus some sheet recipes.
  - So a discovered recipe only counts on the character that discovered it.
    The tool checks each character separately and tells you which one to use.
- **Discipline levels are per character.** Each character lists only the
  disciplines it has unlocked. All characters seen had at most 2 active.
- **`NoSell` does not mean "can't trade".** It means an NPC merchant won't buy it.
  Twilight has `NoSell` and is on the TP. Only `AccountBound` and
  `SoulbindOnAcquire` block trading.
- **TP fees are charged on the total sale**, not per item (GW2 Wiki). Selling a
  stack loses less to rounding and the 1c minimums.
- **Troll listings.** Some items have a lowest listing many times above the top
  buy order. Patient mode skips them (see `--max-spread`).
- **Recipe sheets.** A scan of all ~74,000 items found 6,050 recipe → sheet links.
  4,624 of the 4,750 sheet recipes have a known sheet item. About 1,700 sheets are
  on the TP (their price is shown); the rest come from NPCs (see the wiki).
- **Wiki links** (`https://wiki.guildwars2.com/index.php?search=<name>`):
  exact names jump straight to the page (e.g. "Deldrimor Steel Ingot",
  "Berserker's Charged Stormcaller Greatsword", "Recipe: Charged Stormcaller
  Greatsword"). These land on a search results page instead:
  stat-prefixed names with no own page (e.g. "Sentinel's Destroyer Greatsword")
  and "+17 Agony Infusion". For sheet recipes the hint links the sheet's own page.

## Hand-kept data files (`data/`)

| File | What | Status |
|---|---|---|
| `vendor_prices.json` | NPC prices (copper) | empty, needs in-game checks |
| `timegated.json` | once-a-day crafts | 6 ascended materials |
| `mystic_forge.json` | Mystic Forge recipes for legendaries | Twilight (6 recipes), from the wiki's raw page source; more Gen 1 one at a time |
| `own_materials.json` | your value for account-bound parts | empty |
| `recipe_sheets.json` | NPC recipe sheet prices (optional, shown in hints if filled) | empty |

Never guess values for these files. Check them in game or on the GW2 Wiki.

## Honest caveats

- Prices move. A small margin can be gone by the time you finish crafting.
- Patient profits assume your orders fill. That can take time.
- Results depend on the vendor price list being right.
- Legendary "have" counts bank, material storage, shared slots and bags. Equipped
  gear and wallet currencies (Spirit Shards, Karma) are not counted yet.
- Expansion requirements are not tagged yet. The API doesn't say clearly which
  expansion a recipe needs, so the tool doesn't guess.
- On a new account, many good recipes show as "you don't have it yet". That's
  expected. The list gets shorter as you learn recipes.
- Sites like gw2efficiency and GW2Profits do similar things. This tool's value
  is custom filters, your own recipes, the "missing recipe" hints, and automation.
