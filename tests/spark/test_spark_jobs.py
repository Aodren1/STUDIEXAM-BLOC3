"""Équivalence des deux moteurs : les jobs Spark produisent les mêmes tables et indicateurs que le moteur local.

Exécuté dans la CI (Java 17 + pyspark) ; ignoré sur un poste sans JVM.
"""

from __future__ import annotations

import shutil
from collections import Counter
from datetime import date

import pytest

pyspark = pytest.importorskip("pyspark")
if shutil.which("java") is None:  # pragma: no cover - dépend du poste
    pytest.skip("JVM absente : tests Spark exécutés dans la CI", allow_module_level=True)

from fusion.bus.filelog import FileBus  # noqa: E402
from fusion.extract import extract_all  # noqa: E402
from fusion.lake import LocalLake  # noqa: E402
from fusion.mapping import MappingSpec  # noqa: E402
from fusion.processing.dedup import run_dedup  # noqa: E402
from fusion.processing.gold import build_gold  # noqa: E402
from fusion.processing.silver import run_silver  # noqa: E402

pytestmark = pytest.mark.spark
REF = date(2026, 10, 31)


@pytest.fixture(scope="module")
def spark():
    from pyspark.sql import SparkSession

    s = (
        SparkSession.builder.master("local[2]")
        .appName("tests-fusion")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield s
    s.stop()


@pytest.fixture(scope="module")
def engines(small_sources, tmp_path_factory, spark):
    """Même jeu d'événements traité par le moteur local et par les jobs Spark."""
    base = tmp_path_factory.mktemp("spark")
    spec = MappingSpec.load()
    bus = FileBus(base / "bus")
    extract_all(small_sources, spec, bus)
    bus.close()
    local = run_silver(FileBus(base / "bus"), spec, LocalLake(base / "lake-local"), "t", incremental=False)

    from jobs.spark import silver_job

    lake = str(base / "lake-spark")
    silver_job.main(["--bus-dir", str(base / "bus"), "--out", lake])
    return local, lake


def test_silver_memes_volumes(engines, spark):
    local, lake = engines
    for entite in ("client", "contrat", "sinistre", "paiement"):
        assert spark.read.parquet(f"{lake}/silver/{entite}").count() == local.store.count(entite), entite


def test_silver_memes_orphelins_et_anomalies(engines, spark):
    local, lake = engines
    for entite in ("contrat", "sinistre", "paiement"):
        n_spark = spark.read.parquet(f"{lake}/silver/{entite}").filter("est_orphelin").count()
        assert n_spark == sum(1 for r in local.store.tables[entite] if r["est_orphelin"]), entite
    anomalies = Counter(r["code"] for r in spark.read.parquet(f"{lake}/silver/anomalie_qualite").collect())
    local_counts = Counter(local.log.counts())
    for code in ("DQ01", "DQ02", "DQ03", "DQ04", "DQ05", "DQ07", "DQ08", "DQ09", "DQ11", "DQ12"):
        assert anomalies.get(code, 0) == local_counts.get(code, 0), code


def test_gold_memes_indicateurs(engines, spark):
    from jobs.spark import gold_job

    local, lake = engines
    out = gold_job.main(["--lake", lake, "--ref", REF.isoformat()])
    gold = build_gold(local.store.tables, run_dedup(local.store.tables["client"]), local.log, REF)
    actifs = Counter()
    for r in out["contrats_actifs_par_branche"]:
        actifs[(r["branche"], r["source"])] = r["contrats"]
    block = gold["contrats"]["par_branche"]
    for i, branche in enumerate(block["categories"]):
        for src in ("AB", "AP"):
            assert actifs.get((branche, src), 0) == block["series"][src][i]
    total_spark = sum(r["encaisse"] for r in out["encaissements_mensuels"])
    total_local = sum(sum(v) for v in gold["finances"]["encaissements_mensuels"]["series"].values())
    assert total_spark >= total_local - 0.01  # le gold local ne garde que 22 mois, Spark tout l'historique


def test_dedoublonnage_distribue(engines, spark):
    from jobs.spark import dedup_job

    local, lake = engines
    counts = dedup_job.main(["--lake", lake])
    ref = run_dedup(local.store.tables["client"]).stats
    assert counts.get("CERTAIN", 0) == ref["paires_certaines"]
    assert counts.get("PROBABLE", 0) == ref["paires_probables"]


def test_silver_spark_idempotent_apres_relivraison(small_sources, tmp_path, spark):
    """Correctif FUS-9 côté Spark : des événements publiés deux fois ne doublent pas le silver."""
    from jobs.spark import silver_job

    spec = MappingSpec.load()
    bus = FileBus(tmp_path / "bus")
    extract_all(small_sources, spec, bus)
    extract_all(small_sources, spec, bus)  # republication complète (rejeu)
    bus.close()
    counts = silver_job.main(["--bus-dir", str(tmp_path / "bus"), "--out", str(tmp_path / "lake")])
    local = run_silver(FileBus(tmp_path / "bus"), spec, LocalLake(tmp_path / "lake-local"), "t", incremental=False)
    for entite in ("client", "contrat", "sinistre", "paiement"):
        assert counts[entite] == local.store.count(entite), entite
