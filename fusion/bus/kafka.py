"""Adaptateur Apache Kafka (bibliothèque kafka-python), même contrat que FileBus.

Réglages de fiabilité : acks=all et retries côté producteur ; commit manuel des offsets côté consommateur
(livraison au moins une fois). Les topics sont créés à l'avance avec 3 partitions (infra/kafka/topics.sh).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fusion.bus import Message


class KafkaBus:  # pragma: no cover - exercé par tests/test_kafka_integration.py contre un vrai broker (CI)
    def __init__(self, bootstrap: str, poll_timeout_ms: int = 4000):
        from kafka import KafkaProducer

        self.bootstrap = bootstrap
        self.poll_timeout_ms = poll_timeout_ms
        self.producer = KafkaProducer(
            bootstrap_servers=bootstrap,
            acks="all",
            retries=5,
            linger_ms=20,
            key_serializer=lambda k: None if k is None else k.encode("utf-8"),
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )
        self._consumers: dict[tuple[str, str], Any] = {}

    def produce(self, topic: str, key: str | None, value: dict[str, Any]) -> None:
        self.producer.send(topic, key=key, value=value)

    def flush(self) -> None:
        self.producer.flush()

    def _consumer(self, topic: str, group: str):
        from kafka import KafkaConsumer

        c = self._consumers.get((topic, group))
        if c is None:
            c = KafkaConsumer(
                topic,
                bootstrap_servers=self.bootstrap,
                group_id=group,
                enable_auto_commit=False,
                auto_offset_reset="earliest",
                consumer_timeout_ms=self.poll_timeout_ms,
            )
            self._consumers[(topic, group)] = c
        return c

    def consume(self, topic: str, group: str, max_messages: int | None = None) -> Iterator[Message]:
        for delivered, rec in enumerate(self._consumer(topic, group), start=1):
            raw = rec.value.decode("utf-8", errors="replace")
            try:
                value: Any = json.loads(raw)
            except json.JSONDecodeError:
                value = raw
            key = rec.key.decode("utf-8") if rec.key else None
            yield Message(rec.topic, rec.partition, rec.offset, key, value)
            if max_messages is not None and delivered >= max_messages:
                return

    def commit(self, topic: str, group: str) -> None:
        c = self._consumers.get((topic, group))
        if c is not None:
            c.commit()

    def end_offsets(self, topic: str) -> dict[int, int]:
        from kafka import KafkaConsumer, TopicPartition

        probe = KafkaConsumer(bootstrap_servers=self.bootstrap)
        try:
            parts = [TopicPartition(topic, p) for p in sorted(probe.partitions_for_topic(topic) or [])]
            return {tp.partition: o for tp, o in probe.end_offsets(parts).items()}
        finally:
            probe.close()

    def committed(self, topic: str, group: str) -> dict[int, int]:
        from kafka import KafkaConsumer, TopicPartition

        probe = KafkaConsumer(bootstrap_servers=self.bootstrap, group_id=group, enable_auto_commit=False)
        try:
            out = {}
            for p in sorted(probe.partitions_for_topic(topic) or []):
                out[p] = probe.committed(TopicPartition(topic, p)) or 0
            return out
        finally:
            probe.close()

    def close(self) -> None:
        self.producer.flush()
        self.producer.close()
        for c in self._consumers.values():
            c.close()
