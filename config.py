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

# --- Gestion du risque ------------------------------------------------------
ORDER_NOTIONAL = float(_get("ORDER_NOTIONAL", "1000"))  # $ par ordre d'achat
MAX_POSITIONS = int(_get("MAX_POSITIONS", "5"))

# --- Exécution ---------------------------------------------------------------
DRY_RUN = _get("DRY_RUN", "true").lower() not in ("false", "0", "no")


def require_keys() -> None:
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise SystemExit(
            "Clés Alpaca manquantes. Copie .env.example vers .env et renseigne "
            "ALPACA_API_KEY / ALPACA_SECRET_KEY."
        )
