#!/bin/bash
# Soumet les jobs Spark dans l'ordre silver → doublons → gold sur le cluster standalone.
set -euo pipefail
MASTER="${SPARK_MASTER:-spark://abf-traitement-spark-master:7077}"
LAKE="${LAKE:-hdfs://abf-stockage-namenode:8020/datalake/fusion}"
KAFKA="${KAFKA_BOOTSTRAP:-abf-flux-kafka:9092}"
SUBMIT=/opt/spark/bin/spark-submit

# le paquet fusion (règles de nettoyage, mapping, dédoublonnage) est distribué aux exécuteurs
(cd /opt/fusion && python3 -m zipfile -c /tmp/fusion.zip fusion)

"$SUBMIT" --master "$MASTER" --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.3 \
  --py-files /tmp/fusion.zip jobs/spark/silver_job.py --kafka "$KAFKA" --out "$LAKE"
"$SUBMIT" --master "$MASTER" --py-files /tmp/fusion.zip jobs/spark/dedup_job.py --lake "$LAKE"
"$SUBMIT" --master "$MASTER" jobs/spark/gold_job.py --lake "$LAKE" --ref "${FUSION_REF_DATE:-2026-10-31}"
