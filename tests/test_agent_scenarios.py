"""Scénarios : face à une situation donnée, l'agent décide-t-il bien ?

Le broker est faux (`tests/fake_broker.FakeBroker`), la logique testée est celle de
`src.spread_agent.SpreadAgent` + `src.risk_guard`. La vue par symbole porte un
**régime de marché** ("bullish" / "bearish" / "neutral"), pas un croisement ponctuel.
"""
from src.spread_agent import SpreadAgent, SpreadConfig, SymbolView
from tests.fake_broker import FakeBroker

# config permissive sur le rapport crédit/largeur : on veut tester la logique de
# décision, pas le niveau de prime d'un spread hebdo 30-delta.
OPEN_CFG = SpreadConfig(min_credit_ratio=0.0)


def _broker(**over) -> FakeBroker:
    b = FakeBroker(spot={"AAPL": 100.0, "MSFT": 100.0, "SPY": 100.0})
    for k, v in over.items():
        setattr(b, k, v)
    return b


# --- 1. ouverture nominale selon le régime ---------------------------
def test_bullish_regime_flat_opens_bull_put():
    agent = SpreadAgent(_broker(), config=OPEN_CFG)
    (d,) = agent.decide([SymbolView("AAPL", "bullish")])
    assert d.action == "open"
    assert d.quote.kind == "bull_put"
    assert d.risk.allowed


def test_bearish_regime_flat_opens_bear_call():
    agent = SpreadAgent(_broker(), config=OPEN_CFG)
    (d,) = agent.decide([SymbolView("AAPL", "bearish")])
    assert d.action == "open"
    assert d.quote.kind == "bear_call"


def test_neutral_regime_flat_opens_iron_condor():
    agent = SpreadAgent(_broker(), config=OPEN_CFG)
    (d,) = agent.decide([SymbolView("AAPL", "neutral")])
    assert d.action == "open"
    assert d.quote.kind == "iron_condor"


# --- 2. déjà en position -> on ne ré-ouvre pas -------------------------
def test_same_regime_already_held_does_not_reopen():
    b = _broker()
    b.add_position("AAPL", kind="bull_put", entry_credit=1.0, current_value=0.9, dte=5)
    agent = SpreadAgent(b, config=OPEN_CFG)
    (d,) = agent.decide([SymbolView("AAPL", "bullish")])
    assert d.action == "hold"
    assert "conservée" in d.reason


# --- 3. régime inversé -> clôture -----------------------------------
def test_reversed_regime_closes_position():
    b = _broker()
    b.add_position("AAPL", kind="bull_put", entry_credit=1.0, current_value=1.0, dte=5)
    agent = SpreadAgent(b)
    (d,) = agent.decide([SymbolView("AAPL", "bearish")])
    assert d.action == "close"
    assert "inversé" in d.reason


def test_condor_rides_through_regime_change():
    # l'iron condor est une position de revenu non directionnelle : on ne la fait
    # pas tourner à chaque oscillation du régime (whipsaw coûteux), on la laisse
    # courir vers la prise de profit ou l'échéance.
    b = _broker()
    b.add_position("AAPL", kind="iron_condor", entry_credit=1.0, current_value=1.0, dte=5)
    agent = SpreadAgent(b)
    (d,) = agent.decide([SymbolView("AAPL", "bullish")])
    assert d.action == "hold"


# --- 4. kill-switch perte du jour -> aucune ouverture ----------------
def test_daily_loss_kill_switch_blocks_open():
    b = _broker(equity=80_000.0, start_of_day_equity=100_000.0)  # -20 %
    agent = SpreadAgent(b, config=OPEN_CFG)
    (d,) = agent.decide([SymbolView("AAPL", "bullish")])
    assert d.action == "hold"
    assert "risk_guard" in d.reason and "perte du jour" in d.reason


