"""Risk guard déterministe.

Aucune I/O réseau, aucun LLM : cette brique reçoit *un ordre proposé* + *l'état du
compte* et répond AUTORISÉ ou REFUSÉ + raison. L'agent (moteur ou superviseur LLM)
ne peut jamais passer un ordre sans son feu vert.

Conçu pour être appelé à l'identique par l'agent live ET par le backtest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Action = Literal["open", "close"]


@dataclass(frozen=True)
class AccountState:
    """Photo du compte au moment de la décision (fournie par l'appelant, jamais fetchée ici)."""

    equity: float
    cash: float
    buying_power: float
    start_of_day_equity: float
    high_water_mark: float
    open_positions: int  # nombre de positions ouvertes, tous sous-jacents confondus


@dataclass(frozen=True)
class ProposedOrder:
    """Ordre que l'agent envisage de passer."""

    symbol: str
    action: Action
    max_loss: float = 0.0          # perte maximale de la structure (>= 0), ex. largeur_spread*100 - crédit
    collateral: float = 0.0        # cash immobilisé par la structure
    symbol_exposure: float = 0.0   # perte max déjà engagée sur ce sous-jacent (hors cet ordre)
    dte: int = 0                   # jours avant échéance de l'option
    spread_pct: float = 0.0        # (ask - bid) / mid de l'option, ex. 0.04 pour 4 %
    is_defined_risk: bool = True   # False = au moins une jambe nue -> refus systématique


@dataclass(frozen=True)
class RiskParams:
    max_positions: int = 5
    max_order_loss: float = 500.0          # perte max autorisée par structure ($)
    max_symbol_exposure_pct: float = 0.20  # part de l'equity engageable sur un seul sous-jacent
    min_cash_buffer_pct: float = 0.20      # cash à conserver après collatéral
    daily_max_loss_pct: float = 0.03       # kill-switch perte du jour vs equity d'ouverture
    max_drawdown_pct: float = 0.10         # kill-switch drawdown vs high-water mark
    dte_min: int = 5
    dte_max: int = 45
    max_spread_pct: float = 0.10           # liquidité : spread bid-ask max toléré


# Presets : `RiskParams()` seul = profil conservateur de référence.
PROFILES: dict[str, RiskParams] = {
    "conservative": RiskParams(),
    "balanced": RiskParams(
        max_positions=8,
        max_order_loss=1_500.0,
        max_symbol_exposure_pct=0.25,
        min_cash_buffer_pct=0.10,
        daily_max_loss_pct=0.08,
        max_drawdown_pct=0.15,
        dte_min=3,
        dte_max=45,
        max_spread_pct=0.12,
    ),
    "aggressive": RiskParams(
        max_positions=15,
        max_order_loss=4_000.0,
        max_symbol_exposure_pct=0.35,
        min_cash_buffer_pct=0.02,
        daily_max_loss_pct=0.15,
        max_drawdown_pct=0.30,
        dte_min=2,
        dte_max=60,
        max_spread_pct=0.15,
    ),
}


def risk_params(profile: str = "conservative") -> RiskParams:
    """RiskParams pour un profil nommé ('conservative' | 'balanced' | 'aggressive')."""
    return PROFILES.get(profile, PROFILES["conservative"])


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    checks: tuple[str, ...] = field(default_factory=tuple)  # garde-fous franchis, pour les logs


def check_order(
    order: ProposedOrder,
    acct: AccountState,
    params: RiskParams | None = None,
) -> RiskDecision:
    """Retourne la décision de risque pour `order` compte tenu de `acct`.

    Les clôtures sont toujours autorisées (elles réduisent le risque). Les ouvertures
    passent la batterie complète de garde-fous ; le premier qui échoue donne la raison.
    """
    p = params or RiskParams()
    passed: list[str] = []

    if order.action == "close":
        return RiskDecision(True, "clôture autorisée (réduction de risque)", ("close",))

    # --- Kill-switches globaux ------------------------------------------------
    if acct.start_of_day_equity > 0:
        daily_pl = (acct.equity - acct.start_of_day_equity) / acct.start_of_day_equity
        if daily_pl <= -p.daily_max_loss_pct:
            return RiskDecision(
                False,
                f"kill-switch : perte du jour {daily_pl:.1%} <= -{p.daily_max_loss_pct:.0%}",
                tuple(passed),
            )
        passed.append("daily_loss")

    if acct.high_water_mark > 0:
        drawdown = (acct.equity - acct.high_water_mark) / acct.high_water_mark
        if drawdown <= -p.max_drawdown_pct:
            return RiskDecision(
                False,
                f"kill-switch : drawdown {drawdown:.1%} <= -{p.max_drawdown_pct:.0%}",
                tuple(passed),
            )
        passed.append("drawdown")

    # --- Nature de l'ordre --------------------------------------------------
    if not order.is_defined_risk:
        return RiskDecision(False, "risque non défini (jambe nue) interdit", tuple(passed))
    passed.append("defined_risk")

    if acct.open_positions >= p.max_positions:
        return RiskDecision(
            False, f"plafond de {p.max_positions} positions ouvertes atteint", tuple(passed)
        )
    passed.append("max_positions")

    if order.max_loss > p.max_order_loss:
        return RiskDecision(
            False,
            f"perte max {order.max_loss:.0f}$ > plafond {p.max_order_loss:.0f}$ par structure",
            tuple(passed),
        )
    passed.append("max_order_loss")

    # --- Exposition / capital ---------------------------------------------
    exposure_cap = p.max_symbol_exposure_pct * acct.equity
    if order.symbol_exposure + order.max_loss > exposure_cap:
        return RiskDecision(
            False,
            f"exposition {order.symbol} "
            f"{order.symbol_exposure + order.max_loss:.0f}$ > "
            f"{p.max_symbol_exposure_pct:.0%} equity ({exposure_cap:.0f}$)",
            tuple(passed),
        )
    passed.append("symbol_exposure")

    if order.collateral > acct.buying_power:
        return RiskDecision(
            False,
            f"buying power insuffisant : collatéral {order.collateral:.0f}$ > "
            f"BP {acct.buying_power:.0f}$",
            tuple(passed),
        )
    passed.append("buying_power")

    cash_after = acct.cash - order.collateral
    min_cash = p.min_cash_buffer_pct * acct.equity
    if cash_after < min_cash:
        return RiskDecision(
            False,
            f"buffer de cash : {cash_after:.0f}$ après collatéral < "
            f"{p.min_cash_buffer_pct:.0%} equity ({min_cash:.0f}$)",
            tuple(passed),
        )
    passed.append("cash_buffer")

    # --- Options : échéance & liquidité ----------------------------------
    if not (p.dte_min <= order.dte <= p.dte_max):
        return RiskDecision(
            False,
            f"DTE {order.dte} hors fenêtre [{p.dte_min}, {p.dte_max}]",
            tuple(passed),
        )
    passed.append("dte")

    if order.spread_pct > p.max_spread_pct:
        return RiskDecision(
            False,
            f"illiquide : spread bid-ask {order.spread_pct:.1%} > {p.max_spread_pct:.0%}",
            tuple(passed),
        )
    passed.append("liquidity")

    return RiskDecision(True, "OK", tuple(passed))
