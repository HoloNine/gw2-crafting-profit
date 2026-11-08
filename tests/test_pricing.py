import pytest

from gw2craft.pricing import (
    Mode, Quote, buy_cost, exchange_fee, fmt, listing_fee, net, parse_money, sell_price,
)


# ---------- fees ----------

@pytest.mark.parametrize("price, listing, exchange, expected_net", [
    (1, 1, 1, -1),        # both 1c minimums: selling at 1c loses money
    (2, 1, 1, 0),
    (5, 1, 1, 3),         # 5% of 5 = 0.25 -> 0, raised to 1c minimum
    (10, 1, 1, 8),        # 5% = 0.5 -> rounds up to 1; 10% = 1
    (15, 1, 2, 12),       # 5% = 0.75 -> 1; 10% = 1.5 -> 2
    (29, 1, 3, 25),       # 5% = 1.45 -> 1; 10% = 2.9 -> 3
    (30, 2, 3, 25),       # 5% = 1.5 -> 2
    (100, 5, 10, 85),
    (10_000, 500, 1000, 8500),
    (19_999, 1000, 2000, 16_999),  # 999.95 -> 1000, 1999.9 -> 2000
])
def test_fees(price, listing, exchange, expected_net):
    assert listing_fee(price) == listing
    assert exchange_fee(price) == exchange
    assert net(price) == expected_net


def test_net_is_about_85_percent_for_big_prices():
    assert net(1_000_000) == 850_000


def test_fees_are_charged_on_the_total_not_per_item():
    # One bolt at 38c: fees 2c + 4c = 6c (16%), so you keep 32c.
    assert net(38) == 32
    # 250 bolts at 38c = 9500c total: fees 475c + 950c, so you keep 8075c.
    assert net(38, qty=250) == 8075
    # Per-item fees would have given only 250 * 32 = 8000c.
    assert net(38, qty=250) > 250 * net(38)


def test_qty_one_matches_single_price():
    assert net(12345, qty=1) == net(12345)


# ---------- mode prices ----------

def test_fast_mode_uses_instant_prices():
    q = Quote(buy_price=100, sell_price=120)
    assert buy_cost(q, Mode.FAST) == 120
    assert sell_price(q, Mode.FAST) == 100


def test_patient_mode_outbids_and_undercuts_by_1c():
    q = Quote(buy_price=100, sell_price=120)
    assert buy_cost(q, Mode.PATIENT) == 101
    assert sell_price(q, Mode.PATIENT) == 119


def test_patient_with_no_gap_between_orders():
    q = Quote(buy_price=100, sell_price=101)
    assert buy_cost(q, Mode.PATIENT) == 101   # can't outbid past the lowest listing
    assert sell_price(q, Mode.PATIENT) == 100  # can't undercut below the top buy order


def test_missing_sides():
    no_buys = Quote(buy_price=None, sell_price=50)
    assert buy_cost(no_buys, Mode.PATIENT) == 50
    assert sell_price(no_buys, Mode.PATIENT) == 49
    assert sell_price(no_buys, Mode.FAST) is None

    no_sells = Quote(buy_price=50, sell_price=None)
    assert buy_cost(no_sells, Mode.FAST) is None
    assert buy_cost(no_sells, Mode.PATIENT) == 51
    assert sell_price(no_sells, Mode.PATIENT) is None
    assert sell_price(no_sells, Mode.FAST) == 50


def test_patient_sell_never_below_1c():
    assert sell_price(Quote(buy_price=None, sell_price=1), Mode.PATIENT) == 1


# ---------- money formatting ----------

@pytest.mark.parametrize("copper, text", [
    (0, "0c"), (45, "45c"), (1200, "12s 0c"), (12345, "1g 23s 45c"),
    (10_000, "1g 0s 0c"), (-250, "-2s 50c"), (None, "-"),
])
def test_fmt(copper, text):
    assert fmt(copper) == text


@pytest.mark.parametrize("text, copper", [
    ("1g20s50c", 12050), ("1g 20s", 12000), ("50s", 5000), ("75c", 75),
    ("12345", 12345), ("2G", 20000), (" 3s 4c ", 304),
])
def test_parse_money(text, copper):
    assert parse_money(text) == copper


@pytest.mark.parametrize("bad", ["", "abc", "1x", "g"])
def test_parse_money_rejects_junk(bad):
    with pytest.raises(ValueError):
        parse_money(bad)
