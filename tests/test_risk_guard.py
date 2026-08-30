from src.risk_guard import (
    PROFILES,
    AccountState,
    ProposedOrder,
    RiskParams,
    check_order,
    risk_params,
)


def _acct(**over) -> AccountState:
    base = dict(
        equity=100_000.0,
        cash=100_000.0,
        buying_power=200_000.0,
        start_of_day_equity=100_000.0,
        high_water_mark=100_000.0,
        open_positions=0,
    )
    base.update(over)
    return AccountState(**base)


def _order(**over) -> ProposedOrder:
    base = dict(
        symbol="SPY",
        action="open",
        max_loss=400.0,
        collateral=400.0,
        symbol_exposure=0.0,
        dte=7,
        spread_pct=0.03,
        is_defined_risk=True,
    )
    base.update(over)
    return ProposedOrder(**base)


# --- cas nominal -------------------------------------------------------------
def test_open_ok_when_all_checks_pass():
    d = check_order(_order(), _acct())
    assert d.allowed is True
    assert d.reason == "OK"
    assert "liquidity" in d.checks


def test_close_is_always_allowed_even_under_kill_switch():
    d = check_order(
        _order(action="close"),
        _acct(equity=80_000.0),  # -20 % sur la journée
    )
    assert d.allowed is True


# --- kill-switches ---------------------------------------------------------
def test_daily_loss_kill_switch_blocks_open():
    d = check_order(_order(), _acct(equity=96_500.0))  # -3,5 %
    assert d.allowed is False
    assert "perte du jour" in d.reason


def test_drawdown_kill_switch_blocks_open():
    d = check_order(
        _order(),
        _acct(equity=89_000.0, start_of_day_equity=89_000.0, high_water_mark=100_000.0),
    )
    assert d.allowed is False
    assert "drawdown" in d.reason


# --- nature de l'ordre ---------------------------------------------------
def test_naked_leg_is_rejected():
    d = check_order(_order(is_defined_risk=False), _acct())
    assert d.allowed is False
    assert "non défini" in d.reason


def test_max_positions_cap():
    d = check_order(_order(), _acct(open_positions=5))
    assert d.allowed is False
    assert "plafond" in d.reason


def test_max_loss_per_structure():
    d = check_order(_order(max_loss=600.0), _acct())
    assert d.allowed is False
    assert "perte max" in d.reason


# --- exposition / capital ---------------------------------------------
def test_symbol_exposure_cap():
    # 20 % de 100k = 20 000 ; on est déjà à 19 800 + 400 -> dépasse
    d = check_order(_order(symbol_exposure=19_800.0, max_loss=400.0), _acct())
    assert d.allowed is False
    assert "exposition" in d.reason


def test_buying_power_insufficient():
    d = check_order(_order(collateral=250_000.0, max_loss=400.0), _acct())
    assert d.allowed is False
    assert "buying power" in d.reason.lower()


def test_cash_buffer_floor():
    # buffer mini = 20 % de 100k = 20 000 ; cash 100k, collatéral 85k -> reste 15k < 20k
    d = check_order(_order(collateral=85_000.0, max_loss=400.0), _acct())
    assert d.allowed is False
    assert "buffer de cash" in d.reason


# --- options : échéance & liquidité --------------------------------
def test_dte_too_short():
    d = check_order(_order(dte=2), _acct())
    assert d.allowed is False
    assert "DTE" in d.reason


def test_dte_too_long():
    d = check_order(_order(dte=60), _acct())
    assert d.allowed is False
    assert "DTE" in d.reason


def test_illiquid_spread_rejected():
    d = check_order(_order(spread_pct=0.15), _acct())
    assert d.allowed is False
    assert "illiquide" in d.reason


# --- ordre des contrôles ------------------------------------------------
def test_kill_switch_takes_priority_over_other_failures():
    # ordre par ailleurs invalide (DTE) mais kill-switch d'abord
    d = check_order(_order(dte=99), _acct(equity=90_000.0))
    assert d.allowed is False
    assert "perte du jour" in d.reason


# --- profils de risque ------------------------------------------------
def test_profiles_are_ordered_from_tight_to_loose():
    c, b, a = PROFILES["conservative"], PROFILES["balanced"], PROFILES["aggressive"]
    assert c.max_positions < b.max_positions < a.max_positions
    assert c.max_order_loss < b.max_order_loss < a.max_order_loss
    assert c.daily_max_loss_pct < b.daily_max_loss_pct < a.daily_max_loss_pct
    assert c.min_cash_buffer_pct > b.min_cash_buffer_pct > a.min_cash_buffer_pct


def test_aggressive_profile_allows_more_positions():
    order = _order()
    acct = _acct(open_positions=6)  # dépasse le conservateur (5), pas l'agressif (15)
    assert check_order(order, acct, risk_params("conservative")).allowed is False
    assert check_order(order, acct, risk_params("aggressive")).allowed is True


def test_unknown_profile_falls_back_to_conservative():
    assert risk_params("n_importe_quoi") is PROFILES["conservative"]
