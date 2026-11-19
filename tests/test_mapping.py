"""Moteur de mapping : chargement, validation statique, application et export."""

from __future__ import annotations

import copy
import csv

import pytest

from fusion.mapping import MappingError, MappingSpec
from fusion.quality import REGLES, QualityLog


@pytest.fixture(scope="module")
def spec() -> MappingSpec:
    return MappingSpec.load()


def test_toutes_les_colonnes_sources_sont_mappees(spec):
    """Aucune colonne de l'annexe n'est oubliée (hors colonnes propres à une source documentées)."""
    from tests.test_generator import ANNEXE

    used = set()
    for e in spec.entites.values():
        for ch in e.champs:
            used |= {col for col in ch.colonnes.values() if col and not col.startswith("=")}
    assert set().union(*ANNEXE.values()) - used == set()


def test_client_ap_nom_inverse(spec):
    log = QualityLog()
    rec = spec.map_record(
        "client",
        "AP",
        {
            "AP_USER_ID": 7,
            "AP_FULL_NAME": "DUPONT Marie",
            "AP_BIRTH_DATE": "12/04/1985",
            "AP_MAIL_ADDRESS": "MARIE@FREE.FR",
            "AP_PHONE_NUMBER": "0470 12 34 56",
            "AP_STREET_ADDRESS": "3 av Foch",
            "AP_ZIP_CODE": "1000",
            "AP_CREATED_AT": "2024-01-02 10:00:00",
            "AP_CUSTOMER_STATUS": "Active",
            "AP_LOYALTY_SCORE": "42",
        },
        log,
    )
    assert rec["client_uid"] == "AP-7" and rec["source"] == "AP"
    assert (rec["nom"], rec["prenom"]) == ("DUPONT", "Marie")
    assert rec["date_naissance"] == "1985-04-12"
    assert rec["email"] == "marie@free.fr"
    assert rec["pays"] == "BE" and rec["telephone"] == "+32470123456"
    assert rec["adresse"] == "3 AVENUE FOCH"
    assert rec["statut"] == "ACTIF" and rec["score_fidelite"] == 42 and rec["num_fiscal"] is None
    assert log.counts() == {"DQ01": 1, "DQ04": 1, "DQ05": 1}


def test_client_ab_sans_numero_fiscal_et_contact_invalide(spec):
    log = QualityLog()
    rec = spec.map_record(
        "client",
        "AB",
        {
            "AB_CLIENT_ID": 1,
            "AB_NOM": " Durand ",
            "AB_PRENOM": "léa",
            "AB_DATE_NAISSANCE": "1990-02-30",
            "AB_EMAIL": "lea at free.fr",
            "AB_TELEPHONE": "NC",
            "AB_ADRESSE": "1 R Pasteur",
            "AB_CODE_POSTAL": "35000",
            "AB_NUM_FISCAL": None,
            "AB_DATE_CREATION": "2001-01-01 08:00:00.000000",
            "AB_STATUT_CLIENT": "ACTIF",
        },
        log,
    )
    assert rec["nom"] == "DURAND" and rec["prenom"] == "Léa"
    assert rec["region"] == "Bretagne" and rec["date_creation"] == "2001-01-01 08:00:00"
    assert rec["date_naissance"] is None and rec["email"] is None and rec["telephone"] is None
    assert log.counts() == {"DQ02": 1, "DQ03": 1, "DQ11": 1, "DQ12": 1}


def test_contrat_ap_prime_annualisee_et_canal(spec):
    log = QualityLog()
    base = {
        "AP_CONTRACT_REF": "APC1",
        "AP_USER_ID": 7,
        "AP_PRODUCT_CODE": "AUTO_TIERS",
        "AP_START_DATE": "2024-01-01 00:00:00",
        "AP_END_DATE": None,
        "AP_MONTHLY_PREMIUM": 30.5,
        "AP_CONTRACT_STATE": "Active",
        "AP_BROKER_CODE": None,
    }
    rec = spec.map_record("contrat", "AP", base, log)
    assert rec["prime_annuelle"] == 366.0 and rec["branche"] == "AUTO"
    assert (rec["canal"], rec["code_distributeur"]) == ("DIGITAL", "APP-MOBILE")
    assert rec["client_uid"] == "AP-7" and rec["date_debut"] == "2024-01-01"
    courtier = spec.map_record(
        "contrat",
        "AP",
        {**base, "AP_BROKER_CODE": "BRK-004", "AP_MONTHLY_PREMIUM": 0, "AP_CONTRACT_STATE": "CANCELLED"},
        log,
    )
    assert (courtier["canal"], courtier["code_distributeur"]) == ("COURTIER", "BRK-004")
    assert courtier["prime_annuelle"] is None and courtier["statut"] == "RESILIE"
    assert log.counts() == {"DQ05": 1, "DQ07": 1}


def test_contrat_ab(spec):
    log = QualityLog()
    rec = spec.map_record(
        "contrat",
        "AB",
        {
            "AB_POLICY_NUMBER": "AB-POL-1",
            "AB_CLIENT_ID": 1,
            "AB_TYPE_ASSURANCE": "SANTE",
            "AB_DATE_DEBUT": "2020-01-01",
            "AB_DATE_FIN": "2027-01-01",
            "AB_PRIME_ANNUELLE": 912.4,
            "AB_STATUT_CONTRAT": "ACTIF",
            "AB_AGENCE_ID": 7,
        },
        log,
    )
    assert (rec["canal"], rec["code_distributeur"], rec["prime_annuelle"]) == ("AGENCE", "AG-007", 912.4)
    assert rec["code_produit"] == "SANTE" and not log.anomalies


