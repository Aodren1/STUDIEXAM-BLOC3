"""Bus de messages : contrat commun à Apache Kafka et au journal local.

Sémantique retenue (identique à Kafka avec enable.auto.commit=false) : livraison AU MOINS UNE FOIS.
Le consommateur valide (commit) son offset APRÈS avoir traité un lot ; en cas d'arrêt entre traitement
et commit, le lot est relivré. Les traitements en aval doivent donc être idempotents.

    bus = open_bus(settings)                      # FileBus (local, tests) ou KafkaBus (cible)
    bus.produce("src.ab.client", "AB-1", {...})
    for msg in bus.consume("src.ab.client", "spark-silver"): ...
    bus.commit("src.ab.client", "spark-silver")
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

TOPICS_SOURCES = [f"src.{s}.{e}" for e in ("client", "contrat", "sinistre", "paiement") for s in ("ab", "ap")]
TOPIC_DLQ = "dlq.silver"
PARTITIONS = 3


def topic_for(src: str, entite: str) -> str:
    return f"src.{src.lower()}.{entite}"


@dataclass(frozen=True)
class Message:
    topic: str
    partition: int
    offset: int
    key: str | None
    value: Any  # dict décodé, ou str brute si le JSON est illisible


class Bus(Protocol):
    def produce(self, topic: str, key: str | None, value: dict[str, Any]) -> None: ...

    def flush(self) -> None: ...

    def consume(self, topic: str, group: str, max_messages: int | None = None) -> Iterator[Message]: ...

    def commit(self, topic: str, group: str) -> None: ...

    def end_offsets(self, topic: str) -> dict[int, int]: ...

    def committed(self, topic: str, group: str) -> dict[int, int]: ...

    def close(self) -> None: ...


def lag(bus: Bus, topic: str, group: str) -> int:
    """Retard du groupe : messages publiés et pas encore validés (indicateur de supervision)."""
    end, done = bus.end_offsets(topic), bus.committed(topic, group)
    return sum(max(0, end[p] - done.get(p, 0)) for p in end)


def open_bus(settings) -> Bus:
    if settings.bus == "kafka":
        from fusion.bus.kafka import KafkaBus

        return KafkaBus(settings.kafka_bootstrap)
    from fusion.bus.filelog import FileBus

    return FileBus(settings.data_dir / "bus", partitions=PARTITIONS)
