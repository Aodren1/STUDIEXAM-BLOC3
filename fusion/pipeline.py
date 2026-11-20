"""Orchestration du pipeline : sources → extraction (Talend) → bus (Kafka) → silver / doublons / gold (Spark)
→ lac (HDFS) → entrepôt → tableau de bord.

Chaque exécution produit un rapport (durées, volumes, réconciliation source ↔ silver) écrit dans le lac et
lu par l'onglet « Qualité et exécution » du tableau de bord. En production, l'enchaînement est porté par un
DAG Airflow (infra/airflow/dag_fusion.py) qui appelle ces mêmes étapes.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from fusion.bus import open_bus
from fusion.config import Settings
from fusion.extract import ExtractState, extract_all
from fusion.lake import LocalLake, open_lake
from fusion.mapping import MappingSpec
from fusion.processing.dedup import run_dedup
from fusion.processing.gold import build_gold
from fusion.processing.silver import run_silver
from fusion.quality import Anomalie, QualityLog
from fusion.sources.generator import AB_DB, DDL_SQLITE, TRUTH_FILE, generate
from fusion.sqlsafe import ident
from fusion.warehouse import build_warehouse

log = logging.getLogger("fusion.pipeline")

ENTITE_TABLES = {
    "client": ("AB_CLIENT", "AP_USERS"),
    "contrat": ("AB_CONTRAT", "AP_CONTRACTS"),
    "sinistre": ("AB_SINISTRE", "AP_CLAIMS"),
    "paiement": ("AB_PAIEMENT", "AP_PAYMENTS"),
}


class ReconciliationError(RuntimeError):
    """Les volumes ou montants du silver ne correspondent pas aux sources : on bloque la publication."""


@contextmanager
def _step(timings: list[dict[str, Any]], name: str):
    t0 = time.perf_counter()
    log.info("étape %s : début", name)
    try:
        yield
    finally:  # durée enregistrée même si l'étape échoue (réconciliation bloquante, etc.)
        timings.append({"etape": name, "duree_s": round(time.perf_counter() - t0, 3)})
        log.info("étape %s : %.2f s", name, timings[-1]["duree_s"])


def seed(settings: Settings, force: bool = False) -> dict[str, int] | None:
    if not force and (settings.sources_dir / AB_DB).exists():
        return None
    return generate(settings.sources_dir, scale=settings.seed_scale, ref_date=settings.ref_date)


def extract_only(settings: Settings) -> list[dict[str, Any]]:
    """Étape d'extraction seule (tâche Airflow « extraction_talend_vers_kafka »)."""
    seed(settings)
    bus = open_bus(settings)
    try:
        state = ExtractState.load(settings.data_dir / "extract_state.json")
        return [s.__dict__ for s in extract_all(settings.sources_dir, MappingSpec.load(), bus, state)]
    finally:
        bus.close()


def reconcile(extracted_totals: dict[str, dict[str, Any]], tables: dict[str, list[dict]]) -> list[dict[str, Any]]:
    """Contrôle de complétude : lignes uniques publiées par table source = lignes silver de la source."""
    out = []
    for entite, (t_ab, t_ap) in ENTITE_TABLES.items():
        silver = Counter(r["source"] for r in tables[entite])
        for table, src in ((t_ab, "AB"), (t_ap, "AP")):
            expected = extracted_totals.get(table, {}).get("lignes", 0)
            out.append(
                {
                    "table_source": table,
                    "entite": entite,
                    "lignes_source": expected,
                    "lignes_silver": silver.get(src, 0),
                    "ecart": silver.get(src, 0) - expected,
                }
            )
    return out


def _source_totals(settings: Settings) -> dict[str, dict[str, Any]]:
    """Volumes de référence lus directement dans les bases sources (vérité de la réconciliation)."""
    import sqlite3

    from fusion.extract import SOURCE_DB

    out = {}
    for src, db in SOURCE_DB.items():
        con = sqlite3.connect(f"file:{settings.sources_dir / db}?mode=ro", uri=True)
        try:
            for tables in ENTITE_TABLES.values():
                table = tables[0] if src == "AB" else tables[1]
                # liste blanche (bandit B608)
                sql = f"SELECT COUNT(*) FROM {ident(table, DDL_SQLITE)}"  # nosec B608  # noqa: S608
                out[table] = {"lignes": con.execute(sql).fetchone()[0]}
        finally:
            con.close()
    return out


def _load_anomalies(lake) -> QualityLog:
    previous = QualityLog()
    for a in lake.read_table("silver", "anomalie_qualite"):
        previous.anomalies.append(Anomalie(**a))
    return previous


