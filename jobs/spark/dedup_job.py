"""Job Spark — rapprochement des clients à l'échelle (17 M de comptes en production).

Blocage distribué : chaque compte émet ses clés (explode), une auto-jointure sur la clé produit les
paires candidates, dédoublonnées ; le score réutilise fusion.processing.dedup.compare (UDF Python).
La fusion transitive (union-find) se fait ensuite sur les seules paires certaines, peu nombreuses,
côté driver ; en production on utiliserait GraphFrames.connectedComponents.

    spark-submit --py-files fusion.zip jobs/spark/dedup_job.py --lake hdfs://…/datalake/fusion
"""

from __future__ import annotations

import argparse
import json

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

MAX_BLOC = 50
PAIR_SCHEMA = T.StructType([
    T.StructField("uid_a", T.StringType()), T.StructField("uid_b", T.StringType()),
    T.StructField("score", T.DoubleType()), T.StructField("niveau", T.StringType()),
])


def _name_key(prenom: str | None, nom: str | None) -> str:
    from fusion.cleaning import name_key

    return name_key(f"{prenom or ''} {nom or ''}")


def candidate_pairs(clients: DataFrame) -> DataFrame:
    name_key_udf = F.udf(_name_key, T.StringType())
    c = clients.withColumn("nom_cle", name_key_udf("prenom", "nom"))
    keys = c.select("client_uid", F.explode(F.array_compact(F.array(
        F.when(F.col("email").isNotNull(), F.concat(F.lit("e:"), "email")),
        F.when(F.col("telephone").isNotNull(), F.concat(F.lit("t:"), "telephone")),
        F.when((F.col("nom_cle") != "") & F.col("date_naissance").isNotNull(),
               F.concat(F.lit("n:"), "nom_cle", F.lit("|"), "date_naissance")),
        F.when((F.col("nom_cle") != "") & F.col("code_postal").isNotNull(),
               F.concat(F.lit("a:"), "nom_cle", F.lit("|"), "code_postal")),
    ))).alias("cle"))
    sizes = keys.groupBy("cle").count().filter(F.col("count") <= MAX_BLOC)
    keys = keys.join(sizes.select("cle"), "cle")
    a, b = keys.alias("a"), keys.alias("b")
    return (a.join(b, (F.col("a.cle") == F.col("b.cle")) & (F.col("a.client_uid") < F.col("b.client_uid")))
            .select(F.col("a.client_uid").alias("uid_a"), F.col("b.client_uid").alias("uid_b")).distinct())


def score_pairs(spark: SparkSession, clients: DataFrame, pairs: DataFrame) -> DataFrame:
    cols = ["client_uid", "nom", "prenom", "date_naissance", "email", "telephone", "adresse", "code_postal"]
    small = clients.select(*cols)
    joined = (pairs.join(small.select(*[F.col(x).alias(f"a_{x}") for x in cols]), F.col("uid_a") == F.col("a_client_uid"))
              .join(small.select(*[F.col(x).alias(f"b_{x}") for x in cols]), F.col("uid_b") == F.col("b_client_uid")))

    def score_partition(rows):
        from fusion.cleaning import name_key
        from fusion.processing.dedup import SEUIL_CERTAIN, SEUIL_PROBABLE, compare, score

        for r in rows:
            x = {k: r[f"a_{k}"] for k in cols}
            y = {k: r[f"b_{k}"] for k in cols}
            x["nom_cle"] = name_key(f"{x['prenom'] or ''} {x['nom'] or ''}")
            y["nom_cle"] = name_key(f"{y['prenom'] or ''} {y['nom'] or ''}")
            s = score(compare(x, y))
            if s >= SEUIL_PROBABLE:
                yield (r["uid_a"], r["uid_b"], float(s), "CERTAIN" if s >= SEUIL_CERTAIN else "PROBABLE")

    return spark.createDataFrame(joined.rdd.mapPartitions(score_partition), PAIR_SCHEMA)


def main(argv: list[str] | None = None) -> dict[str, int]:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", required=True)
    ap.add_argument("--master", default=None)
    args = ap.parse_args(argv)
    builder = SparkSession.builder.appName("fusion-doublons")
    if args.master:
        builder = builder.master(args.master)
    spark = builder.getOrCreate()
    lake = args.lake.rstrip("/")
    clients = spark.read.parquet(f"{lake}/silver/client")
    paires = score_pairs(spark, clients, candidate_pairs(clients)).cache()
    paires.write.mode("overwrite").parquet(f"{lake}/silver/paire_doublon")
    counts = {r["niveau"]: r["count"] for r in paires.groupBy("niveau").count().collect()}
    print(json.dumps(counts))
    return counts


if __name__ == "__main__":
    main()
