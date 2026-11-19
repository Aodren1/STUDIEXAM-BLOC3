# fusion-data — AbAssurance × AssurePlus

Plateforme de données de la fusion AbAssurance (Oracle) et AssurePlus (SQL Server) :
**Talend → Kafka → Spark → Hadoop → tableau de bord**.
Examen Bachelor Concepteur Développeur Big Data, **Bloc 3 : développer une solution digitale**.

| Accès | Valeur |
|---|---|
| Tableau de bord déployé | http://51.210.246.138:8096 |
| Compte administrateur | `admin` / `Admin-Fusion-2026` |
| Compte utilisateur | `analyste` / `Analyste-Fusion-2026` |
| Dossier d’examen (PDF) | `BCDBigData_Examen_B3_copiearendre_FLORIDE_Aodren.pdf` |
| Intégration continue | onglet **Actions** du dépôt |
| Convention de branches | [CONTRIBUTING.md](CONTRIBUTING.md) |

Ce sont des comptes de démonstration. En production, les mots de passe viennent du fichier `.env` (non versionné) et sont stockés hachés.

## Ce que fait la solution

1. **Sources** : deux bases générées aux colonnes exactes de l’annexe (`AB_*`, `AP_*`), au 1/2 000e du réel, avec les défauts constatés au profilage (dates texte, noms complets inversés, primes mensuelles, doublons, orphelins).
2. **Extraction (rôle Talend)** : lecture par lots avec filigrane incrémental, publication sur 8 topics Kafka `src.<ab|ap>.<entité>` (clé = identifiant préfixé).
3. **Silver (Spark)** : mapping déclaratif (`fusion/mapping/mapping.toml`), règles de qualité DQ01–DQ17, orphelins en cascade, chargement **idempotent**.
4. **Doublons (Spark)** : blocage, score pondéré (Jaro-Winkler), fusion des seules paires certaines, golden record ; précision et rappel mesurés.
5. **Gold (Spark)** : agrégats des cinq vues demandées ; **réconciliation bloquante** source ↔ silver.
6. **Lac et entrepôt** : zones bronze / silver / gold en local ou sur **HDFS** (WebHDFS) ; entrepôt conforme au MCD, clés étrangères appliquées, quarantaine.
7. **Tableau de bord** (Flask + Chart.js) : synthèse, répartition des clients, contrats, sinistres, finances, matrice de doublons, qualité ; rôles administrateur et utilisateur.

Le même code métier (`fusion/`) est exécuté par le moteur local (poste, tests, démonstration) et par les jobs **PySpark** (`jobs/spark/`). La CI vérifie que les deux produisent les mêmes chiffres.

## Démarrer

```bash
python -m venv .venv && . .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -e ".[dev]"
python -m fusion run --complet                       # génère les sources puis exécute le pipeline
python -m fusion serve --port 8000                   # http://127.0.0.1:8000
```

Avec Docker (profil démonstration, celui du serveur de recette) :

```bash
cp .env.example .env
docker compose up -d --build                         # http://localhost:8096
```

Plateforme Big Data complète (Kafka KRaft, HDFS, Spark, Debezium, Superset) :

```bash
docker compose -f infra/docker-compose.bigdata.yml up -d
docker compose -f infra/docker-compose.bigdata.yml --profile jobs run --rm abf-ingestion-extract
docker compose -f infra/docker-compose.bigdata.yml --profile jobs run --rm abf-traitement-submit
```

## Qualité

```bash
pytest --cov                     # tests et couverture (lignes et branches)
node --test tests/js/format.test.mjs
ruff check . && ruff format --check .
bandit -c pyproject.toml -r fusion
```

La CI (`.github/workflows/ci.yml`) exécute la convention de branche, ruff, bandit, les tests sur Python 3.11 à 3.13 (seuil de couverture 85 %), les jobs Spark sur JVM, l’intégration avec un vrai broker Kafka, SonarCloud (si le secret `SONAR_TOKEN` est défini) et la recette de l’image Docker.

## Arborescence

| Dossier | Rôle |
|---|---|
| `fusion/sources` | DDL Oracle et SQL Server, générateur des bases de test |
| `fusion/extract.py`, `fusion/bus` | Extraction (rôle Talend), bus Kafka ou journal local équivalent |
| `fusion/mapping`, `cleaning.py`, `quality.py` | Mapping déclaratif, nettoyage, règles de qualité |
| `fusion/processing`, `jobs/spark` | Silver, doublons, gold : moteur Python et jobs PySpark |
| `fusion/lake.py`, `fusion/warehouse.py` | Lac (local / HDFS) et entrepôt conforme au MCD |
| `fusion/web` | Tableau de bord |
| `infra`, `talend`, `Dockerfile` | Kafka, HDFS, Spark, Debezium, Airflow ; conception Talend ; déploiement |
| `tests`, `reports` | Tests ; rapports qualité, couverture et anomalie FUS-9 |
| `docs` | Sources du dossier (`python docs/build.py`) et captures (`node docs/captures.mjs`) |

Auteur : Aodren Floride.
