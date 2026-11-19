# Talend — conception des jobs d'intégration

Talend porte, dans l'architecture cible, l'**extraction des bases historiques** et le **mapping** vers le
modèle unifié avant publication sur Kafka (`BDD Oracle / SQL Server → Talend → Kafka`).

## Pourquoi les jobs ne sont pas livrés au format `.item`

Talend Open Studio, l'édition gratuite, n'est plus maintenue depuis le 31 janvier 2024. Les éditions actuelles
(Qlik Talend Cloud / Data Fabric) sont sous licence et s'exécutent sur un *Remote Engine* qu'on ne peut pas
embarquer dans un dépôt public. Le rôle de Talend est donc tenu par le module Python `fusion.extract`, qui
respecte le même contrat que les jobs décrits ici : mêmes requêtes, même filigrane incrémental, même
enveloppe de message et mêmes topics. La migration vers Talend est un changement d'outil, pas de contrat.

Le **mapping**, lui, est commun aux deux mondes : `fusion/mapping/mapping.toml` s'exporte en CSV pour le
composant tMap (`python -m fusion mapping talend/tmap_mapping.csv`), ce qui évite de maintenir deux définitions.

## Jobs

| Job | Composants | Rôle |
|---|---|---|
| `J00_contexte` | tContextLoad | Charge `contexts/<env>.properties` (connexions, filigranes, bootstrap Kafka) |
| `J01_extract_abassurance` | tOracleConnection → tOracleInput ×4 → tMap → tKafkaOutput ×4 | Lit AB_CLIENT, AB_CONTRAT, AB_SINISTRE, AB_PAIEMENT au-delà du filigrane, publie sur `src.ab.*` |
| `J02_extract_assureplus` | tMSSqlConnection → tMSSqlInput ×4 → tMap → tKafkaOutput ×4 | Même chose pour AP_USERS, AP_CONTRACTS, AP_CLAIMS, AP_PAYMENTS vers `src.ap.*` |
| `J03_filigranes` | tJava → tFileOutputProperties | Enregistre la plus grande valeur lue par table, après accusé de réception de Kafka |
| `J10_orchestration` | tRunJob ×3, tParallelize | Lance J01 et J02 en parallèle puis J03, sur ordre du DAG Airflow |

### Détail de `J01_extract_abassurance`

```
tOracleConnection ──┬─► tOracleInput « AB_CLIENT »   (SELECT … WHERE AB_CLIENT_ID > context.wm_ab_client ORDER BY 1)
                    │        └─► tMap (clé = "AB-" + AB_CLIENT_ID, enveloppe fusion.src.v1) ─► tKafkaOutput src.ab.client
                    ├─► tOracleInput « AB_CONTRAT »  … ─► tKafkaOutput src.ab.contrat
                    ├─► tOracleInput « AB_SINISTRE » … ─► tKafkaOutput src.ab.sinistre
                    └─► tOracleInput « AB_PAIEMENT » … ─► tKafkaOutput src.ab.paiement
tLogCatcher ─► tFileOutputDelimited (journal des rejets)      tStatCatcher ─► tableau de suivi des volumes
```

Réglages retenus :

- curseur Oracle avec `fetchSize = 5 000` (lecture par lots, comme `extract_table(batch_size=5000)`) ;
- clé de message Kafka = identifiant préfixé, pour que toutes les versions d'une ligne restent dans la même partition ;
- `tKafkaOutput` : `acks=all`, `retries=5`, compression `lz4` ;
- les colonnes sont publiées **brutes** : le nettoyage est fait par Spark, une seule fois, avec des règles testées. Talend ne corrige rien, il transporte et trace.

## Fichiers

- `contexts/dev.properties`, `contexts/prod.properties` : variables de contexte (aucun secret, voir le coffre-fort) ;
- `tmap_mapping.csv` : export du mapping, généré depuis `mapping.toml`.
