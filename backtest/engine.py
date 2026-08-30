"""Backtest de l'agent de spreads à crédit.

Rejoue *les vraies* fonctions de décision (`src.strategy`, `src.spread_agent`,
`src.risk_guard`) jour par jour sur l'historique de bougies journalières. Les prix
d'options sont modélisés (Black-Scholes, `src.pricing`) faute de données d'options
historiques gratuites.

Usage :
    python -m backtest.engine --start 2024-03-01 --end 2025-12-31
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import config
from src.pricing import (
    VerticalSpread,
    apply_slippage,
    price_vertical_credit,
    realized_vol,
    strike_for_delta,
)
from src.risk_guard import RiskParams, risk_params as risk_params_for_profile
from src.spread_agent import (
    AccountSnapshot,
    SpreadAgent,
    SpreadConfig,
    SpreadKind,
    SpreadPosition,
    SpreadQuote,
    SymbolView,
)
from src.strategy import sma_regime

Series = list[tuple[date, float]]
TRADING_DAYS = 252
OUT_JSON = Path("web/data/history.json")


@dataclass
class _Pos:
    symbol: str
    kind: SpreadKind
    short_strike: float
    long_strike: float
    width: float
    entry_credit: float  # par action, après slippage
    contracts: int
    open_date: date
    expiry_date: date
    open_reason: str

    def dte(self, today: date) -> int:
        return (self.expiry_date - today).days

    def _option_type(self) -> str:
        return "call" if self.kind == "bear_call" else "put"

    def mark(self, spot: float, vol: float, today: date) -> float:
        """Coût actuel pour racheter le spread, par action (>= 0)."""
        t = max(self.dte(today), 0) / 365.0
        spread = VerticalSpread(
            self._option_type(), self.short_strike, self.long_strike, self.contracts
        )
        return max(price_vertical_credit(spread, spot=spot, t=t, vol=vol), 0.0)


class BacktestBroker:
    """Implémente `src.spread_agent.Broker` + l'exécution des décisions."""

    def __init__(self, starting_equity: float, slippage_pct: float) -> None:
        self.cash = starting_equity
        self.high_water_mark = starting_equity
        self.start_of_day_equity = starting_equity
        self.slippage_pct = slippage_pct
        self.today: date | None = None
        self.spot: dict[str, float] = {}
        self.vol: dict[str, float] = {}
        self._positions: list[_Pos] = []
        self.closed_trades: list[dict] = []

    # -- marché du jour (poussé par le moteur) --------------------
    def set_day(self, d: date, spot: dict[str, float], vol: dict[str, float]) -> None:
        self.today = d
        self.spot = spot
        self.vol = vol

    # -- valorisation --------------------------------------------
    def _mark(self, p: _Pos) -> float:
        return p.mark(self.spot.get(p.symbol, p.short_strike), self.vol.get(p.symbol, 0.25), self.today)

    def equity(self) -> float:
        liab = sum(self._mark(p) * 100.0 * p.contracts for p in self._positions)
        return self.cash - liab

    # -- interface Broker --------------------------------------
    def account(self) -> AccountSnapshot:
        eq = self.equity()
        return AccountSnapshot(
            equity=eq,
            cash=self.cash,
            buying_power=max(eq, 0.0) * 2.0,
            start_of_day_equity=self.start_of_day_equity,
            high_water_mark=self.high_water_mark,
        )

    def open_spreads(self) -> list[SpreadPosition]:
        return [
            SpreadPosition(
                symbol=p.symbol,
                kind=p.kind,
                entry_credit=p.entry_credit,
                current_value=self._mark(p),
                dte=max(p.dte(self.today), 0),
                contracts=p.contracts,
            )
            for p in self._positions
        ]

    def quote_credit_spread(
        self, symbol: str, kind: SpreadKind, target_delta: float, width: float, dte: int
    ) -> SpreadQuote | None:
        spot = self.spot.get(symbol)
        vol = self.vol.get(symbol)
        if not spot or spot <= 0 or not vol:
            return None
        t = dte / 365.0
        if kind == "bear_call":
            short = strike_for_delta(spot, target_delta, t, vol, "call")
            spread = VerticalSpread("call", short, short + width)
        else:  # bull_put ou iron_condor -> jambe put pour le dimensionnement
            short = strike_for_delta(spot, target_delta, t, vol, "put")
            spread = VerticalSpread("put", short, short - width)
        credit = apply_slippage(price_vertical_credit(spread, spot=spot, t=t, vol=vol), self.slippage_pct)
        return SpreadQuote(
            symbol=symbol,
            kind=kind,
            short_strike=spread.short_strike,
            long_strike=spread.long_strike,
            credit=credit,
            max_loss=spread.max_loss(credit),
            collateral=width * 100.0,
            dte=dte,
            spread_pct=self.slippage_pct,
        )

    # -- exécution -------------------------------------------
    def execute_open(self, q: SpreadQuote, reason: str) -> None:
        self._positions.append(
            _Pos(
                symbol=q.symbol,
                kind=q.kind,
                short_strike=q.short_strike,
                long_strike=q.long_strike,
                width=q.width,
                entry_credit=q.credit,
                contracts=q.contracts,
                open_date=self.today,
                expiry_date=self.today + timedelta(days=q.dte),
                open_reason=reason,
            )
        )
        self.cash += q.credit * 100.0 * q.contracts

    def execute_close(self, symbol: str, reason: str) -> None:
        for p in list(self._positions):
            if p.symbol != symbol:
                continue
            cost = self._mark(p) * 100.0 * p.contracts
            self.cash -= cost
            credit_total = p.entry_credit * 100.0 * p.contracts
            self.closed_trades.append(
                {
                    "symbol": p.symbol,
                    "kind": p.kind,
                    "open_date": p.open_date.isoformat(),
                    "close_date": self.today.isoformat(),
                    "days_held": (self.today - p.open_date).days,
                    "entry_credit": round(credit_total, 2),
                    "exit_cost": round(cost, 2),
                    "pl": round(credit_total - cost, 2),
                    "open_reason": p.open_reason,
                    "close_reason": reason,
                }
            )
            self._positions.remove(p)

    def eod(self) -> None:
        self.high_water_mark = max(self.high_water_mark, self.equity())


