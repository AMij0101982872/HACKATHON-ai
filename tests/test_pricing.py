import math

import pytest

from src.pricing import (
    VerticalSpread,
    apply_slippage,
    bs_delta,
    bs_price,
    price_vertical_credit,
    strike_for_delta,
)

T = 30 / 365
VOL = 0.25


# --- Black-Scholes : valeurs de référence ---------------------------------
def test_atm_call_put_parity():
    spot = strike = 100.0
    c = bs_price(spot, strike, T, VOL, "call")
    p = bs_price(spot, strike, T, VOL, "put")
    # parité call-put avec r = q = 0 : C - P = S - K
    assert c - p == pytest.approx(spot - strike, abs=1e-9)
    assert c == pytest.approx(p, abs=1e-9)


def test_price_positive_and_monotonic_in_vol():
    lo = bs_price(100, 105, T, 0.15, "call")
    hi = bs_price(100, 105, T, 0.45, "call")
    assert 0 < lo < hi


def test_expiry_returns_intrinsic():
    assert bs_price(110, 100, 0.0, VOL, "call") == pytest.approx(10.0)
    assert bs_price(90, 100, 0.0, VOL, "call") == 0.0
    assert bs_price(90, 100, 0.0, VOL, "put") == pytest.approx(10.0)


def test_deep_itm_call_approaches_spot_minus_strike():
    price = bs_price(200, 100, T, VOL, "call")
    assert price == pytest.approx(100.0, abs=1.0)


# --- Delta ---------------------------------------------------------------
def test_delta_signs_and_bounds():
    cd = bs_delta(100, 100, T, VOL, "call")
    pd = bs_delta(100, 100, T, VOL, "put")
    assert 0.0 < cd < 1.0
    assert -1.0 < pd < 0.0
    # relation call/put : delta_call - delta_put = 1 (q = 0)
    assert cd - pd == pytest.approx(1.0, abs=1e-9)


def test_atm_call_delta_near_half():
    assert bs_delta(100, 100, T, VOL, "call") == pytest.approx(0.5, abs=0.05)


# --- strike_for_delta --------------------------------------------------
@pytest.mark.parametrize("target", [0.16, 0.30, 0.45])
def test_strike_for_delta_roundtrip_put(target):
    k = strike_for_delta(100, target, T, VOL, "put")
    assert abs(bs_delta(100, k, T, VOL, "put")) == pytest.approx(target, abs=1e-3)
    assert k < 100  # un put à delta 0.3 est hors de la monnaie


@pytest.mark.parametrize("target", [0.16, 0.30, 0.45])
def test_strike_for_delta_roundtrip_call(target):
    k = strike_for_delta(100, target, T, VOL, "call")
    assert bs_delta(100, k, T, VOL, "call") == pytest.approx(target, abs=1e-3)
    assert k > 100


# --- VerticalSpread ---------------------------------------------------
def test_bull_put_spread_credit_and_max_loss():
    # bull put : on vend le put 95, on achète le put 90
    spread = VerticalSpread(option_type="put", short_strike=95.0, long_strike=90.0)
    credit = price_vertical_credit(spread, spot=100.0, t=T, vol=VOL)
    assert 0.0 < credit < spread.width  # un spread à crédit vaut moins que sa largeur
    ml = spread.max_loss(credit)
    assert ml == pytest.approx((spread.width - credit) * 100.0)
    assert ml > 0


def test_bear_call_spread_is_credit():
    spread = VerticalSpread(option_type="call", short_strike=105.0, long_strike=110.0)
    credit = price_vertical_credit(spread, spot=100.0, t=T, vol=VOL)
    assert credit > 0


def test_credit_shrinks_as_underlying_moves_against_bull_put():
    spread = VerticalSpread(option_type="put", short_strike=95.0, long_strike=90.0)
    far = price_vertical_credit(spread, spot=110.0, t=T, vol=VOL)  # sous-jacent loin au-dessus
    near = price_vertical_credit(spread, spot=96.0, t=T, vol=VOL)  # proche du strike court
    assert near > far  # spread plus "cher" à racheter = perte latente


# --- slippage --------------------------------------------------------
def test_slippage_reduces_entry_credit():
    assert apply_slippage(1.00, spread_pct=0.05) == pytest.approx(0.975)
    assert apply_slippage(2.00, spread_pct=0.10) == pytest.approx(1.90)


# --- garde-fous d'entrée -------------------------------------------
def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        bs_price(100, 100, T, 0.0, "call")
    with pytest.raises(ValueError):
        strike_for_delta(100, 1.5, T, VOL, "put")
