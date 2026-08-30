"""Runner autonome de l'agent — une passe : données -> régime -> décision -> ordres.

Lancé par cron (voir README). `DRY_RUN=true` dans .env -> aucun ordre envoyé, tout est
journalisé dans web/public/data/live.json (source du dashboard).

    python -m src.run              # une passe
    python -m src.run --loop 900   # boucle toutes les 900 s (usage local seulement)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from src.alpaca_options import AlpacaOptionsBroker, _load_state, _save_state
from src.risk_guard import risk_params
from src.spread_agent import SpreadAgent, SpreadConfig, SymbolView
from src.strategy import sma_regime

LIVE_JSON = Path("web/public/data/live.json")
MAX_LOG = 2000


def _spread_config() -> SpreadConfig:
    return SpreadConfig(
        target_delta=config.TARGET_DELTA,
        width=config.SPREAD_WIDTH,
        dte=config.DTE_TARGET,
        take_profit_pct=config.TAKE_PROFIT_PCT,
        stop_loss_mult=config.STOP_LOSS_MULT,
    )


def _build_views(broker: AlpacaOptionsBroker) -> list[SymbolView]:
    start = datetime.now(timezone.utc) - timedelta(days=90)
    views: list[SymbolView] = []
    for sym in config.OPTION_UNIVERSE:
        try:
            req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Day, start=start)
            bars = broker.stock_data.get_stock_bars(req).data.get(sym, [])
            closes = [float(b.close) for b in bars]
        except Exception as exc:  # noqa: BLE001
            print(f"{sym}: données indisponibles ({exc})")
            continue
        if len(closes) < config.SLOW_MA + 1:
            print(f"{sym}: historique insuffisant ({len(closes)} barres)")
            continue
        regime = sma_regime(closes, config.FAST_MA, config.SLOW_MA, config.REGIME_THRESHOLD)
        # analyst_ok reste True tant que l'agent analyste n'est pas branché
        views.append(SymbolView(sym, regime, analyst_ok=True))
    return views


def run_once() -> dict:
    config.require_keys()
    broker = AlpacaOptionsBroker(dry_run=config.DRY_RUN)
    agent = SpreadAgent(broker, risk_params(config.RISK_PROFILE), _spread_config())

    acct = broker.account()
    positions = {p.symbol: p for p in broker.open_spreads()}
    mode = "SIMULATION (dry-run)" if config.DRY_RUN else "ORDRES RÉELS (paper)"
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%SZ}] {mode}")
    print(f"equity ${acct.equity:,.2f} | cash ${acct.cash:,.2f} | "
          f"BP ${acct.buying_power:,.2f} | positions {len(positions)}")

    views = _build_views(broker)
    print("régimes : " + ", ".join(f"{v.symbol}={v.regime}" for v in views))

    decisions = agent.decide(views)
    events: list[dict] = []
    for d in decisions:
        line = {"ts": datetime.now(timezone.utc).isoformat(), "symbol": d.symbol,
                "action": d.action, "reason": d.reason}
        if d.action == "open" and d.quote is not None:
            rec = broker.execute_open(d.quote, d.reason)
            _remember_entry_credit(d.quote)
            line["order"] = rec
            print(f"  OPEN  {d.symbol:5} {d.quote.kind:11} crédit ${d.quote.credit:.2f} "
                  f"maxLoss ${d.quote.max_loss:.0f} -> {rec.get('status')}")
        elif d.action == "close":
            pos = positions.get(d.symbol)
            rec = broker.execute_close(pos, d.reason) if pos else None
            line["order"] = rec
            print(f"  CLOSE {d.symbol:5} {d.reason} -> {rec.get('status') if rec else 'n/a'}")
        else:
            print(f"  hold  {d.symbol:5} {d.reason}")
        events.append(line)

    snapshot = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "dry_run": config.DRY_RUN,
        "equity": round(acct.equity, 2),
        "cash": round(acct.cash, 2),
        "buying_power": round(acct.buying_power, 2),
        "start_of_day_equity": round(acct.start_of_day_equity, 2),
        "high_water_mark": round(acct.high_water_mark, 2),
        "open_positions": len(positions),
        "positions": [
            {
                "symbol": p.symbol,
                "kind": p.kind,
                "entry_credit": round(p.entry_credit, 2),
                "current_value": round(p.current_value, 2),
                "unrealized_pl": round(p.unrealized_pl, 2),
                "dte": p.dte,
                "contracts": p.contracts,
            }
            for p in positions.values()
        ],
        "regimes": {v.symbol: v.regime for v in views},
    }
    _append_live(snapshot, events)
    return snapshot


def _remember_entry_credit(quote) -> None:
    state = _load_state()
    ec = state.setdefault("entry_credits", {})
    ec[f"{quote.short_symbol}|{quote.long_symbol}"] = quote.credit
    _save_state(state)


def _append_live(snapshot: dict, events: list[dict]) -> None:
    try:
        doc = json.loads(LIVE_JSON.read_text())
    except Exception:  # noqa: BLE001
        doc = {"equity_curve": [], "decisions": []}
    doc["generated_at"] = snapshot["ts"]
    doc["dry_run"] = snapshot["dry_run"]
    doc["latest"] = snapshot
    doc["equity_curve"] = (doc.get("equity_curve", []) + [{
        "ts": snapshot["ts"], "equity": snapshot["equity"],
        "cash": snapshot["cash"], "positions": snapshot["open_positions"],
    }])[-MAX_LOG:]
    doc["decisions"] = (doc.get("decisions", []) + events)[-MAX_LOG:]
    LIVE_JSON.parent.mkdir(parents=True, exist_ok=True)
    LIVE_JSON.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"journal -> {LIVE_JSON}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Agent de spreads à crédit — runner")
    ap.add_argument("--loop", type=int, metavar="SECONDS",
                    help="boucle en continu (usage local ; en prod utiliser cron)")
    args = ap.parse_args()
    if args.loop:
        while True:
            try:
                run_once()
            except Exception as exc:  # noqa: BLE001
                print(f"erreur de passe : {exc}")
            time.sleep(args.loop)
    else:
        run_once()


if __name__ == "__main__":
    main()
