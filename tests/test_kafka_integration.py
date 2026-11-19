"""Intégration avec un vrai broker Apache Kafka (job « kafka » de la CI, conteneur apache/kafka).

Vérifie que l'adaptateur KafkaBus respecte le même contrat que le journal local : ordre par clé,
commit manuel des offsets, reprise sans perte ni relecture après validation, mesure du retard.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("kafka")
BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP")
pytestmark = [
    pytest.mark.kafka,
    pytest.mark.skipif(not BOOTSTRAP, reason="KAFKA_BOOTSTRAP non défini : broker indisponible"),
]


@pytest.fixture()
def topic():
    from kafka.admin import KafkaAdminClient, NewTopic

    name = f"test.fusion.{uuid.uuid4().hex[:8]}"
    admin = KafkaAdminClient(bootstrap_servers=BOOTSTRAP)
    admin.create_topics([NewTopic(name, num_partitions=3, replication_factor=1)])
    yield name
    admin.delete_topics([name])
    admin.close()


def test_aller_retour_commit_et_retard(topic):
    from fusion.bus import lag
    from fusion.bus.kafka import KafkaBus

    bus = KafkaBus(BOOTSTRAP, poll_timeout_ms=6000)
    for i in range(20):
        bus.produce(topic, f"AB-{i % 4}", {"i": i})
    bus.flush()
    assert sum(bus.end_offsets(topic).values()) == 20

    received = list(bus.consume(topic, "g1"))
    assert len(received) == 20
    for key in ("AB-0", "AB-1"):  # ordre conservé pour une même clé
        values = [m.value["i"] for m in received if m.key == key]
        assert values == sorted(values)
    bus.commit(topic, "g1")
    assert lag(bus, topic, "g1") == 0

    bus.produce(topic, "AB-9", {"i": 99})
    bus.flush()
    assert lag(bus, topic, "g1") == 1
    assert [m.value["i"] for m in bus.consume(topic, "g1")] == [99]  # reprise après le dernier commit
    bus.close()
