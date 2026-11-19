"""Job Spark — zone silver : topics Kafka src.* → tables Parquet du modèle unifié sur HDFS.

Les règles de nettoyage ne sont PAS réécrites en Spark : le job distribue le moteur de mapping Python
(fusion.mapping) sur les partitions (mapPartitions). La règle testée unitairement est donc exactement
celle exécutée sur le cluster ; le paquet fusion est envoyé aux exécuteurs avec --py-files.

    spark-submit --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.3 --py-files fusion.zip \\
        jobs/spark/silver_job.py --kafka abf-flux-kafka:9092 --out hdfs://abf-stockage-namenode:8020/datalake/fusion

Mode fichier (tests, CI) : --bus-dir <répertoire du journal local> à la place de --kafka.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from pathlib import Path

from pyspark.sql import DataFrame, Row, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

ENTITES = ("client", "contrat", "sinistre", "paiement")
TOPICS = [f"src.{s}.{e}" for e in ENTITES for s in ("ab", "ap")]
EVENT_SCHEMA = T.StructType([
    T.StructField("topic", T.StringType()), T.StructField("partition", T.IntegerType()),
    T.StructField("offset", T.LongType()), T.StructField("json", T.StringType()),
])
OUT_SCHEMA = T.StructType([
    T.StructField("kind", T.StringType()),  # enregistrement | anomalie | quarantaine
    T.StructField("entite", T.StringType()), T.StructField("uid", T.StringType()),
    T.StructField("partition", T.IntegerType()), T.StructField("offset", T.LongType()),
    T.StructField("payload", T.StringType()),
])


def spark_type(logical: str) -> T.DataType:
    """Type Spark d'un attribut, d'après le type logique déclaré dans mapping.toml."""
    t = logical.upper()
    if t.startswith(("DECIMAL", "NUMBER")):
        return T.DoubleType()
    if t.startswith(("SMALLINT", "INT")):
        return T.IntegerType()
    if t == "BOOLEAN":
        return T.BooleanType()
    return T.StringType()  # VARCHAR, CHAR, TEXT, DATE et TIMESTAMP (ISO 8601, convertis en aval)


def entity_schema(spec_raw: dict, entite: str) -> T.StructType:
    fields = [T.StructField("source", T.StringType())]
    fields += [T.StructField(ch["cible"], spark_type(ch.get("type", ""))) for ch in spec_raw[entite]["champ"]]
    return T.StructType(fields)


# ------------------------------------------------------------------ lecture des événements
def read_kafka(spark: SparkSession, bootstrap: str) -> DataFrame:
    return (spark.read.format("kafka")
            .option("kafka.bootstrap.servers", bootstrap)
            .option("subscribe", ",".join(TOPICS))
            .option("startingOffsets", "earliest").option("endingOffsets", "latest")
            .load()
            .select("topic", "partition", "offset", F.col("value").cast("string").alias("json")))


def read_file_bus(spark: SparkSession, bus_dir: Path) -> DataFrame:
    """Journal local (bus/<topic>/p<n>.jsonl) : l'offset est le numéro de ligne, comme dans FileBus."""
    rows = []
    for topic in TOPICS:
        for f in sorted((bus_dir / topic).glob("p*.jsonl")):
            p = int(f.stem[1:])
            with open(f, encoding="utf-8") as fh:
                for offset, line in enumerate(fh):
                    try:
                        value = json.dumps(json.loads(line)["v"], ensure_ascii=False)
                    except (json.JSONDecodeError, KeyError, TypeError):
                        value = line.rstrip("\n")
                    rows.append((topic, p, offset, value))
    return spark.createDataFrame(rows, EVENT_SCHEMA)