# --------------------------------------------------------------------------
def run_backtest(
    symbols: list[str],
    start: str,
    end: str,
    *,
    spread_cfg: SpreadConfig | None = None,
    risk_params: RiskParams | None = None,
    fast_ma: int = None,
    slow_ma: int = None,
    regime_threshold: float = None,
    starting_equity: float = 100_000.0,
    slippage_pct: float = None,
    vol_window: int = 20,
    data: dict[str, Series] | None = None,
) -> dict:
    """Exécute le backtest et renvoie un dict (courbe d'equity, trades, décisions, métriques)."""
    spread_cfg = spread_cfg or SpreadConfig(
        target_delta=config.TARGET_DELTA,
        width=config.SPREAD_WIDTH,
        dte=config.DTE_TARGET,
        take_profit_pct=config.TAKE_PROFIT_PCT,
        stop_loss_mult=config.STOP_LOSS_MULT,
    )
    risk_params = risk_params or risk_params_for_profile(config.RISK_PROFILE)
    fast_ma = fast_ma or config.FAST_MA
    slow_ma = slow_ma or config.SLOW_MA
    regime_threshold = config.REGIME_THRESHOLD if regime_threshold is None else regime_threshold
    slippage_pct = config.SLIPPAGE_PCT if slippage_pct is None else slippage_pct

    if data is None:
        from backtest.data import load_daily_closes

        data = load_daily_closes(symbols, start, end)

    data = {s: rows for s, rows in data.items() if rows}
    symbols = [s for s in symbols if s in data]
    if not symbols:
        raise SystemExit("Aucune donnée pour les symboles demandés.")

    all_dates = sorted({d for rows in data.values() for d, _ in rows})
    broker = BacktestBroker(starting_equity, slippage_pct)
    agent = SpreadAgent(broker, risk_params, spread_cfg)

    equity_curve: list[dict] = []
    decisions_log: list[dict] = []

    for d in all_dates:
        spot: dict[str, float] = {}
        vol: dict[str, float] = {}
        views: list[SymbolView] = []
        for s in symbols:
            hist = [c for (dd, c) in data[s] if dd <= d]
            if not hist:
                continue
            spot[s] = hist[-1]
            vol[s] = realized_vol(hist, vol_window)
            if len(hist) >= slow_ma + 1:
                views.append(SymbolView(s, sma_regime(hist, fast_ma, slow_ma, regime_threshold)))

        broker.set_day(d, spot, vol)
        broker.start_of_day_equity = broker.equity()

        # règlement des positions arrivées à échéance (filet de sécurité week-ends/gaps)
        for p in list(broker._positions):
            if p.dte(d) <= 0:
                broker.execute_close(p.symbol, "échéance atteinte (règlement)")

        for dec in agent.decide(views):
            decisions_log.append(
                {"date": d.isoformat(), "symbol": dec.symbol, "action": dec.action, "reason": dec.reason}
            )
            if dec.action == "open" and dec.quote is not None:
                broker.execute_open(dec.quote, dec.reason)
            elif dec.action == "close":
                broker.execute_close(dec.symbol, dec.reason)

        broker.eod()
        equity_curve.append(
            {
                "date": d.isoformat(),
                "equity": round(broker.equity(), 2),
                "cash": round(broker.cash, 2),
                "positions": len(broker._positions),
            }
        )

    # clôture de tout ce qui reste ouvert
    if all_dates:
        for p in list(broker._positions):
            broker.execute_close(p.symbol, "fin de backtest")
        if equity_curve:
            equity_curve[-1]["equity"] = round(broker.equity(), 2)
            equity_curve[-1]["cash"] = round(broker.cash, 2)
            equity_curve[-1]["positions"] = 0

    metrics = _metrics(equity_curve, broker.closed_trades, starting_equity)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start": start,
        "end": end,
        "params": {
            "symbols": symbols,
            "risk_profile": config.RISK_PROFILE,
            "fast_ma": fast_ma,
            "slow_ma": slow_ma,
            "regime_threshold": regime_threshold,
            "target_delta": spread_cfg.target_delta,
            "spread_width": spread_cfg.width,
            "dte": spread_cfg.dte,
            "take_profit_pct": spread_cfg.take_profit_pct,
            "stop_loss_mult": spread_cfg.stop_loss_mult,
            "slippage_pct": slippage_pct,
            "starting_equity": starting_equity,
        },
        "metrics": metrics,
        "equity_curve": equity_curve,
        "trades": broker.closed_trades,
        "decisions": decisions_log,
    }


