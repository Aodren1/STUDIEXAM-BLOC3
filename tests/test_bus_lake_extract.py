"""Bus (sémantique Kafka), lac (local et WebHDFS) et extraction incrémentale."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

import pytest

from fusion.bus import TOPICS_SOURCES, lag, open_bus, topic_for
from fusion.bus.filelog import FileBus
from fusion.extract import ExtractState, envelope, extract_all
from fusion.lake import LocalLake, WebHdfsLake, open_lake
from fusion.mapping import MappingSpec
from fusion.sources.generator import AP_DB


# ---------------------------------------------------------------------------- bus
def test_partitionnement_stable_par_cle(tmp_path):
    bus = FileBus(tmp_path, partitions=3)
    autre = FileBus(tmp_path, partitions=3)  # même clé, même partition, d'une instance à l'autre
    assert bus.partition_for("AB-1") == autre.partition_for("AB-1")
    assert {bus.partition_for(f"AB-{i}") for i in range(50)} == {0, 1, 2}


def test_ordre_conserve_pour_une_meme_cle(tmp_path):
    bus = FileBus(tmp_path)
    for v in range(5):
        bus.produce("t", "AB-1", {"v": v})
    assert [m.value["v"] for m in bus.consume("t", "g")] == [0, 1, 2, 3, 4]


def test_livraison_au_moins_une_fois(tmp_path):
    """Sans commit, un consommateur redémarré relit les mêmes messages (comportement Kafka)."""
    bus = FileBus(tmp_path)
    for i in range(10):
        bus.produce("t", f"k{i}", {"i": i})
    first = list(bus.consume("t", "g", max_messages=4))
    assert len(first) == 4
    assert len(list(bus.consume("t", "g"))) == 10  # pas de commit : tout est relivré
    list(bus.consume("t", "g"))
    bus.commit("t", "g")
    assert list(bus.consume("t", "g")) == []
    assert lag(bus, "t", "g") == 0
    bus.produce("t", "k99", {"i": 99})
    assert lag(bus, "t", "g") == 1
    bus.reset("g")
    assert lag(bus, "t", "g") == 11
    bus.commit("inconnu", "g")  # sans consommation préalable : sans effet
    bus.close()


def test_message_corrompu_remonte_en_brut(tmp_path):
    bus = FileBus(tmp_path, partitions=1)
    bus.produce_raw("t", None, "{pas du json")
    msg = next(bus.consume("t", "g"))
    assert msg.value == "{pas du json" and msg.key is None


def test_open_bus_et_topics(tmp_path):
    settings = SimpleNamespace(bus="file", data_dir=tmp_path)
    assert isinstance(open_bus(settings), FileBus)
    assert topic_for("AB", "client") == "src.ab.client" and len(TOPICS_SOURCES) == 8


# ---------------------------------------------------------------------------- lac local
def test_lac_local_ecriture_lecture(tmp_path):
    lake = LocalLake(tmp_path)
    lake.write_table("silver", "client", [{"a": 1}, {"a": 2}])
    lake.write_table("silver", "client", [{"a": 3}])  # overwrite
    assert lake.read_table("silver", "client") == [{"a": 3}]
    manifest = json.loads((tmp_path / "silver/client/_schema.json").read_text(encoding="utf-8"))
    assert manifest["lignes"] == 1 and manifest["colonnes"] == ["a"]
    lake.write_table("bronze", "src.ab.client", [{"x": 1}], part="ingestion=2026-10-31")
    assert lake.read_table("bronze", "src.ab.client") == [{"x": 1}]
    lake.write_json("gold", "doc.json", {"ok": True})
    assert lake.list_tables("silver") == ["client"] and lake.list_tables("gold") == []
    with pytest.raises(ValueError):
        lake.write_table("platine", "x", [])


def test_lac_local_ignore_un_repertoire_incomplet(tmp_path):
    lake = LocalLake(tmp_path)
    lake.write_table("silver", "t", [{"a": 1}])
    (tmp_path / "silver/t/_SUCCESS").unlink()
    assert lake.read_table("silver", "t") == []
    lake.write_table("silver", "vide", [])
    assert lake.read_table("silver", "vide") == []


# ---------------------------------------------------------------------------- lac WebHDFS (faux NameNode)
class _FakeHdfs(BaseHTTPRequestHandler):
    files: ClassVar[dict[str, bytes]] = {}

    def log_message(self, *args):
        pass

    def _reply(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        path = url.path.removeprefix("/webhdfs/v1")
        if q["op"][0] == "MKDIRS":
            return self._reply(200, b'{"boolean":true}')
        if "datanode" not in q:
            loc = f"http://127.0.0.1:{self.server.server_port}{url.path}?op=CREATE&datanode=1"
            return self._reply(307, headers={"Location": loc})
        length = int(self.headers.get("Content-Length", 0))
        self.files[path] = self.rfile.read(length)
        return self._reply(201)

    def do_DELETE(self):
        prefix = urlparse(self.path).path.removeprefix("/webhdfs/v1")
        for k in [k for k in self.files if k.startswith(prefix)]:
            del self.files[k]
        self._reply(200, b'{"boolean":true}')

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        path = url.path.removeprefix("/webhdfs/v1")
        if q["op"][0] == "OPEN":
            return self._reply(200, self.files[path])
        children = {}
        for k in self.files:
            if k.startswith(path + "/"):
                rest = k[len(path) + 1 :].split("/")
                children[rest[0]] = "DIRECTORY" if len(rest) > 1 else "FILE"
        statuses = [{"pathSuffix": n, "type": t} for n, t in children.items()]
        return self._reply(200, json.dumps({"FileStatuses": {"FileStatus": statuses}}).encode())


@pytest.fixture()
def fake_hdfs():
    _FakeHdfs.files = {}
    server = HTTPServer(("127.0.0.1", 0), _FakeHdfs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_lac_webhdfs_aller_retour(fake_hdfs):
    lake = WebHdfsLake(fake_hdfs, "fusion", "/datalake/fusion")
    lake.write_table("silver", "client", [{"client_uid": "AB-1"}, {"client_uid": "AP-1"}])
    assert "/datalake/fusion/silver/client/_SUCCESS" in _FakeHdfs.files
    assert lake.read_table("silver", "client") == [{"client_uid": "AB-1"}, {"client_uid": "AP-1"}]
    assert lake.list_tables("silver") == ["client"]
    lake.write_json("gold", "dashboard.json", {"ok": 1})
    assert json.loads(_FakeHdfs.files["/datalake/fusion/gold/dashboard.json"]) == {"ok": 1}
    del _FakeHdfs.files["/datalake/fusion/silver/client/_SUCCESS"]
    assert lake.read_table("silver", "client") == []


def test_webhdfs_refuse_un_schema_non_http():
    with pytest.raises(ValueError):
        WebHdfsLake("file:///etc", "x").read_table("silver", "client")


def test_open_lake(tmp_path):
    assert isinstance(open_lake(SimpleNamespace(lake="local", lake_dir=tmp_path)), LocalLake)
    hdfs = open_lake(SimpleNamespace(lake="webhdfs", webhdfs_url="http://nn:9870", hdfs_user="u", hdfs_root="/r"))
    assert isinstance(hdfs, WebHdfsLake)


# ---------------------------------------------------------------------------- extraction
def test_extraction_complete_puis_incrementale(small_sources, tmp_path):
    spec = MappingSpec.load()
    bus = FileBus(tmp_path / "bus")
    state = ExtractState.load(tmp_path / "state.json")
    stats = {s.table: s for s in extract_all(small_sources, spec, bus, state)}
    with closing(sqlite3.connect(small_sources / AP_DB)) as con:
        n_users = con.execute("SELECT COUNT(*) FROM AP_USERS").fetchone()[0]
        total_paid = con.execute("SELECT ROUND(SUM(AP_AMOUNT_PAID), 2) FROM AP_PAYMENTS").fetchone()[0]
    assert stats["AP_USERS"].lignes == n_users
    assert stats["AP_PAYMENTS"].somme_controle == total_paid
    msg = next(bus.consume("src.ap.client", "test"))
    assert msg.value["schema"] == "fusion.src.v1" and msg.value["source"]["table"] == "AP_USERS"
    assert msg.key == msg.value["key"] and msg.key.startswith("AP-")

    # second passage sans nouvelle donnée : rien n'est republié, sauf la borne inclusive d'AP_PAYMENTS
    state2 = ExtractState.load(tmp_path / "state.json")
    again = {s.table: s.lignes for s in extract_all(small_sources, spec, bus, state2)}
    assert again["AP_USERS"] == 0 and again["AB_PAIEMENT"] == 0
    assert again["AP_PAYMENTS"] >= 1


def test_enveloppe():
    env = envelope("AB", "AB_CLIENT", "AB-1", {"AB_CLIENT_ID": 1})
    assert env["source"]["db"] == "ORACLE_ABASSURANCE" and env["op"] == "r" and env["after"]["AB_CLIENT_ID"] == 1


def test_noms_de_topic_et_de_groupe_refuses(tmp_path):
    bus = FileBus(tmp_path)
    for nom in ("../evil", "a/b", "..", "", "x" * 200):
        with pytest.raises(ValueError):
            bus.committed("src.ab.client", nom)
        with pytest.raises(ValueError):
            bus.produce(nom, "k", {"v": 1})


def test_offsets_alteres_assainis_a_la_relecture(tmp_path):
    bus = FileBus(tmp_path)
    f = tmp_path / "_offsets" / "g.json"
    f.parent.mkdir(parents=True)
    brut = {"src.ab.client": {"0": "3", "1": 2}, "../../hors": {"0": 1}, "src.ap.users": "x"}
    f.write_text(json.dumps(brut), encoding="utf-8")
    assert bus.committed("src.ab.client", "g") == {0: 3, 1: 2}
    assert bus.committed("../../hors", "g") == {}
    assert bus.committed("src.ap.users", "g") == {}
