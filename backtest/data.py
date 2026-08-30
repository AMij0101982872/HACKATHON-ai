"""Chargement (avec cache disque) de l'historique de bougies journalières via alpaca-py.

Cache CSV dans `data/` pour éviter de retélécharger à chaque run de backtest.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from pathlib import Path

import config

CACHE_DIR = Path("data")

Series = list[tuple[date, float]]  # [(jour, clôture), ...] trié par date croissante


def _cache_path(symbol: str, start: str, end: str) -> Path:
    return CACHE_DIR / f"{symbol}_{start}_{end}.csv"


def _read_csv(path: Path) -> Series:
    with path.open(newline="") as f:
        return [(date.fromisoformat(r[0]), float(r[1])) for r in csv.reader(f)]


def _write_csv(path: Path, rows: Series) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        for d, c in rows:
            w.writerow([d.isoformat(), f"{c:.6f}"])


def load_daily_closes(
    symbols: list[str],
    start: str,
    end: str,
    client=None,
) -> dict[str, Series]:
    """Retourne {symbole: [(date, clôture), ...]} entre `start` et `end` (format YYYY-MM-DD)."""
    out: dict[str, Series] = {}
    missing: list[str] = []
    for s in symbols:
        p = _cache_path(s, start, end)
        if p.exists():
            out[s] = _read_csv(p)
        else:
            missing.append(s)

    if missing:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = client or StockHistoricalDataClient(
            config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY
        )
        req = StockBarsRequest(
            symbol_or_symbols=missing,
            timeframe=TimeFrame.Day,
            start=datetime.fromisoformat(start).replace(tzinfo=timezone.utc),
            end=datetime.fromisoformat(end).replace(tzinfo=timezone.utc),
        )
        bars = client.get_stock_bars(req)
        for s in missing:
            rows: Series = sorted(
                (b.timestamp.date(), float(b.close)) for b in bars.data.get(s, [])
            )
            _write_csv(_cache_path(s, start, end), rows)
            out[s] = rows

    return out
