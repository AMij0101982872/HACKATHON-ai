"""Valorisation d'options — Black-Scholes, sans dépendance externe.

Sert à deux choses :
- le backtest : estimer prix d'entrée / de sortie d'un spread quand on n'a pas de
  données d'options historiques ;
- les tests de scénarios : générer des chaînes d'options synthétiques cohérentes.

Convention : taux sans risque et rendement du dividende à 0 par défaut (compte paper,
horizon court). `t` est en années (dte / 365).
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Literal, Sequence

OptionType = Literal["call", "put"]

TRADING_DAYS = 252
VOL_FLOOR = 0.10


def realized_vol(closes: Sequence[float], window: int = 20, floor: float = VOL_FLOOR) -> float:
    """Volatilité réalisée annualisée sur les `window` derniers rendements log.

    Renvoie 0.25 par défaut si l'historique est trop court, et applique un plancher.
    """
    if len(closes) < 3:
        return 0.25
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    w = rets[-window:]
    if len(w) < 2:
        return 0.25
    return max(statistics.pstdev(w) * math.sqrt(TRADING_DAYS), floor)


def _norm_cdf(x: float) -> float:
    """Fonction de répartition de la loi normale centrée réduite (via erfc)."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _d1_d2(spot: float, strike: float, t: float, vol: float, r: float, q: float):
    if spot <= 0 or strike <= 0 or t <= 0 or vol <= 0:
        raise ValueError("spot, strike, t et vol doivent être > 0")
    vsqrt = vol * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * vol * vol) * t) / vsqrt
    d2 = d1 - vsqrt
    return d1, d2


def bs_price(
    spot: float,
    strike: float,
    t: float,
    vol: float,
    option_type: OptionType,
    r: float = 0.0,
    q: float = 0.0,
) -> float:
    """Prix Black-Scholes d'une option européenne.

    À l'échéance (t <= 0) renvoie la valeur intrinsèque.
    """
    if t <= 0:
        intrinsic = spot - strike if option_type == "call" else strike - spot
        return max(intrinsic, 0.0)

    d1, d2 = _d1_d2(spot, strike, t, vol, r, q)
    disc_s = spot * math.exp(-q * t)
    disc_k = strike * math.exp(-r * t)
    if option_type == "call":
        return disc_s * _norm_cdf(d1) - disc_k * _norm_cdf(d2)
    return disc_k * _norm_cdf(-d2) - disc_s * _norm_cdf(-d1)


def bs_delta(
    spot: float,
    strike: float,
    t: float,
    vol: float,
    option_type: OptionType,
    r: float = 0.0,
    q: float = 0.0,
) -> float:
    """Delta Black-Scholes. Call dans [0, 1], put dans [-1, 0]."""
    if t <= 0:
        if option_type == "call":
            return 1.0 if spot > strike else 0.0
        return -1.0 if spot < strike else 0.0
    d1, _ = _d1_d2(spot, strike, t, vol, r, q)
    if option_type == "call":
        return math.exp(-q * t) * _norm_cdf(d1)
    return -math.exp(-q * t) * _norm_cdf(-d1)


def strike_for_delta(
    spot: float,
    target_delta: float,
    t: float,
    vol: float,
    option_type: OptionType,
    r: float = 0.0,
    q: float = 0.0,
    tol: float = 1e-4,
) -> float:
    """Cherche le strike dont le |delta| vaut `target_delta` (dichotomie).

    `target_delta` est donné en valeur absolue, ex. 0.30.
    """
    if not 0.0 < target_delta < 1.0:
        raise ValueError("target_delta doit être dans ]0, 1[")

    # Sens de variation de |delta| en fonction du strike :
    #   call -> décroissant (strike haut = plus hors de la monnaie)
    #   put  -> croissant   (strike haut = plus dans la monnaie)
    lo, hi = spot * 0.2, spot * 3.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        d = abs(bs_delta(spot, mid, t, vol, option_type, r, q))
        if abs(d - target_delta) < tol:
            return mid
        too_high = d > target_delta
        if option_type == "call":
            lo, hi = (mid, hi) if too_high else (lo, mid)
        else:
            lo, hi = (lo, mid) if too_high else (mid, hi)
    return 0.5 * (lo + hi)


@dataclass(frozen=True)
class VerticalSpread:
    """Spread vertical vendu (à crédit).

    - bull put  : option_type="put",  short_strike > long_strike
    - bear call : option_type="call", short_strike < long_strike
    """

    option_type: OptionType
    short_strike: float
    long_strike: float
    contracts: int = 1

    @property
    def width(self) -> float:
        return abs(self.short_strike - self.long_strike)

    def max_loss(self, credit: float) -> float:
        """Perte maximale = (largeur - crédit) * 100 * contrats, plancher 0."""
        return max(self.width - credit, 0.0) * 100.0 * self.contracts


def price_vertical_credit(
    spread: VerticalSpread,
    spot: float,
    t: float,
    vol: float,
    r: float = 0.0,
    q: float = 0.0,
) -> float:
    """Crédit (net, par action) reçu à la vente du spread : prix jambe courte - prix jambe longue.

    Multiplier par 100 * contracts pour le montant en dollars.
    """
    short = bs_price(spot, spread.short_strike, t, vol, spread.option_type, r, q)
    long = bs_price(spot, spread.long_strike, t, vol, spread.option_type, r, q)
    return short - long


def apply_slippage(mid_credit: float, spread_pct: float = 0.05) -> float:
    """Crédit réellement encaissé après avoir traversé la moitié du bid-ask.

    On reçoit moins que le mid à l'entrée.
    """
    return mid_credit * (1.0 - spread_pct / 2.0)