# --- 5. buying power insuffisant -----------------------------------
def test_insufficient_buying_power_blocks_open():
    b = _broker(buying_power=100.0)
    agent = SpreadAgent(b, config=OPEN_CFG)
    (d,) = agent.decide([SymbolView("AAPL", "bullish")])
    assert d.action == "hold"
    assert "buying power" in d.reason.lower()


# --- 6. chaîne d'options indisponible -----------------------------
def test_no_option_chain_holds():
    b = _broker(chain_available=False)
    agent = SpreadAgent(b, config=OPEN_CFG)
    (d,) = agent.decide([SymbolView("AAPL", "bullish")])
    assert d.action == "hold"
    assert "cotable" in d.reason


# --- 7. plafond de positions atteint (profil conservateur : 5) ----
def test_max_positions_cap_blocks_open():
    b = _broker()
    for sym in ("A", "B", "C", "D", "E"):
        b.spot[sym] = 100.0
        b.add_position(sym, dte=5)
    agent = SpreadAgent(b, config=OPEN_CFG)  # RiskParams() par défaut -> max 5
    (d,) = agent.decide([SymbolView("AAPL", "bullish")])
    assert d.action == "hold"
    assert "plafond" in d.reason


# --- 8. spread trop peu rémunérateur (config par défaut) ---------
def test_low_credit_ratio_holds():
    b = _broker()
    agent = SpreadAgent(b, config=SpreadConfig(width=50.0))  # largeur énorme -> ratio faible
    (d,) = agent.decide([SymbolView("AAPL", "bullish")])
    assert d.action == "hold"
    assert "trop peu payé" in d.reason


# --- 9. veto de l'agent analyste --------------------------------
def test_analyst_veto_holds():
    agent = SpreadAgent(_broker(), config=OPEN_CFG)
    (d,) = agent.decide(
        [SymbolView("AAPL", "bullish", analyst_ok=False, analyst_note="sentiment news négatif")]
    )
    assert d.action == "hold"
    assert "analyste" in d.reason


# --- gestion des positions : prise de profit / stop / échéance --
def test_take_profit_closes():
    b = _broker()
    b.add_position("AAPL", entry_credit=1.0, current_value=0.4, dte=5)  # P&L +60 >= 50 % du crédit
    agent = SpreadAgent(b)
    (d,) = agent.decide([SymbolView("AAPL", "bullish")])
    assert d.action == "close"
    assert "prise de profit" in d.reason


def test_stop_loss_closes():
    b = _broker()
    b.add_position("AAPL", entry_credit=1.0, current_value=3.2, dte=5)  # P&L -220 <= -2x crédit
    agent = SpreadAgent(b)
    (d,) = agent.decide([SymbolView("AAPL", "bullish")])
    assert d.action == "close"
    assert "stop" in d.reason


def test_close_near_expiry():
    b = _broker()
    b.add_position("AAPL", entry_credit=1.0, current_value=1.0, dte=1)
    agent = SpreadAgent(b)
    (d,) = agent.decide([SymbolView("AAPL", "bullish")])
    assert d.action == "close"
    assert "échéance" in d.reason


def test_hold_when_nothing_triggers():
    b = _broker()
    b.add_position("AAPL", entry_credit=1.0, current_value=0.9, dte=5)
    agent = SpreadAgent(b)
    (d,) = agent.decide([SymbolView("AAPL", "bullish")])
    assert d.action == "hold"


# --- multi-symboles en une passe -------------------------------
def test_multiple_symbols_mixed_decisions():
    b = _broker()
    b.add_position("MSFT", kind="bull_put", entry_credit=1.0, current_value=0.3, dte=4)  # -> close (TP)
    agent = SpreadAgent(b, config=OPEN_CFG)
    out = agent.decide([SymbolView("AAPL", "bullish"), SymbolView("MSFT", "bullish")])
    by_sym = {d.symbol: d for d in out}
    assert by_sym["AAPL"].action == "open"
    assert by_sym["MSFT"].action == "close"
