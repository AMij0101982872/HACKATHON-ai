# Agent Alpaca — spreads d'options à crédit pilotés par le régime de marché

Agent de trading **autonome** sur compte **paper** Alpaca, pour le *Alpaca AI Trading
Agents Hackathon* (lablab.ai × Alpaca). Toutes les positions sont des **options**
(spreads verticaux à crédit), à **risque défini et borné**.

## Principe

Deux agents :

1. **Moteur déterministe** (`src/`) — pour chaque sous-jacent, calcule le *régime* de
   marché (écart MM10/MM30) et en déduit une structure d'options :
   | Régime | Structure |
   |--------|-----------|
   | haussier | bull put spread |
   | baissier | bear call spread |
   | neutre | iron condor (côté put) |
   Gestion : prise de profit à 50 % du crédit, stop à 2× le crédit, clôture à
   l'approche de l'échéance. **Poche directionnelle** (`src/directional_pocket.py`) : sur
   régime franchement marqué + feu vert analyste, achat d'un debit spread (call/put,
   budget ≤ 15 % de l'equity, perte plafonnée à la prime). Tout ordre passe par un **garde-fou déterministe**
   (`src/risk_guard.py`) : kill-switch perte du jour / drawdown, plafond de
   positions, perte max par structure, exposition par sous-jacent, buffer de cash,
   fenêtre d'échéance, liquidité.
2. **Agent analyste** (`src/analyst.py`) — un modèle open-source servi par **Featherless AI**
   (partenaire du hackathon, API compatible OpenAI) lit les actualités récentes par sous-jacent
   (Alpaca News API) et rend un verdict `favorable` / `neutral` / `unfavorable` ; un verdict
   défavorable écarte le sous-jacent pour la semaine. Fail-open sans `FEATHERLESS_API_KEY`.

## Arborescence

```
src/
  strategy.py        signal MM + sma_regime (pur, testé)
  risk_guard.py      garde-fou déterministe + profils de risque
  pricing.py         Black-Scholes (prix, delta), strike par delta, vol réalisée
  spread_agent.py    SpreadAgent.decide() — régime -> décisions (broker injecté)
  alpaca_options.py  broker options réel (ordres mleg, mode dry-run)
  run.py             runner CLI une passe -> web/public/data/live.json
backtest/
  data.py            historique de bougies (cache CSV)
  engine.py          rejeu jour/jour : strategy + risk_guard + spread_agent
tests/               62 tests (pytest)
web/                 dashboard Vite + React (déployé sur Netlify)
.github/workflows/   agent.yml — cron GitHub Actions
```

## Installation

```powershell
uv venv
uv pip install -r requirements.txt
copy .env.example .env    # puis renseigner ALPACA_API_KEY / ALPACA_SECRET_KEY
```

## Utilisation

```powershell
pytest                                   # 62 tests
python -m backtest.engine                # backtest -> web/public/data/history.json
python -m src.run                        # une passe de l'agent (DRY_RUN respecté)
python -m src.run --loop 1200            # boucle locale (prod = cron)
```

`DRY_RUN=true` (défaut) : cotations et décisions réelles, **aucun ordre envoyé**.
Passer à `DRY_RUN=false` dans `.env` (ou variable Actions `DRY_RUN`) pour les ordres
réels sur le compte paper.

## Déploiement

- **Agent** : GitHub Actions (`.github/workflows/agent.yml`) exécute `src.run` toutes
  les 20 min en heures de marché et publie `web/public/data/live.json`.
  Secrets requis : `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`. Variable : `DRY_RUN`.
- **Dashboard** : Netlify, importer le repo (réglages dans `netlify.toml`). Le site
  lit `live.json` (réel) et `history.json` (backtest de référence).

## Paramètres (`config.py` / `.env`)

| Variable | Rôle | Défaut |
|----------|------|--------|
| `OPTION_UNIVERSE` | sous-jacents suivis | SPY,QQQ,AAPL,MSFT,NVDA,AMD |
| `TARGET_DELTA` | delta de la jambe courte | 0.30 |
| `SPREAD_WIDTH` | écart entre strikes ($) | 5 |
| `DTE_TARGET` | jours à l'échéance à l'ouverture | 7 |
| `REGIME_THRESHOLD` | seuil de sortie du régime neutre | 0.03 |
| `RISK_PROFILE` | conservative \| balanced \| aggressive | aggressive |
| `DRY_RUN` | simulation si vrai | true |

## Avertissements

Compte **paper** uniquement, capital virtuel, données de marché réelles. Le backtest
d'options modélise les primes en Black-Scholes (vol réalisée) faute de données
d'options historiques : à lire comme un **profil de comportement**, pas une garantie
de P&L.
