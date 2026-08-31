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
from src.analyst import Analyst, AnalystView
from src.directional_pocket import DirectionalPocket, PocketConfig
from src.risk_guard import risk_params
from src.spread_agent import SpreadAgent, SpreadConfig, SymbolView
from src.strategy import sma_gap, sma_regime

LIVE_JSON = Path("web/public/data/live.json")
MAX_LOG = 2000


def _spread_config() -> SpreadConfig:
    return SpreadConfig(
        target_delta=config.TARGET_DELTA,
        width=config.SPREAD_WIDTH,
        dte=config.DTE_TARGET,
        contracts=config.SPREAD_CONTRACTS,
        take_profit_pct=config.TAKE_PROFIT_PCT,
        stop_loss_mult=config.STOP_LOSS_MULT,
        min_credit_ratio=config.MIN_CREDIT_RATIO,
    )


def _pocket_config() -> PocketConfig:
    return PocketConfig(
        strong_gap=config.POCKET_STRONG_GAP,
        width=config.SPREAD_WIDTH,
        dte=config.POCKET_DTE,
        max_pocket_pct=config.POCKET_MAX_PCT,
        contracts=config.POCKET_CONTRACTS,
        max_concurrent=config.POCKET_MAX_CONCURRENT,
    )


def _build_views(
    broker: AlpacaOptionsBroker, opinions: dict[str, AnalystView]
) -> list[SymbolView]:
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
        gap = sma_gap(closes, config.FAST_MA, config.SLOW_MA)
        op = opinions.get(sym) or AnalystView(True, "unknown", "")
        views.append(
            SymbolView(sym, regime, analyst_ok=op.ok,
                       analyst_note=f"{op.sentiment}: {op.note}" if op.note else op.sentiment,
                       gap=gap)
        )
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

    opinions = Analyst().assess(config.OPTION_UNIVERSE)
    views = _build_views(broker, opinions)
    print("régimes : " + ", ".join(f"{v.symbol}={v.regime}" for v in views))
    vetoed = [s for s, o in opinions.items() if not o.ok]
    print("analyste : " + (f"écarte {', '.join(vetoed)}" if vetoed else "aucun veto")
          + f" ({opinions.get(config.OPTION_UNIVERSE[0]).note[:40] if opinions else ''})")

    decisions = agent.decide(views)
    if config.POCKET_ENABLED:
        pocket = DirectionalPocket(broker, risk_params(config.RISK_PROFILE), _pocket_config())
        decisions += pocket.decide(views)

    events: list[dict] = []
    for d in decisions:
        line = {"ts": datetime.now(timezone.utc).isoformat(), "symbol": d.symbol,
                "action": d.action, "reason": d.reason}
        if d.action == "open" and d.quote is not None:
            rec = broker.execute_open(d.quote, d.reason)
            _remember_entry_credit(d.quote)
            line["order"] = rec
            tag = "DÉBIT" if d.quote.strategy == "debit" else "crédit"
            print(f"  OPEN  {d.symbol:5} {d.quote.kind:11} {tag} ${d.quote.credit:.2f} "
                  f"maxLoss ${d.quote.max_loss:.0f} -> {rec.get('status')}")
        elif d.action == "close":
            pos = positions.get(d.symbol)
            rec = broker.execute_close(pos, d.reason) if pos else None
            line["order"] = rec
            print(f"  CLOSE {d.symbol:5} {d.reason} -> {rec.get('status') if rec else 'n/a'}")
        else:
            print(f"  hold  {d.symbol:5} {d.reason}")
        events.append(line)

    day_pl = round(acct.equity - acct.last_equity, 2) if acct.last_equity else None
    snapshot = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "dry_run": config.DRY_RUN,
        "equity": round(acct.equity, 2),
        "cash": round(acct.cash, 2),
        "buying_power": round(acct.buying_power, 2),
        "start_of_day_equity": round(acct.start_of_day_equity, 2),
        "high_water_mark": round(acct.high_water_mark, 2),
        "last_equity": round(acct.last_equity, 2),
        "day_pl": day_pl,
        "total_pl": round(acct.equity - 100_000.0, 2),
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
        "analyst": {s: {"ok": o.ok, "sentiment": o.sentiment, "note": o.note}
                    for s, o in opinions.items()},
    }
    _append_live(snapshot, events)
    return snapshot


def _remember_entry_credit(quote) -> None:
    state = _load_state()
    ec = state.setdefault("entry_credits", {})
    if getattr(quote, "is_condor", False):
        ec[f"{quote.short_symbol}|{quote.long_symbol}"] = quote.put_credit
        ec[f"{quote.call_short_symbol}|{quote.call_long_symbol}"] = quote.call_credit
    else:
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
