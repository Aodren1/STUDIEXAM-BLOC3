"""Job Spark — zone gold : agrégats Spark SQL sur les tables silver (Parquet), mêmes conventions que
fusion.processing.gold (portefeuille actif = statut ACTIF non orphelin, encaissements = REUSSI − REMBOURSE).

    spark-submit jobs/spark/gold_job.py --lake hdfs://abf-stockage-namenode:8020/datalake/fusion --ref 2026-10-31

Chaque agrégat est écrit en Parquet sous gold/<indicateur> et exposable dans Superset via le Thrift Server.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from pyspark import SparkConf
from pyspark.sql import DataFrame, SparkSession

QUERIES = {
    "clients_par_region": """
        SELECT region, source, COUNT(*) AS comptes
        FROM client WHERE region IS NOT NULL GROUP BY region, source""",
    "contrats_actifs_par_branche": """
        SELECT branche, source, COUNT(*) AS contrats, ROUND(SUM(prime_annuelle), 2) AS primes_annualisees
        FROM contrat WHERE statut = 'ACTIF' AND NOT est_orphelin GROUP BY branche, source""",
    "encaissements_mensuels": """
        SELECT substr(date_paiement, 1, 7) AS mois, source,
               ROUND(SUM(CASE WHEN statut = 'REUSSI' THEN montant ELSE -montant END), 2) AS encaisse
        FROM paiement WHERE NOT est_orphelin AND statut IN ('REUSSI', 'REMBOURSE') AND date_paiement IS NOT NULL
        GROUP BY substr(date_paiement, 1, 7), source""",
    "sinistres_mensuels": """
        SELECT substr(date_survenance, 1, 7) AS mois, source, COUNT(*) AS sinistres,
               ROUND(SUM(montant_estime), 2) AS charge
        FROM sinistre WHERE NOT est_orphelin AND date_survenance IS NOT NULL
        GROUP BY substr(date_survenance, 1, 7), source""",
    "ratio_sp_branche": """
        WITH s AS (SELECT c.branche, SUM(s.montant_estime) AS charge FROM sinistre s
                   JOIN contrat c ON s.contrat_uid = c.contrat_uid
                   WHERE NOT s.est_orphelin AND s.statut <> 'REFUSE' AND s.date_survenance > '{debut}'
                   GROUP BY c.branche),
             p AS (SELECT c.branche, SUM(CASE WHEN p.statut = 'REUSSI' THEN p.montant ELSE -p.montant END) AS primes
                   FROM paiement p JOIN contrat c ON p.contrat_uid = c.contrat_uid
                   WHERE NOT p.est_orphelin AND p.statut IN ('REUSSI', 'REMBOURSE') AND p.date_paiement > '{debut}'
                   GROUP BY c.branche)
        SELECT p.branche, ROUND(100 * COALESCE(s.charge, 0) / p.primes, 1) AS ratio_sp
        FROM p LEFT JOIN s ON s.branche = p.branche""",
}


def build_gold(spark: SparkSession, lake: str, ref: date) -> dict[str, DataFrame]:
    for t in ("client", "contrat", "sinistre", "paiement"):
        spark.read.parquet(f"{lake}/silver/{t}").createOrReplaceTempView(t)
    debut = (ref - timedelta(days=365)).isoformat()
    # debut est une date calculée, jamais une saisie : pas d'injection possible
    return {name: spark.sql(sql.format(debut=debut)) for name, sql in QUERIES.items()}


def main(argv: list[str] | None = None) -> dict[str, list[dict]]:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", required=True)
    ap.add_argument("--ref", default="2026-10-31")
    ap.add_argument("--master", default=None)
    args = ap.parse_args(argv)
    # maître : --master du script, sinon celui de spark-submit, sinon local
    master = args.master or SparkConf().get("spark.master", "local[*]")
    spark = SparkSession.builder.appName("fusion-gold").master(master).getOrCreate()
    lake = args.lake.rstrip("/")
    out = {}
    for name, df in build_gold(spark, lake, date.fromisoformat(args.ref)).items():
        df.write.mode("overwrite").parquet(f"{lake}/gold/{name}")
        out[name] = [r.asDict() for r in df.collect()]
    print(json.dumps({k: len(v) for k, v in out.items()}))
    return out


if __name__ == "__main__":
    main()
