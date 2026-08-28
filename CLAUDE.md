# Agent de trading — instructions pour Claude Code

Ce projet pilote un compte **paper** Alpaca. Le serveur MCP `alpaca` est configuré
(scope user) : tu as accès aux outils compte / cours / positions / ordres.

## Règles

- **Compte paper uniquement.** Ne jamais supposer un compte réel.
- Avant tout ordre : afficher le statut du compte, le buying power et les positions.
- Taille d'ordre par défaut : `ORDER_NOTIONAL` de `config.py` (1000 $). Ne pas dépasser
  sans accord explicite de l'utilisateur.
- Pas plus de `MAX_POSITIONS` (5) positions ouvertes simultanément.
- Toujours résumer l'action envisagée et demander confirmation avant de passer un ordre.

## Stratégie de référence

Croisement de moyennes mobiles (`src/strategy.py`) : MM courte `FAST_MA=10`,
MM longue `SLOW_MA=30`, sur bougies journalières.
- croisement haussier -> acheter si pas déjà en position
- croisement baissier -> clôturer la position

## Tâches courantes

- « statut du compte » -> outil compte Alpaca + liste des positions
- « analyse AAPL » -> derniers cours + calcul du signal de croisement
- « lance l'agent en simulation » -> `python -m src.agent` (DRY_RUN reste à `true`)
- « passe en réel » -> demander confirmation, puis mettre `DRY_RUN=false` dans `.env`

## Ne pas faire

- Modifier `.env` (clés) sans demande explicite.
- Envoyer un ordre sans confirmation.
- Committer `.env`.
