#!/bin/bash
# Crée les topics de la plateforme (idempotent). Usage : create-topics.sh <bootstrap>
# Clé de message = identifiant préfixé (AB-…, AP-…) : 3 partitions suffisent à paralléliser les jobs
# tout en garantissant l'ordre des événements d'une même ligne source.
set -euo pipefail
BOOTSTRAP="${1:-localhost:9092}"
KT=/opt/kafka/bin/kafka-topics.sh
for entite in client contrat sinistre paiement; do
  for src in ab ap; do
    "$KT" --bootstrap-server "$BOOTSTRAP" --create --if-not-exists --topic "src.${src}.${entite}" \
      --partitions 3 --replication-factor 1 --config retention.ms=604800000 --config cleanup.policy=delete
  done
done
# file des messages illisibles (dead letter queue), conservée 30 jours pour analyse
"$KT" --bootstrap-server "$BOOTSTRAP" --create --if-not-exists --topic dlq.silver \
  --partitions 1 --replication-factor 1 --config retention.ms=2592000000
"$KT" --bootstrap-server "$BOOTSTRAP" --list