# ------------------------------------------------------------------ transformation distribuée
def harmonise_partition(rows: Iterator[Row], spec_raw: dict) -> Iterator[tuple]:
    from fusion.mapping import MappingSpec
    from fusion.processing.silver import valid_envelope
    from fusion.quality import QualityLog

    spec = MappingSpec(spec_raw)
    log = QualityLog()
    for r in rows:
        _, src, entite = r["topic"].split(".")
        try:
            env = json.loads(r["json"])
        except (json.JSONDecodeError, TypeError):
            env = None
        if not valid_envelope(env):
            log.flag("DQ16", src.upper(), entite, f"{r['topic']}@{r['partition']}:{r['offset']}", r["json"])
            yield ("quarantaine", entite, None, r["partition"], r["offset"], (r["json"] or "")[:2000])
            continue
        rec = spec.map_record(entite, src.upper(), env["after"], log)
        yield ("enregistrement", entite, rec[f"{entite}_uid"], r["partition"], r["offset"],
               json.dumps(rec, ensure_ascii=False))
    for a in log.anomalies:
        yield ("anomalie", a.entite, a.uid, None, None, json.dumps(a.__dict__, ensure_ascii=False))


def build_silver(spark: SparkSession, events: DataFrame, spec_raw: dict) -> dict[str, DataFrame]:
    out = spark.createDataFrame(events.rdd.mapPartitions(lambda it: harmonise_partition(it, spec_raw)), OUT_SCHEMA)
    out = out.cache()
    tables: dict[str, DataFrame] = {}
    for entite in ENTITES:
        recs = out.filter((F.col("kind") == "enregistrement") & (F.col("entite") == entite))
        tables[entite] = (recs.select(F.from_json("payload", entity_schema(spec_raw, entite)).alias("r"))
                          .select("r.*"))
    tables = flag_orphans(tables)
    tables["anomalie_qualite"] = (out.filter(F.col("kind") == "anomalie")
                                  .select(F.from_json("payload", "code STRING, source STRING, entite STRING, "
                                                                 "uid STRING, valeur STRING").alias("a")).select("a.*"))
    tables["quarantaine"] = out.filter(F.col("kind") == "quarantaine").select("entite", "partition", "offset", "payload")
    return tables


def flag_orphans(t: dict[str, DataFrame]) -> dict[str, DataFrame]:
    """Même règle que le moteur local : orphelins en cascade (un contrat orphelin isole ses enfants)."""
    clients = t["client"].select(F.col("client_uid").alias("_parent"))
    contrat = (t["contrat"].join(clients, t["contrat"].client_uid == clients._parent, "left")
               .withColumn("est_orphelin", F.col("_parent").isNull()).drop("_parent"))
    valid = contrat.filter(~F.col("est_orphelin")).select(F.col("contrat_uid").alias("_parent"))
    out = {"client": t["client"], "contrat": contrat}
    for e in ("sinistre", "paiement"):
        out[e] = (t[e].join(valid, t[e].contrat_uid == valid._parent, "left")
                  .withColumn("est_orphelin", F.col("_parent").isNull()).drop("_parent"))
    return out


def main(argv: list[str] | None = None) -> dict[str, int]:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--kafka", help="bootstrap servers Kafka")
    src.add_argument("--bus-dir", type=Path, help="journal local (tests)")
    ap.add_argument("--out", required=True, help="racine du lac (hdfs://… ou chemin local)")
    ap.add_argument("--master", default=None)
    args = ap.parse_args(argv)

    builder = SparkSession.builder.appName("fusion-silver").config("spark.sql.session.timeZone", "Europe/Paris")
    if args.master:
        builder = builder.master(args.master)
    spark = builder.getOrCreate()
    from fusion.mapping import MappingSpec

    spec_raw = MappingSpec.load().raw
    events = read_kafka(spark, args.kafka) if args.kafka else read_file_bus(spark, args.bus_dir)
    tables = build_silver(spark, events, spec_raw)
    counts = {}
    for name, df in tables.items():
        df.write.mode("overwrite").parquet(f"{args.out.rstrip('/')}/silver/{name}")
        counts[name] = df.count()
    print(json.dumps({"silver": counts}, ensure_ascii=False))
    return counts


if __name__ == "__main__":
    main()
