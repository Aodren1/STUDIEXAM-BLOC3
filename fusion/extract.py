"""Étape 1 — rôle Talend : extraction des bases sources et publication sur le bus.

Chaque ligne source devient un événement au format enveloppe « à la Debezium » :
    {"schema": "fusion.src.v1", "op": "r", "ts_ms": …, "source": {"systeme": "AB", "table": "AB_CLIENT"},
     "key": "AB-100001", "after": {…colonnes source brutes…}}
La clé de message est l'identifiant préfixé : toutes les versions d'une même ligne vont dans la même
partition, donc dans l'ordre.

Extraction incrémentale : un filigrane (watermark) par table mémorise la plus grande valeur de la colonne
de suivi déjà publiée ; seules les lignes au-delà sont relues au passage suivant (équivalent du
tOracleInput / tMSSqlInput avec une requête paramétrée par contexte dans un job Talend).
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fusion.bus import Bus, topic_for
from fusion.mapping import MappingSpec
from fusion.sources.generator import AB_DB, AP_DB, DDL_SQLITE
from fusion.sqlsafe import ident

SOURCE_DB = {"AB": AB_DB, "AP": AP_DB}
SYSTEME = {"AB": "ORACLE_ABASSURANCE", "AP": "SQLSERVER_ASSUREPLUS"}

#: Colonne de suivi de l'extraction incrémentale, par table source.
#: Les clés AB et AP sont monotones, sauf AP_PAYMENTS dont la clé est un GUID : on suit alors l'horodatage.
WATERMARKS = {
    "AB_CLIENT": "AB_CLIENT_ID",
    "AB_CONTRAT": "AB_POLICY_NUMBER",
    "AB_SINISTRE": "AB_CLAIM_ID",
    "AB_PAIEMENT": "AB_PAYMENT_ID",
    "AP_USERS": "AP_USER_ID",
    "AP_CONTRACTS": "AP_CONTRACT_REF",
    "AP_CLAIMS": "AP_SINISTRE_NUM",
    "AP_PAYMENTS": "AP_PAYMENT_DATETIME",
}
#: Colonnes non uniques : la borne est inclusive (>=) pour ne perdre aucune ligne arrivée dans la même seconde
#: que la dernière lue. Les lignes de la borne sont donc republiées : le consommateur doit être idempotent.
INCLUSIVE = {"AP_PAYMENTS"}


@dataclass
class ExtractStats:
    table: str
    topic: str
    lignes: int = 0
    somme_controle: float = 0.0  # somme d'une colonne montant : réconciliation source ↔ cible
    watermark: Any = None
    duree_s: float = 0.0


@dataclass
class ExtractState:
    path: Path
    marks: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> ExtractState:
        marks = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return cls(path, marks)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.marks, indent=1), encoding="utf-8")


MONTANTS = {
    "AB_CONTRAT": "AB_PRIME_ANNUELLE",
    "AB_SINISTRE": "AB_MONTANT_ESTIME",
    "AB_PAIEMENT": "AB_MONTANT",
    "AP_CONTRACTS": "AP_MONTHLY_PREMIUM",
    "AP_CLAIMS": "AP_ESTIMATED_AMOUNT",
    "AP_PAYMENTS": "AP_AMOUNT_PAID",
}


def envelope(src: str, table: str, key: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "fusion.src.v1",
        "op": "r",
        "ts_ms": int(time.time() * 1000),
        "source": {"systeme": src, "db": SYSTEME[src], "table": table},
        "key": key,
        "after": row,
    }


def extract_table(
    con: sqlite3.Connection,
    src: str,
    entite: str,
    spec: MappingSpec,
    bus: Bus,
    *,
    state: ExtractState | None = None,
    batch_size: int = 5000,
) -> ExtractStats:
    e = spec.entites[entite]
    table = e.tables[src]
    key_col = next(ch.colonnes[src] for ch in e.champs if ch.cible == e.cle)
    mark_col = WATERMARKS[table]
    topic = topic_for(src, entite)
    stats = ExtractStats(table, topic)
    t0 = time.perf_counter()

    columns = [c.split()[0] for c in DDL_SQLITE[table].split(", ")]
    table, key_col, mark_col = ident(table, DDL_SQLITE), ident(key_col, columns), ident(mark_col, columns)
    # identifiants en liste blanche (ident) (bandit B608)
    sql = f"SELECT * FROM {table}"  # nosec B608  # noqa: S608
    params: tuple[Any, ...] = ()
    previous = state.marks.get(table) if state else None
    if previous is not None:
        sql += f" WHERE {mark_col} {'>=' if table in INCLUSIVE else '>'} ?"
        params = (previous,)
    sql += f" ORDER BY {mark_col}, {key_col}"

    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    amount_col = MONTANTS.get(table)
    while True:
        chunk = cur.fetchmany(batch_size)
        if not chunk:
            break
        for values in chunk:
            row = dict(zip(cols, values, strict=True))
            key = f"{src}-{row[key_col]}"
            bus.produce(topic, key, envelope(src, table, key, row))
            stats.lignes += 1
            if amount_col and row[amount_col] is not None:
                stats.somme_controle += float(row[amount_col])
            stats.watermark = row[mark_col]
    bus.flush()
    stats.somme_controle = round(stats.somme_controle, 2)
    if state is not None and stats.watermark is not None:
        state.marks[table] = stats.watermark
    stats.duree_s = round(time.perf_counter() - t0, 3)
    return stats


def extract_all(
    sources_dir: Path, spec: MappingSpec, bus: Bus, state: ExtractState | None = None
) -> list[ExtractStats]:
    """Extrait les 8 tables (ordre : référentiels parents avant enfants) et publie sur les 8 topics src.*."""
    out = []
    for src in ("AB", "AP"):
        con = sqlite3.connect(f"file:{sources_dir / SOURCE_DB[src]}?mode=ro", uri=True)
        try:
            for entite in ("client", "contrat", "sinistre", "paiement"):
                out.append(extract_table(con, src, entite, spec, bus, state=state))
        finally:
            con.close()
    if state is not None:
        state.save()
    return out
