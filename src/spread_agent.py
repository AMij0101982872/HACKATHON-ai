"""Moteur déterministe : décide d'ouvrir / gérer / clôturer des spreads verticaux à crédit.

Ne fait AUCUNE I/O : reçoit un `broker` injecté (vrai ou faux) + une vue par symbole
(signal technique + avis de l'agent analyste), et renvoie une liste de décisions.
Toute ouverture passe par `risk_guard.check_order`.

Le "runner" (CLI Alpaca en cron) est responsable d'exécuter les décisions.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, Protocol

from src.risk_guard import AccountState, ProposedOrder, RiskDecision, RiskParams, check_order

SpreadKind = Literal["bull_put", "bear_call", "iron_condor", "call_debit", "put_debit"]
Regime = Literal["bullish", "bearish", "neutral"]
Action = Literal["open", "close", "hold"]
Strategy = Literal["credit", "debit"]


# --- Données échangées avec le broker -------------------------------------
@dataclass(frozen=True)
class AccountSnapshot:
    equity: float
    cash: float
    buying_power: float
    start_of_day_equity: float
    high_water_mark: float
    last_equity: float = 0.0   # equity de clôture de la veille (Alpaca) ; 0 si indisponible


@dataclass(frozen=True)
class SpreadPosition:
    """Position de spread ouverte, telle que rapportée par le broker."""

    symbol: str
    kind: SpreadKind
    entry_credit: float    # à l'ouverture, PAR ACTION : crédit reçu (credit) ou débit payé, signé (debit)
    current_value: float   # valeur actuelle pour DÉNOUER, PAR ACTION (>= 0)
    dte: int
    contracts: int = 1
    strategy: Strategy = "credit"
    broker_pl: float | None = None  # P&L latent réel fourni par le broker ($) ; prioritaire sur le calcul

    @property
    def unrealized_pl(self) -> float:
        """P&L latent en dollars.

        Utilise la valeur réelle du broker (`broker_pl`) si disponible — c'est elle
        qui doit coller au dashboard Alpaca. Sinon, reconstruction Black-Scholes :
        - credit : (crédit reçu − coût de rachat) × 100 × contrats
        - debit  : (valeur de revente − débit payé) × 100 × contrats
        """
        if self.broker_pl is not None:
            return self.broker_pl
        if self.strategy == "debit":
            return (self.current_value - self.entry_credit) * 100.0 * self.contracts
        return (self.entry_credit - self.current_value) * 100.0 * self.contracts


@dataclass(frozen=True)
class SpreadQuote:
    """Cotation d'un spread candidat à l'ouverture."""

    symbol: str
    kind: SpreadKind
    short_strike: float
    long_strike: float
    credit: float          # net encaissé PAR ACTION, après slippage
    max_loss: float        # perte maximale en dollars pour la structure entière
    collateral: float      # cash immobilisé en dollars
    dte: int
    spread_pct: float      # largeur bid-ask relative de la jambe courte (liquidité)
    contracts: int = 1
    short_symbol: str = ""  # symbole OCC de la jambe courte (broker réel)
    long_symbol: str = ""   # symbole OCC de la jambe longue (broker réel)
    expiry: str = ""        # date d'échéance ISO (broker réel)
    strategy: Strategy = "credit"  # "debit" -> `credit` porte le débit payé (>0)

    @property
    def width(self) -> float:
        return abs(self.short_strike - self.long_strike)

    @property
    def credit_ratio(self) -> float:
        """Crédit / largeur : mesure du rapport rendement/risque. 0 si largeur nulle."""
        return self.credit / self.width if self.width else 0.0


# --- Entrée de décision --------------------------------------------------
@dataclass(frozen=True)
class SymbolView:
    symbol: str
    regime: Regime            # sortie de src.strategy.sma_regime
    analyst_ok: bool = True   # feu vert de l'agent analyste LLM
    analyst_note: str = ""
    gap: float | None = None  # écart relatif MM courte/longue (src.strategy.sma_gap) ; sert à la poche directionnelle


@dataclass(frozen=True)
class Decision:
    symbol: str
    action: Action
    reason: str
    quote: SpreadQuote | None = None
    risk: RiskDecision | None = None


# --- Interface broker (implémentée par le vrai client et par le faux) ---
class Broker(Protocol):
    def account(self) -> AccountSnapshot: ...

    def open_spreads(self) -> list[SpreadPosition]: ...

    def quote_credit_spread(
        self, symbol: str, kind: SpreadKind, target_delta: float, width: float, dte: int
    ) -> SpreadQuote | None: ...

    def quote_debit_spread(
        self, symbol: str, direction: str, target_delta: float, width: float, dte: int
    ) -> SpreadQuote | None:
        """direction : 'call' (haussier) ou 'put' (baissier). Optionnel : seule la
        poche directionnelle l'appelle."""
        ...


# --- Config du moteur --------------------------------------------------
@dataclass(frozen=True)
class SpreadConfig:
    target_delta: float = 0.30
    width: float = 5.0
    dte: int = 7
    contracts: int = 1             # nombre de contrats par structure
    take_profit_pct: float = 0.50   # clôture si P&L latent >= 50 % du crédit encaissé
    stop_loss_mult: float = 2.0     # clôture si perte latente >= 2x le crédit encaissé
    close_dte: int = 1              # clôture systématique à <= 1 jour de l'échéance
    min_credit_ratio: float = 0.15  # on n'ouvre pas un spread au rapport rendement/risque trop faible


