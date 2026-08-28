"""Wrapper léger autour d'alpaca-py : compte, cours, positions, ordres (paper)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

import config


class AlpacaClient:
    def __init__(self, api_key: str | None = None, secret_key: str | None = None) -> None:
        key = api_key or config.ALPACA_API_KEY
        secret = secret_key or config.ALPACA_SECRET_KEY
        self.trading = TradingClient(key, secret, paper=config.PAPER)
        self.data = StockHistoricalDataClient(key, secret)

    # --- Lecture -------------------------------------------------------------
    def account_summary(self) -> dict:
        a = self.trading.get_account()
        return {
            "status": a.status,
            "buying_power": float(a.buying_power),
            "cash": float(a.cash),
            "equity": float(a.equity),
            "portfolio_value": float(a.portfolio_value),
        }

    def positions(self) -> dict[str, float]:
        """Renvoie {symbole: quantité} des positions ouvertes."""
        return {p.symbol: float(p.qty) for p in self.trading.get_all_positions()}

    def latest_price(self, symbol: str) -> float:
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quote = self.data.get_stock_latest_quote(req)[symbol]
        # milieu de fourchette, ou ask si pas de bid
        bid, ask = float(quote.bid_price), float(quote.ask_price)
        prices = [p for p in (bid, ask) if p > 0]
        return sum(prices) / len(prices) if prices else ask or bid

    def daily_closes(self, symbol: str, lookback_days: int) -> list[float]:
        start = datetime.now(timezone.utc) - timedelta(days=lookback_days * 2 + 5)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
        )
        bars = self.data.get_stock_bars(req).data.get(symbol, [])
        return [float(b.close) for b in bars][-lookback_days:]

    # --- Écriture ---------------------------------------------------------------
    def buy_notional(self, symbol: str, notional: float):
        order = MarketOrderRequest(
            symbol=symbol,
            notional=round(notional, 2),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        return self.trading.submit_order(order)

    def close_position(self, symbol: str):
        return self.trading.close_position(symbol)