def test_sinistre_ap_formats_de_date(spec):
    log = QualityLog()
    row = {
        "AP_SINISTRE_NUM": 1,
        "AP_CONTRACT_REF": "APC1",
        "AP_INCIDENT_DATE": "02/03/2025 14:05",
        "AP_ESTIMATED_AMOUNT": 1200,
        "AP_CLAIM_STATUS": "under_review",
        "AP_CLAIM_COMMENT": "x",
        "AP_FRAUD_SCORE": "12.5",
    }
    rec = spec.map_record("sinistre", "AP", row, log)
    assert rec["date_survenance"] == "2025-03-02 14:05:00" and rec["statut"] == "EN_INSTRUCTION"
    assert rec["score_fraude"] == 12.5
    bad = spec.map_record(
        "sinistre", "AP", {**row, "AP_INCIDENT_DATE": "N/A", "AP_ESTIMATED_AMOUNT": -5, "AP_CLAIM_STATUS": "LOST"}, log
    )
    assert bad["date_survenance"] is None and bad["montant_estime"] is None and bad["statut"] == "INCONNU"
    assert log.counts() == {"DQ05": 1, "DQ08": 1, "DQ09": 1, "DQ17": 1}


def test_paiements(spec):
    log = QualityLog()
    ab = {
        "AB_PAYMENT_ID": 1,
        "AB_POLICY_NUMBER": "AB-POL-1",
        "AB_DATE_PAIEMENT": "2025-01-05",
        "AB_MONTANT": 76.03,
        "AB_MODE_PAIEMENT": "CB",
    }
    r1 = spec.map_record("paiement", "AB", ab, log)
    spec.map_record("paiement", "AB", {**ab, "AB_PAYMENT_ID": 2, "AB_MODE_PAIEMENT": "BITCOIN"}, log)
    assert (r1["paiement_uid"], r1["mode"], r1["statut"]) == ("AB-1", "CARTE", "REUSSI")
    assert r1["date_paiement"] == "2025-01-05 00:00:00"
    r2 = spec.map_record(
        "paiement",
        "AP",
        {
            "AP_PAYMENT_REF": "6F1C",
            "AP_CONTRACT_REF": "APC1",
            "AP_PAYMENT_DATETIME": "2025-01-05 13:00:00",
            "AP_AMOUNT_PAID": 30.5,
            "AP_PAYMENT_CHANNEL": "APPLE_PAY",
            "AP_TRANSACTION_STATUS": "WEIRD",
        },
        log,
    )
    assert (r2["mode"], r2["statut"]) == ("PORTEFEUILLE", "INCONNU")
    assert log.counts() == {"DQ05": 2, "DQ13": 1}  # DQ13 structurelle : une seule fois


def test_validation_transformation_inconnue(spec):
    raw = copy.deepcopy(spec.raw)
    raw["client"]["champ"][2]["t_ab"] = ["inexistante"]
    with pytest.raises(MappingError, match="transformation inconnue"):
        MappingSpec(raw)


def test_validation_colonne_inconnue(spec):
    raw = copy.deepcopy(spec.raw)
    raw["contrat"]["champ"][0]["ap"] = "AP_POLICY"
    with pytest.raises(MappingError, match="absente de AP_CONTRACTS"):
        MappingSpec(raw)


def test_validation_cle_et_doublon(spec):
    raw = copy.deepcopy(spec.raw)
    raw["sinistre"]["cle"] = "id"
    with pytest.raises(MappingError, match="clé"):
        MappingSpec(raw)
    raw = copy.deepcopy(spec.raw)
    raw["paiement"]["champ"].append(raw["paiement"]["champ"][0])
    with pytest.raises(MappingError, match="double"):
        MappingSpec(raw)
    raw = copy.deepcopy(spec.raw)
    del raw["paiement"]
    with pytest.raises(MappingError, match="absente"):
        MappingSpec(raw)


def test_export_csv(spec, tmp_path):
    n = spec.export_csv(tmp_path / "m.csv")
    with open(tmp_path / "m.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    assert n == len(rows) == sum(len(e.champs) for e in spec.entites.values())
    assert rows[0]["entite"] == "client" and "cle_source" in rows[0]["transformation_ab"]
    assert spec.columns("client")[0] == "source"


def test_catalogue_qualite():
    log = QualityLog()
    with pytest.raises(KeyError):
        log.flag("DQ99", "AB", "client", "AB-1")
    log.flag("DQ03", "AB", "client", "AB-1", "x" * 500)
    other = QualityLog()
    other.flag("DQ03", "AP", "client", "AP-1")
    log.extend(other)
    assert len(log.anomalies[0].valeur) == 120
    assert log.counts_by("source") == {("AB",): 1, ("AP",): 1}
    assert all(r.gravite in {"critique", "majeure", "mineure", "info"} for r in REGLES.values())
