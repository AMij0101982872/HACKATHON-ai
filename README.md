# hackathon-AI — Agent de trading Alpaca

Agent de trading sur compte **paper** Alpaca. Deux façons de l'utiliser :

1. **Via Claude Code + MCP** — tu discutes en langage naturel, Claude appelle les outils
   du serveur MCP `alpaca` (déjà configuré en scope user). Voir [CLAUDE.md](CLAUDE.md).
2. **En autonome** — le script Python `src/agent.py` tourne seul : il récupère les cours,
   calcule un signal (croisement de moyennes mobiles) et passe les ordres.

## Prérequis

- Python 3.10+
- `uv` (déjà installé)
- Des clés API **paper** Alpaca → https://alpaca.markets → Paper Trading → *View API Keys*

## Installation

```powershell
cd C:\Users\hp\hackathon-AI
uv venv
.venv\Scripts\activate
uv pip install -r requirements.txt
copy .env.example .env   # puis mets tes clés dans .env
```

## Lancer l'agent

```powershell
# Simulation (aucun ordre envoyé) — valeur par défaut
python -m src.agent

# Passer les ordres pour de vrai (compte paper)
#   -> mets DRY_RUN=false dans .env
```

## Config

Tout se règle dans [config.py](config.py) et `.env` :

| Variable        | Rôle                                              |
|-----------------|--------------------------------------------------|
| `WATCHLIST`     | Symboles suivis (ex. `AAPL,MSFT,NVDA`)            |
| `FAST_MA`       | Fenêtre moyenne mobile courte (jours)            |
| `SLOW_MA`       | Fenêtre moyenne mobile longue (jours)            |
| `ORDER_NOTIONAL`| Montant en $ par ordre d'achat                   |
| `DRY_RUN`       | `true` = simulation, `false` = ordres réels      |

## Tests

```powershell
uv pip install pytest
pytest
```

## Structure

```
hackathon-AI/
├── CLAUDE.md              # mode d'emploi pour Claude Code (agent via MCP)
├── config.py             # paramètres + chargement .env
├── requirements.txt
├── src/
│   ├── alpaca_client.py  # wrapper alpaca-py (compte, cours, ordres)
│   ├── strategy.py       # logique de signal (pure, testable)
│   └── agent.py          # boucle : données -> signal -> risk -> ordre
└── tests/
    └── test_strategy.py
```
