"""Traitements silver, dédoublonnage, gold et entrepôt."""

from __future__ import annotations

import sqlite3
from datetime import date
from types import SimpleNamespace

import pytest

from fusion.bus import TOPIC_DLQ
from fusion.bus.filelog import FileBus
from fusion.extract import envelope
from fusion.lake import LocalLake
from fusion.mapping import MappingSpec
from fusion.processing import dedup as d
from fusion.processing.gold import _months, _tranche, build_gold
from fusion.processing.silver import SilverStore, check_integrity, run_silver, valid_envelope
from fusion.quality import QualityLog
from fusion.warehouse import build_warehouse

REF = date(2026, 10, 31)


def _client(uid, **kw):
    base = {
        "client_uid": uid,
        "source": uid[:2],
        "source_id": uid[3:],
        "nom": "DUPONT",
        "prenom": "Marie",
        "date_naissance": "1985-04-12",
        "email": None,
        "telephone": None,
        "adresse": "1 RUE PASTEUR",
        "code_postal": "35000",
        "pays": "FR",
        "region": "Bretagne",
        "date_creation": "2020-01-01 00:00:00",
        "statut": "ACTIF",
        "num_fiscal": None,
        "score_fidelite": None,
    }
    return {**base, **kw}


# ---------------------------------------------------------------------------- silver
def test_enveloppe_valide():
    assert valid_envelope(envelope("AB", "AB_CLIENT", "AB-1", {}))
    assert not valid_envelope("texte brut")
    assert not valid_envelope({"schema": "autre", "after": {}, "source": {}})


def test_silver_quarantaine_des_messages_illisibles(tmp_path):
    bus = FileBus(tmp_path / "bus", partitions=1)
    bus.produce_raw("src.ab.client", "AB-1", "{json tronqué")
    bus.produce(
        "src.ab.client",
        "AB-2",
        envelope(
            "AB",
            "AB_CLIENT",
            "AB-2",
            {
                "AB_CLIENT_ID": 2,
                "AB_NOM": "MARTIN",
                "AB_PRENOM": "Léa",
                "AB_DATE_NAISSANCE": "1990-01-01",
                "AB_EMAIL": "lea@free.fr",
                "AB_TELEPHONE": "0102030405",
                "AB_ADRESSE": "1 rue X",
                "AB_CODE_POSTAL": "75011",
                "AB_NUM_FISCAL": "1",
                "AB_DATE_CREATION": "2020-01-01 00:00:00",
                "AB_STATUT_CLIENT": "ACTIF",
            },
        ),
    )
    lake = LocalLake(tmp_path / "lake")
    res = run_silver(bus, MappingSpec.load(), lake, "r1")
    assert res.quarantaine == 1 and res.store.count("client") == 1
    assert res.log.counts()["DQ16"] == 1
    assert len(list(bus.consume(TOPIC_DLQ, "audit"))) == 1
    assert lake.read_table("silver", "client")[0]["client_uid"] == "AB-2"
    assert list(bus.consume("src.ab.client", "spark-silver")) == []  # offsets validés


def test_orphelins_en_cascade_et_paiement_tardif():
    store = SilverStore()
    store.add("client", _client("AB-1"))
    store.add("contrat", {"contrat_uid": "AB-C1", "client_uid": "AB-1", "source": "AB", "date_fin": "2025-01-31"})
    store.add("contrat", {"contrat_uid": "AB-C2", "client_uid": "AB-999", "source": "AB", "date_fin": None})
    store.add("sinistre", {"sinistre_uid": "AB-S1", "contrat_uid": "AB-C2", "source": "AB"})
    store.add(
        "paiement",
        {"paiement_uid": "AB-P1", "contrat_uid": "AB-C1", "source": "AB", "date_paiement": "2025-03-20 00:00:00"},
    )
    store.add(
        "paiement",
        {"paiement_uid": "AB-P2", "contrat_uid": "AB-C1", "source": "AB", "date_paiement": "2025-02-15 00:00:00"},
    )
    log = QualityLog()
    check_integrity(store, log)
    assert log.counts() == {"DQ06": 1, "DQ10": 1, "DQ15": 1}
    assert store.tables["sinistre"][0]["est_orphelin"]  # parent orphelin : isolé à son tour
    assert [p["est_tardif"] for p in store.tables["paiement"]] == [True, False]
    check_integrity(store, log)  # un second contrôle ne double pas les anomalies
    assert log.counts() == {"DQ06": 1, "DQ10": 1, "DQ15": 1}


