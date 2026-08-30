"""Backtest hors ligne : données synthétiques injectées, aucun appel réseau."""
import math
from datetime import date, timedelta

from backtest.engine import realized_vol, run_backtest


def _series(prices: list[float], start: date = date(2024, 1, 1)):
    return [(start + timedelta(days=i), p) for i, p in enumerate(prices)]


def test_realized_vol_floor_and_scale():
    flat = [100.0] * 30
    assert realized_vol(flat, 20) == 0.10  # plancher
    noisy = [100.0 * (1.02 if i % 2 else 0.98) for i in range(40)]
    assert realized_vol(noisy, 20) > 0.10


def test_backtest_runs_and_trades_on_synthetic_trend():
    # tendance baissière puis rebond marqué -> le régime passe bearish puis bullish
    down = [100 - i for i in range(40)]
    up = [60 + 4 * i for i in range(20)]
    prices = down + up
    data = {"AAPL": _series(prices)}

    result = run_backtest(
        ["AAPL"],
        "2024-01-01",
        "2024-03-31",
        fast_ma=3,
        slow_ma=5,
        data=data,
    )

    assert result["equity_curve"], "courbe d'equity vide"
    assert result["metrics"]["num_trades"] >= 1
    assert set(result["metrics"]) >= {
        "total_return_pct",
        "max_drawdown_pct",
        "sharpe_annualized",
        "win_rate_pct",
    }
    # comptabilité : dernier point = pas de position résiduelle
    assert result["equity_curve"][-1]["positions"] == 0
    # chaque trade a un P&L chiffré et des motifs
    for t in result["trades"]:
        assert isinstance(t["pl"], float)
        assert t["open_reason"] and t["close_reason"]


def test_backtest_flat_market_stays_bounded():
    data = {"SPY": _series([100.0 + math.sin(i / 5) for i in range(80)])}
    result = run_backtest(["SPY"], "2024-01-01", "2024-04-01", fast_ma=3, slow_ma=5, data=data)
    # marché sans tendance : le risque défini borne l'equity dans un couloir raisonnable
    eqs = [r["equity"] for r in result["equity_curve"]]
    assert max(eqs) - min(eqs) < 15_000
    assert result["equity_curve"][-1]["positions"] == 0