def run(settings: Settings | None = None, *, incremental: bool = True, strict: bool = True) -> dict[str, Any]:
    """Exécute le pipeline complet et retourne le rapport d'exécution."""
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    timings: list[dict[str, Any]] = []
    t_start = time.perf_counter()

    with _step(timings, "sources"):
        seeded = seed(settings)
    spec = MappingSpec.load()
    bus = open_bus(settings)
    lake = open_lake(settings)
    if not incremental:
        _reset_local_state(settings)
    # mode complet : on repart d'un état vide, mais on l'ENREGISTRE (correctif FUS-9) pour que l'exécution
    # incrémentale suivante ne republie pas toutes les lignes déjà chargées
    state = ExtractState.load(settings.data_dir / "extract_state.json")
    try:
        with _step(timings, "extraction (Talend → Kafka)"):
            extracted = extract_all(settings.sources_dir, spec, bus, state)
        with _step(timings, "silver (Spark)"):
            silver = run_silver(bus, spec, lake, run_id, incremental=incremental)
    finally:
        bus.close()
    tables = silver.store.tables
    quality = _load_anomalies(lake) if incremental else QualityLog()
    quality.merge_unique(silver.log)
    lake.write_table("silver", "anomalie_qualite", [a.__dict__ for a in quality.anomalies])

    reconciliation = reconcile(_source_totals(settings), tables)
    ecarts = [r for r in reconciliation if r["ecart"]]
    if ecarts and strict:
        raise ReconciliationError(f"réconciliation en échec : {ecarts}")

    truth_file = settings.sources_dir / TRUTH_FILE
    truth = (
        [tuple(p) for p in json.loads(truth_file.read_text(encoding="utf-8"))["paires"]]
        if truth_file.exists()
        else None
    )
    with _step(timings, "doublons (Spark)"):
        dedup = run_dedup(tables["client"], truth)
        lake.write_table("silver", "paire_doublon", dedup.paires)
        lake.write_table("silver", "personne", dedup.personnes)
    with _step(timings, "entrepôt"):
        wh_counts = build_warehouse(settings.warehouse_path, tables, dedup, quality)

    report = {
        "run_id": run_id,
        "date_execution": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": "incrémental" if incremental else "complet",
        "bus": settings.bus,
        "lac": settings.lake,
        "sources_generees": seeded,
        "extraction": [s.__dict__ for s in extracted],
        "silver": {
            "consommes": silver.consommes,
            "quarantaine": silver.quarantaine,
            "bronze": silver.bronze,
            "remplacements": silver.remplacements,
        },
        "entrepot": wh_counts,
        "duree_totale_s": None,
        "etapes": timings,
    }
    with _step(timings, "gold (Spark)"):
        gold = build_gold(tables, dedup, quality, settings.ref_date, reconciliation=reconciliation, run=report)
        lake.write_json("gold", "dashboard.json", gold)
        lake.write_table("gold", "paires_doublons_detail", _pairs_detail(dedup, tables["client"]))
    report["duree_totale_s"] = round(time.perf_counter() - t_start, 2)
    gold["qualite"]["execution"] = report
    lake.write_json("gold", "dashboard.json", gold)
    if settings.lake != "local":
        # le lac de référence est HDFS ; le tableau de bord lit une copie locale publiée (cache de restitution)
        local = LocalLake(settings.lake_dir)
        local.write_json("gold", "dashboard.json", gold)
        local.write_table("gold", "paires_doublons_detail", _pairs_detail(dedup, tables["client"]))
    settings.run_report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return report


def _pairs_detail(dedup, clients: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Paires à valider par le métier (données personnelles : servies au seul rôle administrateur)."""
    by_uid = {c["client_uid"]: c for c in clients}
    out = []
    for p in sorted(dedup.paires, key=lambda p: (p["niveau"] != "PROBABLE", -p["score"]))[:300]:
        a, b = by_uid[p["uid_a"]], by_uid[p["uid_b"]]
        out.append(
            {
                **{k: p[k] for k in ("uid_a", "uid_b", "type_paire", "score", "niveau")},
                "nom_a": f"{a['prenom'] or ''} {a['nom'] or ''}".strip(),
                "nom_b": f"{b['prenom'] or ''} {b['nom'] or ''}".strip(),
                "naissance_a": a["date_naissance"],
                "naissance_b": b["date_naissance"],
                "email_a": a["email"],
                "email_b": b["email"],
                "cp_a": a["code_postal"],
                "cp_b": b["code_postal"],
                "criteres": [k for k in ("email", "telephone", "nom", "naissance", "adresse") if p[f"c_{k}"] >= 0.7],
            }
        )
    return out


def _reset_local_state(settings: Settings) -> None:
    """Mode complet : on repart d'un bus, d'un lac et d'un filigrane vides (rejeu intégral)."""
    import shutil

    for d in (settings.data_dir / "bus", settings.lake_dir):
        if d.exists():
            shutil.rmtree(d)
    f = settings.data_dir / "extract_state.json"
    if f.exists():
        f.unlink()