# ---------------------------------------------------------------------------- doublons
@pytest.mark.parametrize(
    ("a", "b", "low", "high"),
    [
        ("MARTHA", "MARHTA", 0.96, 0.97),
        ("DUPONT", "DUPONT", 1.0, 1.0),
        ("ABC", "XYZ", 0.0, 0.0),
        ("", "ABC", 0.0, 0.0),
    ],
)
def test_jaro_winkler(a, b, low, high):
    assert low <= d.jaro_winkler(a, b) <= high


def test_poids_normalises():
    assert round(sum(d.POIDS.values()), 6) == 1.0


def test_score_et_niveaux():
    x = _client("AB-1", email="m@x.fr", telephone="+33102030405", nom_cle="DUPONT MARIE")
    y = _client("AP-1", email="m@x.fr", telephone="+33102030405", nom_cle="DUPONT MARIE")
    assert d.score(d.compare(x, y)) == 1.0
    z = _client("AP-2", nom_cle="DUPNOT MARIE", adresse="9 AVENUE FOCH")
    crit = d.compare(x, z)
    assert crit["nom"] == 0.7 and crit["adresse"] == 0.3 and crit["email"] == 0.0
    assert d.token_similarity("", "A") == 0.0


def test_dedoublonnage_bout_en_bout():
    clients = [
        _client("AB-1", email="marie@x.fr", telephone="+33102030405"),
        _client(
            "AP-7",
            nom="DUPONT",
            prenom="Marie",
            email="marie@x.fr",
            telephone="+33102030405",
            date_creation="2024-01-01 00:00:00",
        ),
        _client("AP-8", nom="DUPONT", prenom="Marie", date_naissance="1985-04-12", email=None),  # probable
        _client("AB-2", nom="MARTIN", prenom="Paul", date_naissance="1970-01-01", code_postal="13001"),
    ]
    res = d.run_dedup(clients, truth=[("AB-1", "AP-7")])
    niveaux = {(p["uid_a"], p["uid_b"]): p["niveau"] for p in res.paires}
    assert niveaux[("AB-1", "AP-7")] == "CERTAIN"
    assert niveaux[("AB-1", "AP-8")] == "PROBABLE"
    assert res.personne_de["AP-7"] == res.personne_de["AB-1"] == "P-AB-1"  # AB référentiel maître
    assert res.personne_de["AP-8"] == "P-AP-8"  # une paire probable n'est jamais fusionnée automatiquement
    golden = {p["personne_id"]: p for p in res.personnes}["P-AB-1"]
    assert golden["sources"] == "AB+AP" and golden["nb_comptes"] == 2
    assert res.stats["evaluation"]["certain"]["precision"] == 1.0
    assert res.stats["evaluation"]["certain"]["rappel"] == 1.0


def test_blocs_trop_grands_ignores(monkeypatch):
    monkeypatch.setattr(d, "MAX_BLOC", 2)
    clients = [_client(f"AB-{i}", email="commun@x.fr") for i in range(3)]
    res = d.run_dedup(clients)
    assert res.stats["blocs_ignores_trop_grands"] >= 1 and res.stats["evaluation"] is None


def test_union_find_compression():
    uf = d.UnionFind()
    uf.union("AP-3", "AP-2")
    uf.union("AP-2", "AB-1")
    assert uf.find("AP-3") == "AB-1"


# ---------------------------------------------------------------------------- gold
def test_utilitaires_gold():
    assert _months(date(2026, 2, 15), 3) == ["2025-12", "2026-01", "2026-02"]
    assert _tranche(0, [0, 10, float("inf")]) == 0 and _tranche(10, [0, 10, float("inf")]) == 1
    assert _tranche(-5, [0, 10, 20]) == 1


@pytest.fixture(scope="module")
def jeu_complet(small_sources, tmp_path_factory):
    from fusion.config import Settings
    from fusion.pipeline import run

    data = tmp_path_factory.mktemp("data")
    (data / "sources").mkdir()
    for f in small_sources.iterdir():
        (data / "sources" / f.name).write_bytes(f.read_bytes())
    settings = Settings(data_dir=data, ref_date=REF)
    report = run(settings, incremental=False)
    return settings, report


def test_pipeline_rapport_et_reconciliation(jeu_complet):
    settings, report = jeu_complet
    assert report["mode"] == "complet"
    assert [e["etape"] for e in report["etapes"]][:3] == ["sources", "extraction (Talend → Kafka)", "silver (Spark)"]
    assert settings.run_report_path.exists() and settings.gold_path.exists()
    import json

    gold = json.loads(settings.gold_path.read_text(encoding="utf-8"))
    assert all(r["ecart"] == 0 for r in gold["qualite"]["reconciliation"])
    assert set(gold) >= {"synthese", "clients", "contrats", "sinistres", "finances", "doublons", "qualite"}


