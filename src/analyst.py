"""Agent analyste — lit les actualités récentes par sous-jacent et rend un avis (Claude).

Le moteur déterministe reste maître : l'analyste ne fait qu'**autoriser ou écarter**
un sous-jacent pour la semaine (`SymbolView.analyst_ok`). Politique **fail-open** :
sans `ANTHROPIC_API_KEY`, ou en cas d'erreur réseau / news, tous les sous-jacents
sont autorisés — l'analyste ne doit jamais bloquer le trading par accident.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import config

MODEL = "claude-haiku-4-5-20251001"


@dataclass(frozen=True)
class AnalystView:
    ok: bool
    sentiment: str  # "favorable" | "neutral" | "unfavorable" | "unknown"
    note: str


def _all_ok(symbols, note: str) -> dict[str, AnalystView]:
    return {s: AnalystView(True, "unknown", note) for s in symbols}


class Analyst:
    def __init__(
        self,
        api_key: str | None = None,
        lookback_days: int = 3,
        max_headlines: int = 6,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("ANTHROPIC_API_KEY", "")
        self.lookback_days = lookback_days
        self.max_headlines = max_headlines

    # ------------------------------------------------------------------
    def assess(self, symbols: list[str]) -> dict[str, AnalystView]:
        if not self.api_key:
            return _all_ok(symbols, "analyste désactivé (pas de clé ANTHROPIC_API_KEY)")

        try:
            headlines = self._headlines(symbols)
        except Exception as exc:  # noqa: BLE001
            return _all_ok(symbols, f"news indisponibles ({exc})")

        try:
            verdicts = self._ask_claude(headlines)
        except Exception as exc:  # noqa: BLE001
            return _all_ok(symbols, f"analyste indisponible ({exc})")

        out: dict[str, AnalystView] = {}
        for s in symbols:
            v = verdicts.get(s) or {}
            sentiment = str(v.get("verdict", "neutral")).lower()
            if sentiment not in ("favorable", "neutral", "unfavorable"):
                sentiment = "neutral"
            out[s] = AnalystView(
                ok=sentiment != "unfavorable",
                sentiment=sentiment,
                note=str(v.get("why", ""))[:180],
            )
        return out

    # ------------------------------------------------------------------
    def _headlines(self, symbols: list[str]) -> dict[str, list[str]]:
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.requests import NewsRequest

        client = NewsClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
        start = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        out: dict[str, list[str]] = {}
        for s in symbols:
            try:
                res = client.get_news(
                    NewsRequest(symbols=s, start=start, limit=self.max_headlines,
                                exclude_contentless=True)
                )
                out[s] = [n.headline for n in res.data.get("news", [])]
            except Exception:  # noqa: BLE001
                out[s] = []
        return out

    def _ask_claude(self, headlines: dict[str, list[str]]) -> dict[str, dict]:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        lines: list[str] = []
        for s, hs in headlines.items():
            lines.append(f"{s} :")
            lines.extend(f"  - {h}" for h in hs)
            if not hs:
                lines.append("  (aucune actualité récente)")

        prompt = (
            "Tu es analyste actions pour un agent qui VEND des primes d'options "
            "(spreads verticaux à crédit, échéance ~1 semaine). Pour chaque ticker, "
            "à partir des titres d'actualité ci-dessous, donne un verdict :\n"
            "- 'favorable' : contexte calme ou porteur, pas de catalyseur binaire imminent\n"
            "- 'neutral' : rien de saillant\n"
            "- 'unfavorable' : résultats trimestriels imminents, litige/enquête, choc, "
            "M&A, forte incertitude directionnelle — bref, risque de gros mouvement\n\n"
            f"{chr(10).join(lines)}\n\n"
            'Réponds UNIQUEMENT en JSON compact : '
            '{"TICKER": {"verdict": "favorable|neutral|unfavorable", "why": "6 mots max"}}'
        )
        msg = client.messages.create(
            model=MODEL,
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {}
