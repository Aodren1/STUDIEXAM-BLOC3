#!/bin/sh
# Au premier démarrage (volume /data vide) : génération des sources et exécution complète du pipeline,
# pour que le tableau de bord soit immédiatement consultable. Les démarrages suivants réutilisent le gold.
set -eu
if [ ! -f "${FUSION_DATA_DIR}/lake/gold/dashboard.json" ]; then
  echo "[fusion] zone gold absente : génération des sources et exécution du pipeline"
  python -m fusion seed
  python -m fusion run --complet
fi
exec "$@"