def test_gold_coherence_des_indicateurs(jeu_complet):
    import json

    settings, _ = jeu_complet
    g = json.loads(settings.gold_path.read_text(encoding="utf-8"))
    c = g["clients"]
    assert c["kpi"]["comptes_total"] == sum(c["kpi"]["comptes"].values())
    assert sum(c["portefeuille"]["valeurs"]) == c["kpi"]["personnes"]
    assert sum(sum(v) for v in c["par_statut"]["series"].values()) == c["kpi"]["comptes_total"]
    ct = g["contrats"]
    assert sum(sum(v) for v in ct["par_branche"]["series"].values()) == ct["kpi"]["actifs_total"]
    d_ = g["doublons"]
    assert d_["kpi"]["comptes"] - d_["kpi"]["personnes"] == d_["kpi"]["comptes_fusionnes"]
    m = d_["matrice_sources"]["CERTAIN"]
    assert m["AB"]["AP"] == m["AP"]["AB"]  # matrice symétrique
    cooc = d_["matrice_criteres"]["valeurs"]
    assert all(cooc[i][j] == cooc[j][i] for i in range(5) for j in range(5))
    assert g["finances"]["kpi"]["encaisse_12_mois"] > 0
    assert g["synthese"]["personnes"] == c["kpi"]["personnes"]


def test_entrepot_respecte_le_mcd(jeu_complet):
    settings, _ = jeu_complet
    con = sqlite3.connect(settings.warehouse_path)
    try:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        n_cs = con.execute("SELECT COUNT(*) FROM client_source").fetchone()[0]
        n_p = con.execute("SELECT COUNT(*) FROM personne").fetchone()[0]
        assert n_p <= n_cs
        # chaque contrat de l'entrepôt a un client ; les orphelins sont en quarantaine avec leur motif
        assert (
            con.execute(
                "SELECT COUNT(*) FROM contrat c LEFT JOIN client_source s USING (client_uid) WHERE s.client_uid IS NULL"
            ).fetchone()[0]
            == 0
        )
        motifs = {r[0] for r in con.execute("SELECT DISTINCT motif FROM quarantaine")}
        assert motifs <= {"DQ06", "DQ10", "DQ14"}
        assert con.execute("SELECT COUNT(*) FROM paire_doublon WHERE uid_a >= uid_b").fetchone()[0] == 0
    finally:
        con.close()


def test_entrepot_refuse_une_violation_de_cardinalite(tmp_path):
    tables = {
        "client": [],
        "contrat": [
            {
                "contrat_uid": "AB-C1",
                "source": "AB",
                "source_id": "C1",
                "client_uid": "AB-404",
                "code_produit": "AUTO",
                "code_distributeur": None,
                "date_debut": None,
                "date_fin": None,
                "prime_annuelle": 100.0,
                "statut": "ACTIF",
                "canal": "AGENCE",
                "est_orphelin": False,
            }
        ],
        "sinistre": [],
        "paiement": [],
    }
    dedup = SimpleNamespace(personnes=[], personne_de={}, paires=[])
    with pytest.raises(sqlite3.IntegrityError):
        build_warehouse(tmp_path / "w.sqlite", tables, dedup, QualityLog())
    assert not (tmp_path / "w.sqlite").exists()  # bascule atomique : pas d'entrepôt partiel


def test_gold_sur_jeu_vide():
    tables = {"client": [], "contrat": [], "sinistre": [], "paiement": []}
    dedup = d.run_dedup([])
    g = build_gold(tables, dedup, QualityLog(), REF)
    assert g["clients"]["kpi"]["age_median"] is None and g["finances"]["kpi"]["ratio_sp_12_mois"] is None


def test_extraction_seule_et_cli(small_sources, tmp_path, monkeypatch, capsys):
    from fusion.__main__ import main
    from fusion.config import Settings
    from fusion.pipeline import extract_only

    (tmp_path / "sources").mkdir()
    for f in small_sources.iterdir():
        (tmp_path / "sources" / f.name).write_bytes(f.read_bytes())
    stats = extract_only(Settings(data_dir=tmp_path))
    assert len(stats) == 8 and all(s["lignes"] > 0 for s in stats)
    monkeypatch.setenv("FUSION_DATA_DIR", str(tmp_path))
    assert main(["mapping", str(tmp_path / "m.csv")]) == 0
    assert "attributs exportés" in capsys.readouterr().out
    assert main(["seed"]) == 0  # sources déjà présentes : rien n'est régénéré
    assert capsys.readouterr().out.strip() == "null"