def _metrics(equity_curve: list[dict], trades: list[dict], starting_equity: float) -> dict:
    if not equity_curve:
        return {}
    eqs = [row["equity"] for row in equity_curve]
    end = eqs[-1]

    peak = eqs[0]
    max_dd = 0.0
    for e in eqs:
        peak = max(peak, e)
        if peak > 0:
            max_dd = min(max_dd, e / peak - 1.0)

    rets = [eqs[i] / eqs[i - 1] - 1.0 for i in range(1, len(eqs)) if eqs[i - 1] > 0]
    if len(rets) > 1 and statistics.pstdev(rets) > 0:
        sharpe = statistics.fmean(rets) / statistics.pstdev(rets) * math.sqrt(TRADING_DAYS)
    else:
        sharpe = 0.0

    pls = [t["pl"] for t in trades]
    wins = [x for x in pls if x > 0]
    return {
        "total_return_pct": round((end / starting_equity - 1.0) * 100.0, 2),
        "final_equity": round(end, 2),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "sharpe_annualized": round(sharpe, 2),
        "num_trades": len(trades),
        "win_rate_pct": round(len(wins) / len(pls) * 100.0, 1) if pls else 0.0,
        "avg_trade_pl": round(statistics.fmean(pls), 2) if pls else 0.0,
        "total_realized_pl": round(sum(pls), 2),
        "avg_days_held": round(statistics.fmean([t["days_held"] for t in trades]), 1) if trades else 0.0,
    }


def _print_report(result: dict) -> None:
    m = result["metrics"]
    print(f"\n=== Backtest {result['start']} -> {result['end']} ===")
    print(f"Symboles     : {', '.join(result['params']['symbols'])}")
    print(f"Equity finale: ${m['final_equity']:,.2f}  ({m['total_return_pct']:+.2f} %)")
    print(f"Max drawdown : {m['max_drawdown_pct']:.2f} %")
    print(f"Sharpe (ann.): {m['sharpe_annualized']:.2f}")
    print(f"Trades       : {m['num_trades']}  | win rate {m['win_rate_pct']:.1f} %  | "
          f"P&L moyen ${m['avg_trade_pl']:,.2f}  | durée moy. {m['avg_days_held']:.1f} j")
    print(f"P&L réalisé  : ${m['total_realized_pl']:,.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest de l'agent de spreads à crédit")
    ap.add_argument("--symbols", default=",".join(config.OPTION_UNIVERSE))
    ap.add_argument("--start", default="2024-03-01")
    ap.add_argument("--end", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--out", default=str(OUT_JSON), help="chemin du JSON de sortie")
    args = ap.parse_args()

    result = run_backtest(
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()],
        args.start,
        args.end,
        starting_equity=args.equity,
    )
    _print_report(result)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nJSON écrit : {out}")


if __name__ == "__main__":
    main()
