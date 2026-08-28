"""Logique de signal — fonctions pures, sans appel réseau, faciles à tester."""
from __future__ import annotations

from typing import Literal, Sequence

Signal = Literal["buy", "sell", "hold"]


def moving_average(values: Sequence[float], window: int) -> float | None:
    """Moyenne mobile simple sur les `window` dernières valeurs, ou None si pas assez de données."""
    if window <= 0 or len(values) < window:
        return None
    return sum(values[-window:]) / window


def sma_crossover_signal(closes: Sequence[float], fast: int, slow: int) -> Signal:
    """Signal de croisement de moyennes mobiles.

    - "buy"  : la MM courte passe AU-DESSUS de la MM longue (croisement haussier)
    - "sell" : la MM courte passe EN-DESSOUS de la MM longue (croisement baissier)
    - "hold" : pas de croisement sur la dernière barre, ou données insuffisantes
    """
    if len(closes) < slow + 1:
        return "hold"

    fast_now = moving_average(closes, fast)
    slow_now = moving_average(closes, slow)
    fast_prev = moving_average(closes[:-1], fast)
    slow_prev = moving_average(closes[:-1], slow)

    if None in (fast_now, slow_now, fast_prev, slow_prev):
        return "hold"

    crossed_up = fast_prev <= slow_prev and fast_now > slow_now
    crossed_down = fast_prev >= slow_prev and fast_now < slow_now

    if crossed_up:
        return "buy"
    if crossed_down:
        return "sell"
    return "hold"
