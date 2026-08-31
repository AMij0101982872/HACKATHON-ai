"""Faux broker pour les tests de scénarios et le backtest.

Implémente l'interface `src.spread_agent.Broker` avec un état 100 % scriptable et une
chaîne d'options synthétique cohérente (Black-Scholes via `src.pricing`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.pricing import (
    VerticalSpread,
    apply_slippage,
    price_vertical_credit,
    strike_for_delta,
)
from src.spread_agent import (
    AccountSnapshot,
    Broker,
    SpreadKind,
    SpreadPosition,
    SpreadQuote,
)


@dataclass
class FakeBroker(Broker):
    equity: float = 100_000.0
    cash: float = 100_000.0
    buying_power: float = 200_000.0
    start_of_day_equity: float = 100_000.0
    high_water_mark: float = 100_000.0

    positions: list[SpreadPosition] = field(default_factory=list)

    # marché synthétique
    spot: dict[str, float] = field(default_factory=dict)   # symbole -> cours
    vol: float = 0.25                                       # vol implicite supposée
    liquidity_spread_pct: float = 0.03                      # largeur bid-ask relative
    chain_available: bool = True                            # False -> quote_credit_spread renvoie None

    # -- interface Broker ------------------------------------------
    def account(self) -> AccountSnapshot:
        return AccountSnapshot(
            equity=self.equity,
            cash=self.cash,
            buying_power=self.buying_power,
            start_of_day_equity=self.start_of_day_equity,
            high_water_mark=self.high_water_mark,
        )

    def open_spreads(self) -> list[SpreadPosition]:
        return list(self.positions)

    def quote_credit_spread(
        self, symbol: str, kind: SpreadKind, target_delta: float, width: float, dte: int
    ) -> SpreadQuote | None:
        if not self.chain_available or symbol not in self.spot:
            return None

        spot = self.spot[symbol]
        t = dte / 365.0

        def _leg(otype: str, sign: int):
            short = strike_for_delta(spot, target_delta, t, self.vol, otype)
            sp = VerticalSpread(otype, short_strike=short, long_strike=short + sign * width)
            c = apply_slippage(price_vertical_credit(sp, spot=spot, t=t, vol=self.vol),
                               self.liquidity_spread_pct)
            return sp, c

        if kind == "bull_put":
            spread, credit = _leg("put", -1)
        elif kind == "bear_call":
            spread, credit = _leg("call", +1)
        else:  # iron_condor : vraies deux ailes
            put_sp, put_c = _leg("put", -1)
            call_sp, call_c = _leg("call", +1)
            total = put_c + call_c
            return SpreadQuote(
                symbol=symbol, kind="iron_condor",
                short_strike=put_sp.short_strike, long_strike=put_sp.long_strike,
                call_short_strike=call_sp.short_strike, call_long_strike=call_sp.long_strike,
                credit=total, put_credit=put_c, call_credit=call_c,
                max_loss=max(width * 100.0 - total * 100.0, 0.0),
                collateral=width * 100.0,
                dte=dte, spread_pct=self.liquidity_spread_pct,
            )

        return SpreadQuote(
            symbol=symbol,
            kind=kind,
            short_strike=spread.short_strike,
            long_strike=spread.long_strike,
            credit=credit,
            max_loss=spread.max_loss(credit),
            collateral=spread.width * 100.0,
            dte=dte,
            spread_pct=self.liquidity_spread_pct,
        )

    def quote_debit_spread(
        self, symbol: str, direction: str, target_delta: float, width: float, dte: int
    ) -> SpreadQuote | None:
        if not self.chain_available or symbol not in self.spot:
            return None
        spot = self.spot[symbol]
        t = dte / 365.0
        if direction == "call":
            long_k = strike_for_delta(spot, target_delta, t, self.vol, "call")
            spread = VerticalSpread("call", short_strike=long_k + width, long_strike=long_k)
        else:
            long_k = strike_for_delta(spot, target_delta, t, self.vol, "put")
            spread = VerticalSpread("put", short_strike=long_k - width, long_strike=long_k)

        # price_vertical_credit = prix(jambe courte) - prix(jambe longue) -> négatif ici
        debit = -price_vertical_credit(spread, spot=spot, t=t, vol=self.vol)
        debit *= 1.0 + self.liquidity_spread_pct / 2.0  # on paie au-dessus du mid
        return SpreadQuote(
            symbol=symbol,
            kind="call_debit" if direction == "call" else "put_debit",
            short_strike=spread.short_strike,
            long_strike=spread.long_strike,
            credit=round(debit, 2),
            max_loss=round(debit * 100.0, 2),
            collateral=round(debit * 100.0, 2),
            dte=dte,
            spread_pct=self.liquidity_spread_pct,
            strategy="debit",
        )

    # -- helpers de scénario -------------------------------------
    def add_position(
        self,
        symbol: str,
        kind: SpreadKind = "bull_put",
        entry_credit: float = 1.0,
        current_value: float = 1.0,
        dte: int = 5,
        contracts: int = 1,
        strategy: str = "credit",
    ) -> None:
        self.positions.append(
            SpreadPosition(
                symbol=symbol,
                kind=kind,
                entry_credit=entry_credit,
                current_value=current_value,
                dte=dte,
                contracts=contracts,
                strategy=strategy,
            )
        )
