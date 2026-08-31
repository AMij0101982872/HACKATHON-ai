# Agent Alpaca — spreads d'options à crédit pilotés par le régime

**Alpaca AI Trading Agents Hackathon (lablab.ai × Alpaca)** · compte paper dédié **`PA37A6KORM0G`** · 100 000 $

---

## 1. Logique d'IA

**Deux agents.**

**a) Agent analyste (Featherless AI).** À chaque passe, récupère les titres d'actualité des
3 derniers jours par sous-jacent (Alpaca News API) et demande à un modèle open-source servi
par **Featherless AI** (API compatible OpenAI, Llama&nbsp;3.1) un verdict par ticker :
`favorable` / `neutral` / `unfavorable` pour *vendre de la prime cette semaine*. Un verdict
`unfavorable` (résultats imminents, litige, choc, M&A) **écarte** le sous-jacent. Politique
*fail-open* : sans clé ou en cas d'erreur, aucun veto — l'IA n'interrompt jamais le moteur.

**b) Moteur déterministe.** Pour chaque sous-jacent autorisé, calcule le **régime de marché**
= écart relatif MM10/MM30 comparé à un seuil de 3 % :

| Régime | Structure d'options ouverte |
|---|---|
| haussier | **bull put spread** |
| baissier | **bear call spread** |
| neutre | **iron condor** (côté put) |

Sélection des strikes : jambe courte à **delta ≈ 0,30** (Black-Scholes sur volatilité réalisée),
largeur **5 $**, échéance **~7 jours**. Gestion : prise de profit à **50 %** du crédit encaissé,
stop à **2×** le crédit, clôture à **DTE ≤ 1** ; un spread directionnel se clôture aussi si le
régime bascule à l'opposé ; l'iron condor est laissé courir vers sa cible (évite le *whipsaw*).

**Backtest de référence** (6 sous-jacents, 17 mois, primes modélisées Black-Scholes) :
rendement **+2,1 %**, drawdown max **−7,2 %**, Sharpe 0,36, **569 trades**, **75,7 %** gagnants.
À lire comme un *profil de comportement* : le modèle sous-estime la prime réelle (vol implicite
> vol réalisée) et la fenêtre de jugement est courte.

## 2. Points de contrôle des risques

`risk_guard.py` — **déterministe, aucun LLM dans le chemin d'un ordre**. Toute ouverture passe
la batterie ; le premier contrôle qui échoue donne la raison (journalisée).

1. **Compte paper obligatoire**
2. **Kill-switch perte du jour** (−15 % vs equity d'ouverture) → bloque les ouvertures, autorise les clôtures
3. **Kill-switch drawdown** (−30 % vs high-water mark)
4. **Risque défini uniquement** — aucune jambe nue, jamais
5. **Plafond de positions ouvertes** (15)
6. **Perte maximale par structure** (3 000 $)
7. **Exposition maximale par sous-jacent** (35 % de l'equity)
8. **Buying power suffisant**
9. **Buffer de cash plancher** (2 %)
10. **Fenêtre d'échéance** (DTE 2–60)
11. **Liquidité** : spread bid-ask de la jambe courte ≤ 15 %

Trois profils prédéfinis (`conservative` / `balanced` / `aggressive`) ; slippage modélisé à
**3 %** à l'entrée comme à la sortie. Le pire cas d'un trade est borné par construction
(perte = largeur − crédit).

## 3. Mise en œuvre de l'infrastructure Alpaca

- **Trading API** (`alpaca-py`, paper) : compte & positions, `get_option_contracts` (chaîne
  filtrée par type / échéance / strike), cotations d'options (`OptionHistoricalDataClient`),
  **ordres multi-jambes** (`OrderClass.MLEG` + `OptionLegRequest` avec `PositionIntent`
  `sell_to_open` / `buy_to_open` / `*_to_close`), bougies journalières (régime + vol réalisée),
  **Alpaca News API** pour l'analyste.
- **Serveur MCP Alpaca** : connecté dans Claude Code pour la **supervision en langage naturel**
  (état du compte, positions, cotations, passage d'ordres manuel) pendant le développement et la démo.
- **Featherless AI** (partenaire) : inférence open-source (API compatible OpenAI) pour l'agent analyste.
- **Runner CLI** (`python -m src.run`) = point d'entrée autonome, **planifié par GitHub Actions**
  toutes les 20 min en heures de marché ; publie `web/public/data/live.json`.
- **Dashboard** : Vite + React sur **Netlify**, lit `live.json` (réel) et `history.json` (backtest).
- **Tests** : 66 tests `pytest` (garde-fou, pricing, machine à états, scénarios, backtest, analyste).

**Repo** : github.com/gatsoundoujuniior-netizen/ALPACA_AI_HACKHATONS · branche `ivan-jr`
