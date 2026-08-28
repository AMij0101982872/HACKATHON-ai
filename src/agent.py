"""Agent de trading : pour chaque symbole suivi, calcule un signal et agit.

Lancement :  python -m src.agent
Par défaut DRY_RUN=true -> aucun ordre n'est envoyé, les intentions sont juste affichées.
"""
from __future__ import annotations

import sys

import config

# Console Windows en UTF-8 pour les accents
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass
from src.alpaca_client import AlpacaClient
from src.strategy import sma_crossover_signal


def run_once() -> None:
    config.require_keys()
    client = AlpacaClient()

    acct = client.account_summary()
    print(f"Compte {acct['status']} | equity ${acct['equity']:,.2f} | "
          f"cash ${acct['cash']:,.2f} | buying power ${acct['buying_power']:,.2f}")

    positions = client.positions()
    print(f"Positions ouvertes : {positions or 'aucune'}")
    print(f"Mode : {'SIMULATION (dry-run)' if config.DRY_RUN else 'ORDRES RÉELS (paper)'}\n")

    for symbol in config.WATCHLIST:
        closes = client.daily_closes(symbol, config.LOOKBACK_DAYS)
        if len(closes) < config.SLOW_MA + 1:
            print(f"{symbol}: pas assez d'historique ({len(closes)} barres) — ignoré")
            continue

        signal = sma_crossover_signal(closes, config.FAST_MA, config.SLOW_MA)
        held = symbol in positions
        price = closes[-1]
        print(f"{symbol}: prix ${price:,.2f} | signal={signal} | détenu={held}")

        action = _decide(signal, held, len(positions))
        if action is None:
            continue

        if config.DRY_RUN:
            print(f"  -> [SIMULÉ] {action} {symbol}")
            continue

        if action == "BUY":
            order = client.buy_notional(symbol, config.ORDER_NOTIONAL)
            print(f"  -> ordre ACHAT envoyé id={order.id} notional=${config.ORDER_NOTIONAL}")
        elif action == "SELL":
            order = client.close_position(symbol)
            print(f"  -> position CLÔTURÉE id={getattr(order, 'id', 'n/a')}")


def _decide(signal: str, held: bool, open_positions: int) -> str | None:
    """Traduit un signal en action concrète, avec garde-fous de risque."""
    if signal == "buy" and not held:
        if open_positions >= config.MAX_POSITIONS:
            print(f"  -> achat ignoré : plafond de {config.MAX_POSITIONS} positions atteint")
            return None
        return "BUY"
    if signal == "sell" and held:
        return "SELL"
    return None


if __name__ == "__main__":
    run_once()
