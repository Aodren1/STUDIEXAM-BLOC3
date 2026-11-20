# Journal des versions

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/) ; versions sémantiques.

## [1.0.2] — 2026-11-20

### Corrigé
- FUS-13 : alertes de la première analyse SonarCloud traitées. Noms de topic et de groupe du journal validés
  et offsets assainis à la relecture (traversée de chemin) ; export du mapping confiné au répertoire courant ;
  rubrique lue dans l’URL vérifiée et encodée avant l’appel d’API (requête forgée côté client) ; promesses
  JavaScript toutes traitées ; durée d’une étape enregistrée même quand elle échoue ; jointures et sessions
  Spark explicites ; attribut autofocus retiré de la page de connexion (accessibilité). La règle CSRF de
  Flask-WTF est une exception documentée : le jeton de session maison est vérifié sur chaque POST et testé.

### Modifié
- FUS-13 : analyse SonarCloud active dans la CI, sans secret (jeton OIDC éphémère via sonarqube-auth-action) ;
  actions SonarSource épinglées par empreinte de commit ; périmètre de couverture aligné sur pytest-cov.
- FUS-13 : dossier du premier dépôt (20/11/2026) : tableau des liens et synthèse mis à jour.

## [1.0.1] — 2026-11-19

### Modifié
- FUS-12 : dossier complété par les preuves de la CI GitHub (neuf jobs verts) et du déploiement de recette.

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
