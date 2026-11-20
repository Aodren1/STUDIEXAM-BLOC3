"""Journal de messages local au comportement de Kafka : topics partitionnés, offsets, groupes de consommateurs.

Disposition sur disque (lisible et rejouable) :
    bus/<topic>/p0.jsonl …            une ligne JSON par message, append-only
    bus/_offsets/<groupe>.json        offsets validés par topic et partition

Le partitionnement par clé (crc32 % n) garantit, comme Kafka, l'ordre des messages d'une même clé.
Sert au développement, aux tests et au serveur de démonstration ; la cible utilise KafkaBus.
"""

from __future__ import annotations

import json
import re
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fusion.bus import Message

# Noms de topic et de groupe : ils deviennent des noms de fichiers, donc ni séparateur ni « .. ».
_NOM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _nom_valide(nom: str, quoi: str) -> str:
    if not isinstance(nom, str) or not _NOM.fullmatch(nom) or ".." in nom:
        raise ValueError(f"{quoi} invalide : {nom!r}")
    return nom


class FileBus:
    def __init__(self, root: Path, partitions: int = 3):
        self.root = Path(root)
        self.partitions = partitions
        self.root.mkdir(parents=True, exist_ok=True)
        self._handles: dict[tuple[str, int], Any] = {}
        self._counts: dict[tuple[str, int], int] = {}
        self._positions: dict[tuple[str, str], dict[int, int]] = {}

    # ------------------------------------------------------------------ production
    def partition_for(self, key: str | None) -> int:
        return zlib.crc32((key or "").encode("utf-8")) % self.partitions

    def _file(self, topic: str, partition: int) -> Path:
        return self.root / _nom_valide(topic, "topic") / f"p{int(partition)}.jsonl"

    def produce(self, topic: str, key: str | None, value: dict[str, Any]) -> None:
        p = self.partition_for(key)
        h = self._handles.get((topic, p))
        if h is None:
            self._file(topic, p).parent.mkdir(parents=True, exist_ok=True)
            h = self._handles[(topic, p)] = open(self._file(topic, p), "a", encoding="utf-8")  # noqa: SIM115 - descripteur gardé ouvert, fermé par close()
        h.write(json.dumps({"k": key, "v": value}, ensure_ascii=False, separators=(",", ":")) + "\n")

    def produce_raw(self, topic: str, key: str | None, raw_line: str) -> None:
        """Écrit une ligne brute (sert aux tests de messages corrompus)."""
        p = self.partition_for(key)
        self._file(topic, p).parent.mkdir(parents=True, exist_ok=True)
        with open(self._file(topic, p), "a", encoding="utf-8") as fh:
            fh.write(raw_line + "\n")

    def flush(self) -> None:
        for h in self._handles.values():
            h.flush()

    def close(self) -> None:
        for h in self._handles.values():
            h.close()
        self._handles.clear()

    # ------------------------------------------------------------------ consommation
    def _offsets_file(self, group: str) -> Path:
        return self.root / "_offsets" / f"{_nom_valide(group, 'groupe')}.json"

    def _load_offsets(self, group: str) -> dict[str, dict[str, int]]:
        f = self._offsets_file(group)
        if not f.exists():
            return {}
        brut = json.loads(f.read_text(encoding="utf-8"))
        # Le fichier est relu puis réécrit à chaque commit : on n'en garde que des noms de topic
        # valides et des entiers, un fichier altéré ne peut donc rien injecter.
        return {
            topic: {str(int(p)): int(o) for p, o in parts.items()}
            for topic, parts in brut.items()
            if isinstance(parts, dict) and _NOM.fullmatch(topic) and ".." not in topic
        }

    def committed(self, topic: str, group: str) -> dict[int, int]:
        return {int(p): o for p, o in self._load_offsets(group).get(topic, {}).items()}

    def end_offsets(self, topic: str) -> dict[int, int]:
        self.flush()
        out = {}
        for p in range(self.partitions):
            f = self._file(topic, p)
            if f.exists():
                with open(f, encoding="utf-8") as fh:
                    out[p] = sum(1 for _ in fh)
            else:
                out[p] = 0
        return out

    def consume(self, topic: str, group: str, max_messages: int | None = None) -> Iterator[Message]:
        """Lit à partir des offsets validés du groupe ; la position atteinte n'est validée que par commit()."""
        self.flush()
        start = self.committed(topic, group)
        position = dict(start)
        self._positions[(topic, group)] = position
        delivered = 0
        for p in range(self.partitions):
            f = self._file(topic, p)
            if not f.exists():
                continue
            with open(f, encoding="utf-8") as fh:
                for offset, line in enumerate(fh):
                    if offset < start.get(p, 0):
                        continue
                    if max_messages is not None and delivered >= max_messages:
                        return
                    try:
                        payload = json.loads(line)
                        msg = Message(topic, p, offset, payload.get("k"), payload.get("v"))
                    except (json.JSONDecodeError, AttributeError):
                        msg = Message(topic, p, offset, None, line.rstrip("\n"))
                    position[p] = offset + 1
                    delivered += 1
                    yield msg

    def commit(self, topic: str, group: str) -> None:
        position = self._positions.get((topic, group))
        if position is None:
            return
        offsets = self._load_offsets(group)
        offsets[topic] = {str(p): o for p, o in sorted(position.items())}
        f = self._offsets_file(group)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(offsets, indent=1), encoding="utf-8")
        tmp.replace(f)  # écriture atomique : un arrêt brutal ne corrompt pas les offsets

    def reset(self, group: str) -> None:
        f = self._offsets_file(group)
        if f.exists():
            f.unlink()
