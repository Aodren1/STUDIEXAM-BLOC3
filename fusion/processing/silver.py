"""Étape 2 — rôle Spark : consommation des topics src.* et chargement de la zone silver (modèle unifié).

Pour chaque message : contrôle de l'enveloppe (sinon quarantaine DQ16), application du mapping,
écriture dans la table silver de l'entité. Après chargement : contrôles d'intégrité référentielle
(orphelins DQ06, DQ10, DQ14) et règle de gestion DQ15. Les offsets ne sont validés qu'une fois la
zone silver écrite (livraison au moins une fois).

Idempotence (correctif FUS-9) : une même ligne source peut être reçue plusieurs fois (rejeu, borne
inclusive d'un filigrane, relivraison Kafka après un arrêt avant commit). Le silver est donc indexé
par identifiant : chaque réception REMPLACE la version précédente (upsert, la dernière version reçue
l'emporte ; l'ordre est garanti par clé car une clé va toujours dans la même partition).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from fusion.bus import TOPIC_DLQ, Bus, topic_for
from fusion.lake import Lake
from fusion.mapping import MappingSpec
from fusion.processing import ENTITES
from fusion.quality import QualityLog

#: Tolérance de régularisation après résiliation (dernier prélèvement au prorata), validée en recette Bloc 2.
TOLERANCE_FIN_CONTRAT_JOURS = 31
GROUP = "spark-silver"


#: Marqueurs posés par les contrôles d'intégrité, conservés quand une nouvelle version remplace l'ancienne
#: (sinon l'anomalie d'origine serait signalée une seconde fois).
MARQUEURS = ("est_orphelin", "est_tardif")


@dataclass
class SilverStore:
    """Tables silver en mémoire, indexées par identifiant : l'écriture est un upsert idempotent."""

    rows: dict[str, dict[str, dict[str, Any]]] = field(default_factory=lambda: {e: {} for e in ENTITES})
    remplacements: int = 0

    def add(self, entite: str, record: dict[str, Any]) -> None:
        uid = record[f"{entite}_uid"]
        previous = self.rows[entite].get(uid)
        if previous is not None:
            self.remplacements += 1
            for m in MARQUEURS:
                if m in previous and m not in record:
                    record[m] = previous[m]
        self.rows[entite][uid] = record

    @property
    def tables(self) -> dict[str, list[dict[str, Any]]]:
        return {e: list(rows.values()) for e, rows in self.rows.items()}

    def load(self, lake: Lake) -> None:
        for e in ENTITES:
            for rec in lake.read_table("silver", e):
                self.add(e, rec)
        self.remplacements = 0

    def count(self, entite: str) -> int:
        return len(self.rows[entite])


@dataclass
class SilverResult:
    store: SilverStore
    log: QualityLog
    consommes: dict[str, int]
    quarantaine: int
    bronze: dict[str, int]
    remplacements: int = 0  # messages déjà chargés, reçus à nouveau et absorbés par l'upsert


def valid_envelope(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("schema") == "fusion.src.v1"
        and isinstance(value.get("after"), dict)
        and isinstance(value.get("source"), dict)
    )


def run_silver(
    bus: Bus,
    spec: MappingSpec,
    lake: Lake,
    run_id: str,
    *,
    incremental: bool = True,
    commit: bool = True,
    group: str = GROUP,
) -> SilverResult:
    log = QualityLog()
    store = SilverStore()
    if incremental:
        store.load(lake)  # état silver précédent : on y applique les nouveaux événements
    consommes: dict[str, int] = {}
    bronze: dict[str, int] = {}
    quarantaine = 0

    for entite in ENTITES:
        for src in ("AB", "AP"):
            topic = topic_for(src, entite)
            raw_events = []
            n = 0
            for msg in bus.consume(topic, group):
                n += 1
                if not valid_envelope(msg.value):
                    quarantaine += 1
                    log.flag("DQ16", src, entite, f"{topic}@{msg.partition}:{msg.offset}", msg.value)
                    bus.produce(
                        TOPIC_DLQ,
                        msg.key,
                        {
                            "topic": topic,
                            "partition": msg.partition,
                            "offset": msg.offset,
                            "brut": str(msg.value)[:2000],
                        },
                    )
                    continue
                raw_events.append(msg.value)
                store.add(entite, spec.map_record(entite, src, msg.value["after"], log))
            consommes[topic] = n
            if raw_events:  # zone bronze : copie brute immuable, partitionnée par exécution
                lake.write_table("bronze", topic, raw_events, part=f"run={run_id}")
            bronze[topic] = len(raw_events)

    check_integrity(store, log)
    for e in ENTITES:
        lake.write_table("silver", e, store.tables[e])
    bus.flush()
    if commit:
        for entite in ENTITES:
            for src in ("AB", "AP"):
                bus.commit(topic_for(src, entite), group)
    return SilverResult(store, log, consommes, quarantaine, bronze, store.remplacements)


def check_integrity(store: SilverStore, log: QualityLog) -> None:
    """Marque les orphelins (clé étrangère sans parent) et les paiements tardifs ; rien n'est supprimé."""
    clients = {c["client_uid"] for c in store.tables["client"]}
    _flag_orphans(store, log, "contrat", "client_uid", parents=clients, code="DQ06")
    # cascade : un contrat orphelin est isolé, ses sinistres et paiements n'ont donc plus de parent valide
    contrats = {c["contrat_uid"]: c for c in store.tables["contrat"] if not c["est_orphelin"]}
    _flag_orphans(store, log, "sinistre", "contrat_uid", parents=contrats, code="DQ10")
    _flag_orphans(store, log, "paiement", "contrat_uid", parents=contrats, code="DQ14")
    for p in store.tables["paiement"]:
        c = contrats.get(p["contrat_uid"])
        late = False
        if c and c.get("date_fin") and p.get("date_paiement"):
            delta = (date.fromisoformat(p["date_paiement"][:10]) - date.fromisoformat(c["date_fin"])).days
            late = delta > TOLERANCE_FIN_CONTRAT_JOURS
            if late and not p.get("est_tardif"):
                log.flag(
                    "DQ15", p["source"], "paiement", p["paiement_uid"], f"{p['date_paiement'][:10]} > {c['date_fin']}"
                )
        p["est_tardif"] = late


def _flag_orphans(store: SilverStore, log: QualityLog, rel: str, fk: str, *, parents, code: str) -> None:
    for rec in store.tables[rel]:
        orphan = rec[fk] not in parents
        if orphan and not rec.get("est_orphelin"):
            log.flag(code, rec["source"], rel, rec[f"{rel}_uid"], rec[fk])
        rec["est_orphelin"] = orphan
