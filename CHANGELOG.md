# Journal des versions

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/) ; versions sémantiques.

## [1.0.0] — 2026-11-19

### Ajouté
- FUS-1 : dépôt, modèle de branches et convention de nommage vérifiée par script.
- FUS-2 : bases sources Oracle / SQL Server fidèles à l’annexe, générateur déterministe.
- FUS-3 : mapping déclaratif des deux sources, règles de qualité DQ01–DQ17.
- FUS-4 : extraction incrémentale (rôle Talend), bus Kafka et journal local, lac local / WebHDFS.
- FUS-5 : silver, dédoublonnage (golden record), gold, entrepôt conforme au MCD, réconciliation.
- FUS-6 : tableau de bord à deux rôles (Flask, Chart.js), palette validée, CSP stricte.
- FUS-7 : jobs PySpark, conception Talend, infrastructure Kafka / HDFS / Spark / Debezium / Airflow, Docker.
- FUS-8 : ruff, bandit, SonarCloud, couverture, CI GitHub Actions.
- FUS-10 : dossier d’examen généré depuis les résultats réels.

### Corrigé
- FUS-9 : les republications (rejeu, borne inclusive du filigrane, relivraison Kafka) doublaient les tables silver ;
  chargement désormais idempotent, filigranes enregistrés en mode complet, journal qualité sans doublon.
- Orphelins en cascade (sinistres et paiements d’un contrat orphelin) ; poids du score de doublon normalisés.

### Modifié
- FUS-11 : seuil de fusion automatique des doublons calibré à 0,66 (rappel 69,2 % → 75,5 %, précision 100 %).
