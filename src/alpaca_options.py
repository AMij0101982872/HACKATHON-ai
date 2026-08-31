"""Broker options réel : implémente `src.spread_agent.Broker` contre l'API Alpaca (paper).

Portée volontairement réduite pour le hackathon : **spreads verticaux à crédit à 2
jambes** uniquement (ordres `mleg`). Le régime `neutral` est traité comme un
bull put spread (prime côté put, léger biais haussier) — cohérent avec le modèle du
backtest. L'iron condor 4 jambes est une amélioration ultérieure.

`dry_run=True` : les cotations et décisions sont réelles, **aucun ordre n'est envoyé**.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import (
    OptionLatestQuoteRequest,
    StockBarsRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    AssetStatus,
    ContractType,
    OrderClass,
    OrderSide,
    PositionIntent,
    TimeInForce,
)
from alpaca.trading.requests import GetOptionContractsRequest, LimitOrderRequest, OptionLegRequest

import config
from src.pricing import realized_vol, strike_for_delta
from src.spread_agent import (
    AccountSnapshot,
    SpreadKind,
    SpreadPosition,
    SpreadQuote,
)

STATE_FILE = Path("web/public/data/agent_state.json")
_OPT_TYPE: dict[SpreadKind, str] = {"bull_put": "put", "bear_call": "call", "iron_condor": "put"}


class AlpacaOptionsBroker:
    def __init__(self, *, dry_run: bool = True, slippage_pct: float | None = None) -> None:
        key, secret = config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY
        self.trading = TradingClient(key, secret, paper=True)
        self.stock_data = StockHistoricalDataClient(key, secret)
        self.opt_data = OptionHistoricalDataClient(key, secret)
        self.dry_run = dry_run
        self.slippage_pct = config.SLIPPAGE_PCT if slippage_pct is None else slippage_pct
        self.submitted: list[dict] = []  # trace des ordres (ou intentions en dry-run)

    # ------------------------------------------------------------------
    # Lecture
    # ------------------------------------------------------------------
    def account(self) -> AccountSnapshot:
        a = self.trading.get_account()
        equity = float(a.equity)
        sod, hwm = self._roll_state(equity)
        bp = float(getattr(a, "options_buying_power", None) or a.buying_power)
        return AccountSnapshot(
            equity=equity,
            cash=float(a.cash),
            buying_power=bp,
            start_of_day_equity=sod,
            high_water_mark=hwm,
            last_equity=float(getattr(a, "last_equity", 0.0) or 0.0),
        )

    def open_spreads(self) -> list[SpreadPosition]:
        legs = self._option_legs()
        by_under: dict[tuple[str, date, str], list[_Leg]] = {}
        for lg in legs:
            by_under.setdefault((lg.symbol_underlying, lg.expiry, lg.opt_type), []).append(lg)

        out: list[SpreadPosition] = []
        for (under, expiry, opt_type), grp in by_under.items():
            short = next((x for x in grp if x.qty < 0), None)
            long = next((x for x in grp if x.qty > 0), None)
            if not short or not long:
                continue
            contracts = max(int(min(abs(short.qty), abs(long.qty))), 1)

            # Débit vs crédit d'après l'ordre des strikes :
            #   put  : jambe longue plus haute  -> put debit  ; sinon bull put (crédit)
            #   call : jambe longue plus basse  -> call debit  ; sinon bear call (crédit)
            if opt_type == "put":
                is_debit = long.strike > short.strike
                kind: SpreadKind = "put_debit" if is_debit else "bull_put"
            else:
                is_debit = long.strike < short.strike
                kind = "call_debit" if is_debit else "bear_call"

            s_mid, l_mid = self._mid(short.occ), self._mid(long.occ)
            if is_debit:
                cur = max((l_mid - s_mid), 0.0)   # valeur de revente du debit spread
            else:
                cur = max((s_mid - l_mid), 0.0)   # coût de rachat du credit spread
            entry = self._entry_credit(short.occ, long.occ)
            broker_pl = sum(l.unrealized_pl for l in grp) or None  # somme des jambes = P&L Alpaca
            out.append(
                SpreadPosition(
                    symbol=under,
                    kind=kind,
                    entry_credit=entry if entry is not None else cur,
                    current_value=cur,
                    dte=max((expiry - date.today()).days, 0),
                    contracts=contracts,
                    strategy="debit" if is_debit else "credit",
                    broker_pl=broker_pl,
                )
            )
        return out

    def quote_credit_spread(
        self, symbol: str, kind: SpreadKind, target_delta: float, width: float, dte: int
    ) -> SpreadQuote | None:
        spot = self._spot(symbol)
        vol = self._vol(symbol)
        if not spot or not vol:
            return None
        opt_type = _OPT_TYPE[kind]

        contracts = self._contracts_in_window(symbol, opt_type, dte)
        if not contracts:
            return None
        expiry = min(contracts, key=lambda c: abs((c["expiry"] - date.today()).days - dte))["expiry"]
        strikes = sorted(c["strike"] for c in contracts if c["expiry"] == expiry)
        by_strike = {c["strike"]: c for c in contracts if c["expiry"] == expiry}

        t = max((expiry - date.today()).days, 1) / 365.0
        wanted = strike_for_delta(spot, target_delta, t, vol, opt_type)
        short_k = min(strikes, key=lambda k: abs(k - wanted))
        long_target = short_k - width if opt_type == "put" else short_k + width
        far = [k for k in strikes if (k < short_k) == (opt_type == "put")]
        if not far:
            return None
        long_k = min(far, key=lambda k: abs(k - long_target))

        short_c, long_c = by_strike[short_k], by_strike[long_k]
        sq = self._quote(short_c["occ"])
        lq = self._quote(long_c["occ"])
        if not sq or not lq:
            return None
        s_bid, s_ask = sq
        l_bid, l_ask = lq
        s_mid, l_mid = (s_bid + s_ask) / 2, (l_bid + l_ask) / 2
        credit = (s_mid - l_mid) * (1.0 - self.slippage_pct / 2.0)
        if credit <= 0:
            return None

        real_width = abs(short_k - long_k)
        spread_pct = (s_ask - s_bid) / s_mid if s_mid > 0 else 1.0
        return SpreadQuote(
            symbol=symbol,
            kind=kind,
            short_strike=short_k,
            long_strike=long_k,
            credit=round(credit, 2),
            max_loss=round(max(real_width - credit, 0.0) * 100.0, 2),
            collateral=round(real_width * 100.0, 2),
            dte=(expiry - date.today()).days,
            spread_pct=round(spread_pct, 4),
            short_symbol=short_c["occ"],
            long_symbol=long_c["occ"],
            expiry=expiry.isoformat(),
        )

    def quote_debit_spread(
        self, symbol: str, direction: str, target_delta: float, width: float, dte: int
    ) -> SpreadQuote | None:
        spot = self._spot(symbol)
        vol = self._vol(symbol)
        if not spot or not vol:
            return None
        opt_type = "call" if direction == "call" else "put"

        contracts = self._contracts_in_window(symbol, opt_type, dte)
        if not contracts:
            return None
        expiry = min(contracts, key=lambda c: abs((c["expiry"] - date.today()).days - dte))["expiry"]
        strikes = sorted(c["strike"] for c in contracts if c["expiry"] == expiry)
        by_strike = {c["strike"]: c for c in contracts if c["expiry"] == expiry}
        if not strikes:
            return None

        t = max((expiry - date.today()).days, 1) / 365.0
        wanted = strike_for_delta(spot, target_delta, t, vol, opt_type)  # jambe LONGUE (proche monnaie)
        long_k = min(strikes, key=lambda k: abs(k - wanted))
        short_target = long_k + width if opt_type == "call" else long_k - width
        far = [k for k in strikes if (k > long_k) == (opt_type == "call")]
        if not far:
            return None
        short_k = min(far, key=lambda k: abs(k - short_target))

        lq, sq = self._quote(by_strike[long_k]["occ"]), self._quote(by_strike[short_k]["occ"])
        if not lq or not sq:
            return None
        l_mid = (lq[0] + lq[1]) / 2.0
        s_mid = (sq[0] + sq[1]) / 2.0
        debit = (l_mid - s_mid) * (1.0 + self.slippage_pct / 2.0)  # on paie au-dessus du mid
        if debit <= 0:
            return None
        spread_pct = (lq[1] - lq[0]) / l_mid if l_mid > 0 else 1.0
        return SpreadQuote(
            symbol=symbol,
            kind="call_debit" if opt_type == "call" else "put_debit",
            short_strike=short_k,
            long_strike=long_k,
            credit=round(debit, 2),
            max_loss=round(debit * 100.0, 2),
            collateral=round(debit * 100.0, 2),
            dte=(expiry - date.today()).days,
            spread_pct=round(spread_pct, 4),
            short_symbol=by_strike[short_k]["occ"],
            long_symbol=by_strike[long_k]["occ"],
            expiry=expiry.isoformat(),
            strategy="debit",
        )

    # ------------------------------------------------------------------
    # Écriture
    # ------------------------------------------------------------------
    def execute_open(self, q: SpreadQuote, reason: str) -> dict:
        # credit : on VEND la jambe courte, on ACHÈTE la jambe longue (encaisse un crédit)
        # debit  : on ACHÈTE la jambe longue, on VEND la jambe courte (paie un débit)
        legs = [
            OptionLegRequest(symbol=q.short_symbol, ratio_qty=1, side=OrderSide.SELL,
                             position_intent=PositionIntent.SELL_TO_OPEN),
            OptionLegRequest(symbol=q.long_symbol, ratio_qty=1, side=OrderSide.BUY,
                             position_intent=PositionIntent.BUY_TO_OPEN),
        ]
        return self._submit(legs, limit_price=round(q.credit, 2),
                            tag=f"open {q.kind} {q.symbol}", reason=reason,
                            qty=max(int(q.contracts), 1), meta={"quote": q.__dict__})

    def execute_close(self, position: SpreadPosition, reason: str) -> dict | None:
        legs = self._legs_for_close(position.symbol)
        if not legs:
            return None
        return self._submit(legs, limit_price=None, tag=f"close {position.kind} {position.symbol}",
                            reason=reason, qty=max(int(position.contracts), 1), meta={})

    # ------------------------------------------------------------------
    # Interne
    # ------------------------------------------------------------------
    def _submit(self, legs, limit_price, tag: str, reason: str, meta: dict, qty: int = 1) -> dict:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tag": tag,
            "reason": reason,
            "qty": qty,
            "legs": [{"symbol": lg.symbol, "side": lg.side.value,
                      "intent": lg.position_intent.value} for lg in legs],
            "limit_price": limit_price,
            "dry_run": self.dry_run,
            **meta,
        }
        if self.dry_run:
            record["status"] = "SIMULATED"
            self.submitted.append(record)
            return record

        req = LimitOrderRequest(
            qty=max(int(qty), 1),
            order_class=OrderClass.MLEG,
            time_in_force=TimeInForce.DAY,
            legs=legs,
            limit_price=limit_price if limit_price is not None else 0.01,
        )
        order = self.trading.submit_order(req)
        record["status"] = str(order.status)
        record["order_id"] = str(order.id)
        self.submitted.append(record)
        return record

    def _legs_for_close(self, underlying: str) -> list[OptionLegRequest]:
        legs: list[OptionLegRequest] = []
        for lg in self._option_legs():
            if lg.symbol_underlying != underlying:
                continue
            if lg.qty < 0:
                legs.append(OptionLegRequest(symbol=lg.occ, ratio_qty=1, side=OrderSide.BUY,
                                             position_intent=PositionIntent.BUY_TO_CLOSE))
            else:
                legs.append(OptionLegRequest(symbol=lg.occ, ratio_qty=1, side=OrderSide.SELL,
                                             position_intent=PositionIntent.SELL_TO_CLOSE))
        return legs

    def _option_legs(self):
        """Positions options ouvertes, normalisées."""
        legs = []
        for p in self.trading.get_all_positions():
            ac = getattr(p, "asset_class", "")
            ac = getattr(ac, "value", ac)  # AssetClass.US_OPTION -> "us_option"
            if "option" not in str(ac).lower():
                continue
            occ = p.symbol
            info = _parse_occ(occ)
            if not info:
                continue
            under, expiry, opt_type, strike = info
            qty = float(p.qty)
            legs.append(_LegView(
                occ=occ, symbol_underlying=under, expiry=expiry,
                opt_type=opt_type, strike=strike, qty=qty,
                unrealized_pl=float(getattr(p, "unrealized_pl", 0.0) or 0.0),
                market_value=float(getattr(p, "market_value", 0.0) or 0.0),
            ))
        return legs

    def _contracts_in_window(self, underlying: str, opt_type: str, dte: int):
        lo = date.today() + timedelta(days=max(dte - 4, 1))
        hi = date.today() + timedelta(days=dte + 6)
        req = GetOptionContractsRequest(
            underlying_symbols=[underlying],
            status=AssetStatus.ACTIVE,
            expiration_date_gte=lo,
            expiration_date_lte=hi,
            type=ContractType.PUT if opt_type == "put" else ContractType.CALL,
            limit=1000,
        )
        res = self.trading.get_option_contracts(req)
        return [
            {"occ": c.symbol, "strike": float(c.strike_price),
             "expiry": c.expiration_date if isinstance(c.expiration_date, date)
             else date.fromisoformat(str(c.expiration_date))}
            for c in res.option_contracts
        ]

    def _quote(self, occ: str) -> tuple[float, float] | None:
        try:
            q = self.opt_data.get_option_latest_quote(OptionLatestQuoteRequest(symbol_or_symbols=occ))
            qq = q.get(occ)
            if not qq:
                return None
            bid, ask = float(qq.bid_price or 0), float(qq.ask_price or 0)
            if ask <= 0:
                return None
            return bid, ask
        except Exception:
            return None

    def _mid(self, occ: str) -> float:
        q = self._quote(occ)
        return (q[0] + q[1]) / 2.0 if q else 0.0

    def _spread_mid(self, short_occ: str, long_occ: str) -> float:
        return max(self._mid(short_occ) - self._mid(long_occ), 0.0)

    def _entry_credit(self, short_occ: str, long_occ: str) -> float | None:
        """Crédit d'entrée si on le retrouve dans l'état persistant, sinon None."""
        state = _load_state()
        return state.get("entry_credits", {}).get(f"{short_occ}|{long_occ}")

    def _spot(self, symbol: str) -> float | None:
        try:
            r = self.stock_data.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol))
            return float(r[symbol].price)
        except Exception:
            return None

    def _vol(self, symbol: str) -> float | None:
        try:
            start = datetime.now(timezone.utc) - timedelta(days=60)
            req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day, start=start)
            bars = self.stock_data.get_stock_bars(req).data.get(symbol, [])
            closes = [float(b.close) for b in bars]
            return realized_vol(closes, 20) if len(closes) >= 5 else 0.25
        except Exception:
            return 0.25

    def _roll_state(self, equity: float) -> tuple[float, float]:
        state = _load_state()
        today = date.today().isoformat()
        if state.get("sod_date") != today:
            state["sod_date"] = today
            state["sod_equity"] = equity
        state["hwm"] = max(state.get("hwm", equity), equity)
        _save_state(state)
        return state.get("sod_equity", equity), state["hwm"]


@dataclass
class _LegView:
    occ: str
    symbol_underlying: str
    expiry: date
    opt_type: str
    strike: float
    qty: float
    unrealized_pl: float = 0.0   # P&L latent réel de la jambe ($, Alpaca)
    market_value: float = 0.0    # valeur de marché réelle de la jambe ($, Alpaca)


def _parse_occ(occ: str):
    """Décompose un symbole OCC type 'SPY260902P00500000'."""
    import re

    m = re.fullmatch(r"([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d{8})", occ)
    if not m:
        return None
    root, yy, mm, dd, cp, strike = m.groups()
    return (
        root,
        date(2000 + int(yy), int(mm), int(dd)),
        "call" if cp == "C" else "put",
        int(strike) / 1000.0,
    )


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))
