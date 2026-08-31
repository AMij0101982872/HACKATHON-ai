"""Scénarios de la poche directionnelle (achat de debit spreads)."""
from src.directional_pocket import DirectionalPocket, PocketConfig
from src.spread_agent import SymbolView
from tests.fake_broker import FakeBroker

STRONG = 0.08  # > strong_gap par défaut (0.06)


def _broker(**over) -> FakeBroker:
    b = FakeBroker(spot={"AAPL": 100.0, "MSFT": 100.0, "NVDA": 100.0, "AMD": 100.0, "SPY": 100.0})
    for k, v in over.items():
        setattr(b, k, v)
    return b


# --- ouverture -----------------------------------------------------
def test_strong_bullish_opens_call_debit():
    p = DirectionalPocket(_broker())
    (d,) = p.decide([SymbolView("AAPL", "bullish", gap=STRONG)])
    assert d.action == "open"
    assert d.quote.kind == "call_debit"
    assert d.quote.strategy == "debit"
    assert d.risk.allowed


def test_strong_bearish_opens_put_debit():
    p = DirectionalPocket(_broker())
    (d,) = p.decide([SymbolView("AAPL", "bearish", gap=-STRONG)])
    assert d.action == "open"
    assert d.quote.kind == "put_debit"


def test_neutral_regime_holds():
    p = DirectionalPocket(_broker())
    (d,) = p.decide([SymbolView("AAPL", "neutral", gap=0.0)])
    assert d.action == "hold"
    assert "neutre" in d.reason


def test_weak_regime_holds():
    p = DirectionalPocket(_broker())
    (d,) = p.decide([SymbolView("AAPL", "bullish", gap=0.02)])
    assert d.action == "hold"
    assert "pas assez marqué" in d.reason


def test_missing_gap_holds():
    p = DirectionalPocket(_broker())
    (d,) = p.decide([SymbolView("AAPL", "bullish", gap=None)])
    assert d.action == "hold"


def test_analyst_veto_holds():
    p = DirectionalPocket(_broker())
    (d,) = p.decide([SymbolView("AAPL", "bullish", gap=STRONG, analyst_ok=False, analyst_note="risque earnings")])
    assert d.action == "hold"
    assert "analyste" in d.reason


def test_skips_symbol_with_existing_credit_position():
    b = _broker()
    b.add_position("AAPL", kind="bull_put", strategy="credit", dte=6)
    p = DirectionalPocket(b)
    (d,) = p.decide([SymbolView("AAPL", "bullish", gap=STRONG)])
    assert d.action == "hold"
    assert "déjà une position" in d.reason


def test_budget_cap_blocks_open():
    p = DirectionalPocket(_broker(), config=PocketConfig(max_pocket_pct=0.0005))  # cap ~50$
    (d,) = p.decide([SymbolView("AAPL", "bullish", gap=STRONG)])
    assert d.action == "hold"
    assert "budget dépassé" in d.reason


def test_max_concurrent_blocks_open():
    b = _broker()
    for s in ("MSFT", "NVDA", "AMD"):
        b.add_position(s, kind="call_debit", strategy="debit", entry_credit=2.0, current_value=2.0, dte=10)
    p = DirectionalPocket(b)
    (d,) = p.decide([SymbolView("AAPL", "bullish", gap=STRONG)])
    assert d.action == "hold"
    assert "plafond" in d.reason


def test_no_chain_holds():
    p = DirectionalPocket(_broker(chain_available=False))
    (d,) = p.decide([SymbolView("AAPL", "bullish", gap=STRONG)])
    assert d.action == "hold"
    assert "cotable" in d.reason


# --- gestion -----------------------------------------------------
def _held(b, **over):
    kw = dict(symbol="AAPL", kind="call_debit", strategy="debit",
              entry_credit=2.0, current_value=2.0, dte=8)
    kw.update(over)
    b.add_position(**kw)


def test_take_profit_closes():
    b = _broker()
    _held(b, current_value=4.1)  # P&L +210 >= 100 % du débit (200)
    p = DirectionalPocket(b)
    (d,) = p.decide([SymbolView("AAPL", "bullish", gap=STRONG)])
    assert d.action == "close"
    assert "prise de profit" in d.reason


def test_stop_closes():
    b = _broker()
    _held(b, current_value=0.9)  # P&L -110 <= -50 % du débit (-100)
    p = DirectionalPocket(b)
    (d,) = p.decide([SymbolView("AAPL", "bullish", gap=STRONG)])
    assert d.action == "close"
    assert "stop" in d.reason


def test_close_near_expiry():
    b = _broker()
    _held(b, current_value=2.0, dte=1)
    p = DirectionalPocket(b)
    (d,) = p.decide([SymbolView("AAPL", "bullish", gap=STRONG)])
    assert d.action == "close"
    assert "échéance" in d.reason


def test_regime_flip_closes():
    b = _broker()
    _held(b, current_value=2.1, dte=8)
    p = DirectionalPocket(b)
    (d,) = p.decide([SymbolView("AAPL", "bearish", gap=-STRONG)])
    assert d.action == "close"
    assert "inversé" in d.reason


def test_hold_when_nothing_triggers():
    b = _broker()
    _held(b, current_value=2.2, dte=8)
    p = DirectionalPocket(b)
    (d,) = p.decide([SymbolView("AAPL", "bullish", gap=STRONG)])
    assert d.action == "hold"
