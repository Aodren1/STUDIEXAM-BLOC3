"""fusion-data : unification des données AbAssurance (Oracle) et AssurePlus (SQL Server).

Chaîne cible : sources → Talend (extraction, mapping) → Kafka (topics src.*) → Spark (silver, doublons, gold)
→ HDFS (zones bronze / silver / gold) → tableau de bord.
"""

__version__ = "1.0.0"
