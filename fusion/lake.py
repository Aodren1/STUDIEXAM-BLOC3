"""Lac de données : zones bronze (brut), silver (nettoyé, modèle unifié) et gold (agrégats).

Deux implémentations du même contrat :
  LocalLake    système de fichiers local (poste, tests, serveur de démonstration) ;
  WebHdfsLake  HDFS via l'API REST WebHDFS du NameNode (cible), sans dépendance externe.

Format : JSON Lines par table, avec un manifeste _schema.json et un marqueur _SUCCESS
(convention Hadoop : un lecteur ne consomme qu'un répertoire terminé). Les jobs Spark écrivent
la même arborescence en Parquet.
"""

from __future__ import annotations

import json
import shutil
import urllib.parse
import urllib.request
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

ZONES = ("bronze", "silver", "gold")


def _lines(rows: Iterable[dict[str, Any]]) -> tuple[str, int]:
    out, n = [], 0
    for r in rows:
        out.append(json.dumps(r, ensure_ascii=False, separators=(",", ":"), default=str))
        n += 1
    return ("\n".join(out) + "\n") if out else "", n


def _manifest(table: str, n: int, columns: list[str]) -> str:
    return json.dumps({"table": table, "lignes": n, "colonnes": columns, "format": "jsonl",
                       "ecrit_le": datetime.now(UTC).isoformat(timespec="seconds")}, ensure_ascii=False)


class Lake(Protocol):
    def write_table(self, zone: str, table: str, rows: list[dict[str, Any]], part: str = "") -> str: ...

    def read_table(self, zone: str, table: str) -> list[dict[str, Any]]: ...

    def write_json(self, zone: str, name: str, doc: Any) -> str: ...

    def list_tables(self, zone: str) -> list[str]: ...


def _check_zone(zone: str) -> None:
    if zone not in ZONES:
        raise ValueError(f"zone inconnue : {zone}")


class LocalLake:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, zone: str, table: str, part: str = "") -> Path:
        _check_zone(zone)
        d = self.root / zone / table
        return d / part if part else d

    def write_table(self, zone: str, table: str, rows: list[dict[str, Any]], part: str = "") -> str:
        d = self._dir(zone, table, part)
        if d.exists():
            shutil.rmtree(d)  # écriture en mode overwrite de la partition : rejouable
        d.mkdir(parents=True)
        text, n = _lines(rows)
        (d / "part-00000.jsonl").write_text(text, encoding="utf-8")
        (d / "_schema.json").write_text(_manifest(table, n, list(rows[0]) if rows else []), encoding="utf-8")
        (d / "_SUCCESS").touch()
        return str(d)

    def read_table(self, zone: str, table: str) -> list[dict[str, Any]]:
        base = self._dir(zone, table)
        rows: list[dict[str, Any]] = []
        for f in sorted(base.rglob("part-*.jsonl")):
            if not (f.parent / "_SUCCESS").exists():
                continue  # répertoire incomplet (écriture interrompue) : ignoré
            with open(f, encoding="utf-8") as fh:
                rows.extend(json.loads(line) for line in fh if line.strip())
        return rows

    def write_json(self, zone: str, name: str, doc: Any) -> str:
        _check_zone(zone)
        path = self.root / zone / name
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        tmp.replace(path)
        return str(path)

    def list_tables(self, zone: str) -> list[str]:
        _check_zone(zone)
        d = self.root / zone
        return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.exists() else []


class WebHdfsLake:
    """Client WebHDFS minimal (MKDIRS, CREATE en deux temps, OPEN, LISTSTATUS, DELETE)."""

    def __init__(self, base_url: str, user: str, root: str = "/datalake/fusion", timeout: float = 30.0):
        self.base = base_url.rstrip("/") + "/webhdfs/v1"
        self.user = user
        self.root = "/" + root.strip("/")
        self.timeout = timeout

    def _url(self, path: str, op: str, **params: str) -> str:
        if not self.base.startswith(("http://", "https://")):
            raise ValueError("WEBHDFS_URL doit être une URL http(s)")
        query = urllib.parse.urlencode({"op": op, "user.name": self.user, **params})
        return f"{self.base}{urllib.parse.quote(path)}?{query}"

    def _request(self, method: str, url: str, data: bytes | None = None) -> bytes:
        req = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - schéma vérifié dans _url
        if data is not None:
            req.add_header("Content-Type", "application/octet-stream")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            return resp.read()

    def _put_file(self, path: str, content: bytes) -> None:
        # étape 1 : le NameNode répond 307 vers un DataNode ; urllib suit la redirection en conservant PUT
        req = urllib.request.Request(self._url(path, "CREATE", overwrite="true"), method="PUT")  # noqa: S310
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                location = resp.headers.get("Location")
        except urllib.error.HTTPError as err:
            if err.code != 307:
                raise
            location = err.headers.get("Location")
        if not location:
            raise OSError(f"WebHDFS : pas de redirection DataNode pour {path}")
        self._request("PUT", location, content)  # étape 2 : envoi des octets au DataNode

    def _dir(self, zone: str, table: str, part: str = "") -> str:
        _check_zone(zone)
        return f"{self.root}/{zone}/{table}" + (f"/{part}" if part else "")

    def write_table(self, zone: str, table: str, rows: list[dict[str, Any]], part: str = "") -> str:
        d = self._dir(zone, table, part)
        self._request("DELETE", self._url(d, "DELETE", recursive="true"))
        self._request("PUT", self._url(d, "MKDIRS"))
        text, n = _lines(rows)
        self._put_file(f"{d}/part-00000.jsonl", text.encode("utf-8"))
        self._put_file(f"{d}/_schema.json", _manifest(table, n, list(rows[0]) if rows else []).encode("utf-8"))
        self._put_file(f"{d}/_SUCCESS", b"")
        return d

    def read_table(self, zone: str, table: str) -> list[dict[str, Any]]:
        d = self._dir(zone, table)
        listing = json.loads(self._request("GET", self._url(d, "LISTSTATUS")))
        names = [s["pathSuffix"] for s in listing["FileStatuses"]["FileStatus"]]
        if "_SUCCESS" not in names:
            return []
        rows: list[dict[str, Any]] = []
        for name in sorted(n for n in names if n.startswith("part-")):
            text = self._request("GET", self._url(f"{d}/{name}", "OPEN")).decode("utf-8")
            rows.extend(json.loads(line) for line in text.splitlines() if line.strip())
        return rows

    def write_json(self, zone: str, name: str, doc: Any) -> str:
        _check_zone(zone)
        path = f"{self.root}/{zone}/{name}"
        self._put_file(path, json.dumps(doc, ensure_ascii=False, default=str).encode("utf-8"))
        return path

    def list_tables(self, zone: str) -> list[str]:
        listing = json.loads(self._request("GET", self._url(f"{self.root}/{zone}", "LISTSTATUS")))
        return sorted(s["pathSuffix"] for s in listing["FileStatuses"]["FileStatus"] if s["type"] == "DIRECTORY")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_lake(settings) -> Lake:
    if settings.lake == "webhdfs":
        return WebHdfsLake(settings.webhdfs_url, settings.hdfs_user, settings.hdfs_root)
    return LocalLake(settings.lake_dir)