_KIND_FOR_REGIME: dict[Regime, SpreadKind] = {
    "bullish": "bull_put",
    "bearish": "bear_call",
    "neutral": "iron_condor",
}


class SpreadAgent:
    def __init__(
        self,
        broker: Broker,
        risk_params: RiskParams | None = None,
        config: SpreadConfig | None = None,
    ) -> None:
        self.broker = broker
        self.risk_params = risk_params or RiskParams()
        self.cfg = config or SpreadConfig()

    # -- API publique --------------------------------------------------
    def decide(self, views: list[SymbolView]) -> list[Decision]:
        acct = self.broker.account()
        all_positions = self.broker.open_spreads()
        # ce moteur ne gère QUE les spreads à crédit ; les positions "debit" sont
        # celles de la poche directionnelle (src/directional_pocket.py).
        credit_pos = {p.symbol: p for p in all_positions if p.strategy == "credit"}
        open_count = len(all_positions)  # le plafond de positions compte tout

        out: list[Decision] = []
        for view in views:
            held = credit_pos.get(view.symbol)
            if held is not None:
                out.append(self._manage(held, view))
            else:
                out.append(self._maybe_open(view, acct, open_count))
        return out

    # -- gestion d'une position ouverte -----------------------------
    def _manage(self, pos: SpreadPosition, view: SymbolView) -> Decision:
        pl = pos.unrealized_pl
        credit_total = pos.entry_credit * 100.0 * pos.contracts

        if credit_total > 0 and pl >= self.cfg.take_profit_pct * credit_total:
            return Decision(
                pos.symbol, "close",
                f"prise de profit : P&L {pl:+.0f}$ >= {self.cfg.take_profit_pct:.0%} du crédit",
            )
        if credit_total > 0 and pl <= -self.cfg.stop_loss_mult * credit_total:
            return Decision(
                pos.symbol, "close",
                f"stop : P&L {pl:+.0f}$ <= -{self.cfg.stop_loss_mult:g}x le crédit",
            )
        if pos.dte <= self.cfg.close_dte:
            return Decision(pos.symbol, "close", f"échéance proche (DTE {pos.dte})")
        if _regime_reversed(pos.kind, view.regime):
            return Decision(pos.symbol, "close", f"régime inversé ({view.regime})")
        return Decision(pos.symbol, "hold", f"position conservée (P&L {pl:+.0f}$, DTE {pos.dte})")

    # -- ouverture éventuelle -------------------------------------
    def _maybe_open(self, view: SymbolView, acct: AccountSnapshot, open_count: int) -> Decision:
        if not view.analyst_ok:
            return Decision(view.symbol, "hold", f"analyste : écarté ({view.analyst_note or 'sans motif'})")

        kind = _KIND_FOR_REGIME[view.regime]

        quote = self.broker.quote_credit_spread(
            view.symbol, kind, self.cfg.target_delta, self.cfg.width, self.cfg.dte
        )
        if quote is None:
            return Decision(view.symbol, "hold", "pas de spread cotable (chaîne indisponible)")
        if quote.credit_ratio < self.cfg.min_credit_ratio:
            return Decision(
                view.symbol, "hold",
                f"rapport crédit/largeur {quote.credit_ratio:.0%} < "
                f"{self.cfg.min_credit_ratio:.0%} — trop peu payé",
                quote=quote,
            )

        n = max(self.cfg.contracts, 1)
        if n != 1:  # le broker cote 1 contrat ; on met à l'échelle risque + collatéral
            quote = replace(quote, contracts=n,
                            max_loss=quote.max_loss * n, collateral=quote.collateral * n)

        order = ProposedOrder(
            symbol=view.symbol,
            action="open",
            max_loss=quote.max_loss,
            collateral=quote.collateral,
            symbol_exposure=0.0,
            dte=quote.dte,
            spread_pct=quote.spread_pct,
            is_defined_risk=True,
        )
        state = AccountState(
            equity=acct.equity,
            cash=acct.cash,
            buying_power=acct.buying_power,
            start_of_day_equity=acct.start_of_day_equity,
            high_water_mark=acct.high_water_mark,
            open_positions=open_count,
        )
        rd = check_order(order, state, self.risk_params)
        if not rd.allowed:
            return Decision(view.symbol, "hold", f"risk_guard : {rd.reason}", quote=quote, risk=rd)
        return Decision(
            view.symbol, "open", f"ouverture {kind} {view.symbol} (crédit {quote.credit:.2f}/action)",
            quote=quote, risk=rd,
        )


def _regime_reversed(kind: SpreadKind, regime: Regime) -> bool:
    """True si le régime courant contredit la thèse *directionnelle* de la position.

    Les spreads directionnels (bull put / bear call) se clôturent quand le régime
    bascule à l'opposé. L'iron condor, lui, est une position de revenu *non
    directionnelle* : on le laisse courir vers sa prise de profit ou son échéance
    plutôt que de le faire tourner (whipsaw coûteux en slippage) à chaque
    oscillation du régime autour de zéro.
    """
    if kind == "bull_put":
        return regime == "bearish"
    if kind == "bear_call":
        return regime == "bullish"
    return False
