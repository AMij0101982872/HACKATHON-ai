"""Poche directionnelle — ACHÈTE des debit spreads sur régime franchement marqué.

Vise l'upside P&L que la vente de prime (src/spread_agent.py) ne donne pas. Séparée
du moteur de crédit : elle ne gère QUE ses propres positions (`strategy == "debit"`)
et n'ouvre jamais sur un sous-jacent qui porte déjà une position. Toute ouverture
passe par `risk_guard.check_order` **et** un plafond de budget de poche (% de l'equity).
"""
from __future__ import annotations

from dataclasses import dataclass

from src.risk_guard import AccountState, ProposedOrder, RiskParams, check_order
from src.spread_agent import AccountSnapshot, Broker, Decision, SymbolView

_OPP_REGIME = {"call_debit": "bearish", "put_debit": "bullish"}
_DIR_FOR_REGIME = {"bullish": "call", "bearish": "put"}


@dataclass(frozen=True)
class PocketConfig:
    strong_gap: float = 0.06        # |écart MM courte/longue| minimal pour un régime "marqué"
    target_delta: float = 0.45      # jambe longue proche de la monnaie
    width: float = 5.0
    dte: int = 14
    max_pocket_pct: float = 0.15    # débit total de la poche <= 15 % de l'equity
    take_profit_pct: float = 1.0    # clôture si P&L latent >= 100 % du débit payé
    stop_loss_pct: float = 0.5      # clôture si perte latente >= 50 % du débit payé
    close_dte: int = 1
    max_concurrent: int = 3


class DirectionalPocket:
    def __init__(
        self,
        broker: Broker,
        risk_params: RiskParams | None = None,
        config: PocketConfig | None = None,
    ) -> None:
        self.broker = broker
        self.risk_params = risk_params or RiskParams()
        self.cfg = config or PocketConfig()

    # -- API publique ------------------------------------------------
    def decide(self, views: list[SymbolView]) -> list[Decision]:
        acct = self.broker.account()
        all_pos = self.broker.open_spreads()
        mine = {p.symbol: p for p in all_pos if p.strategy == "debit"}
        other_symbols = {p.symbol for p in all_pos if p.strategy != "debit"}
        open_count = len(all_pos)
        budget_used = sum(p.entry_credit * 100.0 * p.contracts for p in mine.values())

        out: list[Decision] = []
        for v in views:
            held = mine.get(v.symbol)
            if held is not None:
                out.append(self._manage(held, v))
            elif v.symbol in other_symbols:
                out.append(Decision(v.symbol, "hold", "poche : déjà une position sur ce sous-jacent"))
            else:
                out.append(self._maybe_open(v, acct, open_count, len(mine), budget_used))
        return out

    # -- gestion ------------------------------------------------
    def _manage(self, pos, view: SymbolView) -> Decision:
        pl = pos.unrealized_pl
        debit_total = pos.entry_credit * 100.0 * pos.contracts

        if debit_total > 0 and pl >= self.cfg.take_profit_pct * debit_total:
            return Decision(pos.symbol, "close", f"poche : prise de profit (P&L {pl:+.0f}$)")
        if debit_total > 0 and pl <= -self.cfg.stop_loss_pct * debit_total:
            return Decision(pos.symbol, "close", f"poche : stop (P&L {pl:+.0f}$)")
        if pos.dte <= self.cfg.close_dte:
            return Decision(pos.symbol, "close", f"poche : échéance proche (DTE {pos.dte})")
        if view.regime == _OPP_REGIME.get(pos.kind):
            return Decision(pos.symbol, "close", f"poche : régime inversé ({view.regime})")
        return Decision(pos.symbol, "hold", f"poche : conservée (P&L {pl:+.0f}$, DTE {pos.dte})")

    # -- ouverture --------------------------------------------
    def _maybe_open(
        self, view: SymbolView, acct: AccountSnapshot,
        open_count: int, mine_count: int, budget_used: float,
    ) -> Decision:
        direction = _DIR_FOR_REGIME.get(view.regime)
        if direction is None:
            return Decision(view.symbol, "hold", "poche : régime neutre")
        if view.gap is None or abs(view.gap) < self.cfg.strong_gap:
            return Decision(
                view.symbol, "hold",
                f"poche : régime pas assez marqué (|écart| {abs(view.gap or 0.0):.1%} "
                f"< {self.cfg.strong_gap:.0%})",
            )
        if not view.analyst_ok:
            return Decision(view.symbol, "hold", f"poche : analyste écarte ({view.analyst_note})")
        if mine_count >= self.cfg.max_concurrent:
            return Decision(view.symbol, "hold", f"poche : plafond {self.cfg.max_concurrent} positions")

        quote = self.broker.quote_debit_spread(
            view.symbol, direction, self.cfg.target_delta, self.cfg.width, self.cfg.dte
        )
        if quote is None:
            return Decision(view.symbol, "hold", "poche : pas de spread cotable")

        debit_dollars = quote.credit * 100.0 * quote.contracts
        budget_cap = self.cfg.max_pocket_pct * acct.equity
        if budget_used + debit_dollars > budget_cap:
            return Decision(
                view.symbol, "hold",
                f"poche : budget dépassé ({budget_used + debit_dollars:.0f}$ > {budget_cap:.0f}$)",
                quote=quote,
            )

        order = ProposedOrder(
            symbol=view.symbol,
            action="open",
            max_loss=quote.max_loss,
            collateral=debit_dollars,
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
            return Decision(view.symbol, "hold", f"poche : risk_guard : {rd.reason}", quote=quote, risk=rd)
        return Decision(
            view.symbol, "open",
            f"poche : ouverture {quote.kind} {view.symbol} (débit {quote.credit:.2f}/action)",
            quote=quote, risk=rd,
        )
