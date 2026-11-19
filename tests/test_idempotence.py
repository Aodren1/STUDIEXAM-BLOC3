"""Anomalie FUS-9 : paiements (et toutes les entités) comptés plusieurs fois après une republication.

Le bus garantit une livraison AU MOINS une fois ; trois situations normales republient des lignes déjà
chargées : rejeu après un mode complet, borne inclusive du filigrane d'AP_PAYMENTS, relivraison Kafka
après un arrêt du job entre l'écriture silver et le commit des offsets. Le chargement silver doit donc
être idempotent : la même ligne source, reçue n fois, donne une seule ligne silver (la dernière version).
"""

from __future__ import annotations

import json
import shutil

import pytest

from fusion.bus.filelog import FileBus
from fusion.config import Settings
from fusion.extract import ExtractState, envelope, extract_all
from fusion.lake import LocalLake
from fusion.mapping import MappingSpec
from fusion.pipeline import run
from fusion.processing.silver import run_silver
from tests.conftest import REF_DATE


@pytest.fixture()
def data_dir(small_sources, tmp_path):
    shutil.copytree(small_sources, tmp_path / "sources")
    return tmp_path


def _gold(settings):
    return json.loads(settings.gold_path.read_text(encoding="utf-8"))


def test_rejeu_incremental_sans_nouvelle_donnee(data_dir):
    """Reproduction de l'incident : la 2e exécution doublait toutes les tables silver."""
    settings = Settings(data_dir=data_dir, ref_date=REF_DATE)
    run(settings, incremental=False)
    avant = _gold(settings)
    run(settings, incremental=True)  # levait ReconciliationError (écart = +100 % sur chaque table)
    apres = _gold(settings)
    assert apres["finances"]["kpi"] == avant["finances"]["kpi"]
    assert apres["contrats"]["kpi"]["actifs_total"] == avant["contrats"]["kpi"]["actifs_total"]
    assert all(r["ecart"] == 0 for r in apres["qualite"]["reconciliation"])


def test_mode_complet_enregistre_les_filigranes(data_dir):
    settings = Settings(data_dir=data_dir, ref_date=REF_DATE)
    run(settings, incremental=False)
    marks = json.loads((data_dir / "extract_state.json").read_text(encoding="utf-8"))
    assert set(marks) == {"AB_CLIENT", "AB_CONTRAT", "AB_SINISTRE", "AB_PAIEMENT", "AP_USERS", "AP_CONTRACTS",
                          "AP_CLAIMS", "AP_PAYMENTS"}


def test_relivraison_apres_arret_avant_commit(small_sources, tmp_path):
    spec = MappingSpec.load()
    bus = FileBus(tmp_path / "bus")
    extract_all(small_sources, spec, bus)
    bus.close()
    lake = LocalLake(tmp_path / "lake")
    first = run_silver(FileBus(tmp_path / "bus"), spec, lake, "r1", commit=False)  # arrêt avant commit
    n = {e: first.store.count(e) for e in first.store.tables}
    again = run_silver(FileBus(tmp_path / "bus"), spec, lake, "r2")  # tout est relivré
    assert {e: again.store.count(e) for e in again.store.tables} == n


def test_borne_inclusive_du_filigrane_ap_payments(small_sources, tmp_path):
    spec = MappingSpec.load()
    lake = LocalLake(tmp_path / "lake")
    state_file = tmp_path / "state.json"
    bus = FileBus(tmp_path / "bus")
    extract_all(small_sources, spec, bus, ExtractState.load(state_file))
    first = run_silver(bus, spec, lake, "r1")
    n = first.store.count("paiement")
    republies = {s.table: s.lignes for s in extract_all(small_sources, spec, bus, ExtractState.load(state_file))}
    assert republies["AP_PAYMENTS"] >= 1  # la borne (>=) republie les paiements de la dernière seconde lue
    second = run_silver(bus, spec, lake, "r2")
    assert second.store.count("paiement") == n
    bus.close()


def test_la_derniere_version_d_une_ligne_l_emporte(tmp_path):
    spec = MappingSpec.load()
    bus = FileBus(tmp_path / "bus")
    row = {"AB_CLIENT_ID": 1, "AB_NOM": "MARTIN", "AB_PRENOM": "Léa", "AB_DATE_NAISSANCE": "1990-01-01",
           "AB_EMAIL": "ancienne@free.fr", "AB_TELEPHONE": "0102030405", "AB_ADRESSE": "1 rue X",
           "AB_CODE_POSTAL": "75011", "AB_NUM_FISCAL": "1", "AB_DATE_CREATION": "2020-01-01 00:00:00",
           "AB_STATUT_CLIENT": "ACTIF"}
    bus.produce("src.ab.client", "AB-1", envelope("AB", "AB_CLIENT", "AB-1", row))
    bus.produce("src.ab.client", "AB-1", envelope("AB", "AB_CLIENT", "AB-1", {**row, "AB_EMAIL": "nouvelle@free.fr"}))
    res = run_silver(bus, spec, LocalLake(tmp_path / "lake"), "r1")
    assert res.store.count("client") == 1
    assert res.store.tables["client"][0]["email"] == "nouvelle@free.fr"
