# Contribuer au dépôt `fusion-data`

Ce document fixe le modèle de branches, la convention de nommage et les règles de fusion.
Il est opposable : la CI refuse une branche dont le nom ne respecte pas la convention
(`scripts/check_branch_name.py`, exécuté par le job `convention` du workflow).

## 1. Modèle de branches (Git Flow simplifié)

| Branche | Rôle | Créée depuis | Fusionnée dans | Protection |
|---|---|---|---|---|
| `main` | Code en production. Chaque commit de `main` correspond à une version taguée `vX.Y.Z` déployée. | — | — | Pas de push direct, PR obligatoire, CI verte, 1 revue |
| `develop` | Intégration continue du sprint. Toujours déployable sur l'environnement de recette. | `main` | `release/*` | Pas de push direct, PR obligatoire, CI verte |
| `feature/*` | Une User Story ou une tâche technique. | `develop` | `develop` | — |
| `fix/*` | Correction d'une anomalie trouvée en recette (avant mise en production). | `develop` | `develop` | — |
| `hotfix/*` | Correction urgente d'un défaut en production. | `main` | `main` **et** `develop` | — |
| `release/*` | Stabilisation d'une version : seuls les correctifs et la documentation y entrent. | `develop` | `main` (tag) puis `develop` | — |
| `docs/*`, `ci/*`, `chore/*`, `test/*`, `refactor/*` | Travaux sans effet fonctionnel. | `develop` | `develop` | — |

```
main     ●───────────────────────────────●──────────●   v1.0.0, v1.0.1
          \                             / \        /
release    \                ●──●──●────●   \      /
            \              /                \    /
develop      ●──●───●──●──●──────────────────●──●
                 \ /    \ /                     \
feature           ●      ●                hotfix ●
```

## 2. Convention de nommage des branches

```
<type>/<ticket>-<description-courte>
```

- `type` : `feature`, `fix`, `hotfix`, `release`, `docs`, `ci`, `chore`, `test`, `refactor`.
- `ticket` : identifiant du ticket au format `FUS-<n>` (projet *Fusion*). Obligatoire sauf pour `release`.
- `description-courte` : 2 à 6 mots en `kebab-case`, minuscules, sans accent, chiffres autorisés.
- `release` : `release/<X.Y.Z>` (version sémantique), sans ticket.

Expression régulière appliquée par la CI :

```
^(main|develop)$
|^(feature|fix|hotfix|docs|ci|chore|test|refactor)/FUS-[0-9]+-[a-z0-9]+(-[a-z0-9]+){1,5}$
|^release/[0-9]+\.[0-9]+\.[0-9]+$
```

| Exemple | Valide | Raison |
|---|---|---|
| `feature/FUS-4-bus-kafka-extraction` | oui | |
| `fix/FUS-12-paiements-comptes-deux-fois` | oui | |
| `release/1.0.0` | oui | |
| `hotfix/FUS-15-cle-partition-paiements` | oui | |
| `feature/dashboard` | non | ticket absent |
| `Feature/FUS-4-Bus` | non | majuscules |
| `fix/FUS-12-correctif` | non | description d'un seul mot, trop vague |

## 3. Messages de commit

Format [Conventional Commits](https://www.conventionalcommits.org/fr/), en français :

```
<type>(<portée>): <résumé à l'impératif, 72 caractères max>

<corps : pourquoi, et non comment>

Refs: FUS-<n>
```

Types : `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `ci`, `chore`, `build`.
Portées usuelles : `sources`, `nettoyage`, `mapping`, `bus`, `silver`, `doublons`, `gold`, `web`, `spark`, `infra`, `dossier`.

## 4. Règles de fusion

1. Une branche courte (moins de 3 jours) et à jour de `develop` avant la PR.
2. Fusion sans avance rapide (`--no-ff`) pour garder la trace de la branche dans l'historique.
3. La PR décrit le ticket, la façon de tester et l'impact sur les données (schéma, volumes).
4. CI verte obligatoire : convention de nommage, `ruff`, `bandit`, tests et couverture ≥ 85 %.
5. Tout correctif (`fix/*`, `hotfix/*`) commence par un test qui reproduit l'anomalie.
6. Une version = une branche `release/X.Y.Z` fusionnée dans `main`, taguée `vX.Y.Z`, puis refusionnée dans `develop`.
