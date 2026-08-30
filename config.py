"""Paramètres de l'agent. Les valeurs peuvent être surchargées via le fichier .env."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str) -> str:
    return os.getenv(name, default)


# --- Identifiants ---------------------------------------------------------------
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
PAPER = True  # ce projet ne vise QUE le compte paper

# --- Univers & stratégie ------------------------------------------------------
WATCHLIST = [s.strip().upper() for s in _get("WATCHLIST", "AAPL,MSFT,NVDA").split(",") if s.strip()]
FAST_MA = int(_get("FAST_MA", "10"))
SLOW_MA = int(_get("SLOW_MA", "30"))
LOOKBACK_DAYS = SLOW_MA + 10  # marge pour calculer la MM longue

# --- Stratégie options : spreads verticaux à crédit ------------------------
OPTION_UNIVERSE = [
    s.strip().upper()
    for s in _get("OPTION_UNIVERSE", "SPY,QQQ,AAPL,MSFT,NVDA,AMD").split(",")
    if s.strip()
]
TARGET_DELTA = float(_get("TARGET_DELTA", "0.30"))     # delta visé pour la jambe courte
SPREAD_WIDTH = float(_get("SPREAD_WIDTH", "5"))        # écart entre strikes ($)
# Seuil d'écart MM courte/longue au-delà duquel on quitte le régime "neutral".
# Élevé => l'iron condor (revenu non directionnel) est l'état par défaut.
REGIME_THRESHOLD = float(_get("REGIME_THRESHOLD", "0.03"))
DTE_TARGET = int(_get("DTE_TARGET", "7"))              # jours à l'échéance à l'ouverture
TAKE_PROFIT_PCT = float(_get("TAKE_PROFIT_PCT", "0.5"))  # clôture à 50 % du crédit encaissé
STOP_LOSS_MULT = float(_get("STOP_LOSS_MULT", "2"))      # stop à 2x le crédit
SLIPPAGE_PCT = float(_get("SLIPPAGE_PCT", "0.03"))       # traversée du bid-ask à l'exécution

# --- Gestion du risque ------------------------------------------------------
# Profil appliqué par l'agent : "conservative" | "balanced" | "aggressive"
# (presets définis dans src/risk_guard.py). Le hackathon vise "aggressive".
RISK_PROFILE = _get("RISK_PROFILE", "aggressive")
ORDER_NOTIONAL = float(_get("ORDER_NOTIONAL", "1000"))  # $ par ordre d'achat (legacy actions)
MAX_POSITIONS = int(_get("MAX_POSITIONS", "5"))

# --- Exécution ---------------------------------------------------------------
DRY_RUN = _get("DRY_RUN", "true").lower() not in ("false", "0", "no")


def require_keys() -> None:
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise SystemExit(
            "Clés Alpaca manquantes. Copie .env.example vers .env et renseigne "
            "ALPACA_API_KEY / ALPACA_SECRET_KEY."
        )
