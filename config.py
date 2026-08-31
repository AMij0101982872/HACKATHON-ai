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
    for s in _get(
        "OPTION_UNIVERSE",
        "SPY,QQQ,IWM,AAPL,MSFT,NVDA,AMD,META,GOOGL,AMZN,TSLA",
    ).split(",")
    if s.strip()
]
TARGET_DELTA = float(_get("TARGET_DELTA", "0.30"))     # delta visé pour la jambe courte
SPREAD_WIDTH = float(_get("SPREAD_WIDTH", "5"))        # écart entre strikes ($)
# Seuil d'écart MM courte/longue au-delà duquel on quitte le régime "neutral".
# Élevé => l'iron condor (revenu non directionnel) est l'état par défaut.
REGIME_THRESHOLD = float(_get("REGIME_THRESHOLD", "0.03"))
DTE_TARGET = int(_get("DTE_TARGET", "7"))              # jours à l'échéance à l'ouverture
SPREAD_CONTRACTS = int(_get("SPREAD_CONTRACTS", "5"))    # contrats par structure à crédit
TAKE_PROFIT_PCT = float(_get("TAKE_PROFIT_PCT", "0.4"))  # clôture à 40 % du crédit (réalise plus vite)
STOP_LOSS_MULT = float(_get("STOP_LOSS_MULT", "2"))      # stop à 2x le crédit
# Backtest : descendre ce seuil sous 0,15 détruit l'espérance (spreads mal payés × levier).
MIN_CREDIT_RATIO = float(_get("MIN_CREDIT_RATIO", "0.15"))  # crédit/largeur minimal pour ouvrir
SLIPPAGE_PCT = float(_get("SLIPPAGE_PCT", "0.03"))       # traversée du bid-ask à l'exécution

# --- Poche directionnelle (achat de debit spreads sur régime marqué) --------
# COUPÉE par défaut : dans TOUTES les configs de backtest, l'achat de debit spreads
# fait perdre de l'argent (aucun edge après slippage, le levier ne fait qu'amplifier).
# Le code reste dans le repo (options des 2 côtés, démontrable) ; POCKET_ENABLED=true
# pour la réactiver et l'observer, jamais en profil "profit max".
POCKET_ENABLED = _get("POCKET_ENABLED", "false").lower() not in ("false", "0", "no")
POCKET_STRONG_GAP = float(_get("POCKET_STRONG_GAP", "0.06"))  # |écart MM| minimal
POCKET_MAX_PCT = float(_get("POCKET_MAX_PCT", "0.15"))        # débit total <= % de l'equity
POCKET_DTE = int(_get("POCKET_DTE", "14"))
POCKET_CONTRACTS = int(_get("POCKET_CONTRACTS", "1"))         # contrats par debit spread
POCKET_MAX_CONCURRENT = int(_get("POCKET_MAX_CONCURRENT", "3"))

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
