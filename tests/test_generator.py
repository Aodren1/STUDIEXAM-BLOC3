"""Le générateur respecte les schémas de l'annexe et injecte les défauts attendus."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date

from fusion.sources.generator import AB_DB, AP_DB, TRUTH_FILE, _poisson, _schedule, generate

ANNEXE = {
    "AB_CLIENT": ["AB_CLIENT_ID", "AB_NOM", "AB_PRENOM", "AB_DATE_NAISSANCE", "AB_EMAIL", "AB_TELEPHONE",
                  "AB_ADRESSE", "AB_CODE_POSTAL", "AB_NUM_FISCAL", "AB_DATE_CREATION", "AB_STATUT_CLIENT"],
    "AB_CONTRAT": ["AB_POLICY_NUMBER", "AB_CLIENT_ID", "AB_TYPE_ASSURANCE", "AB_DATE_DEBUT", "AB_DATE_FIN",
                   "AB_PRIME_ANNUELLE", "AB_STATUT_CONTRAT", "AB_AGENCE_ID"],
    "AB_SINISTRE": ["AB_CLAIM_ID", "AB_POLICY_NUMBER", "AB_DATE_SINISTRE", "AB_MONTANT_ESTIME",
                    "AB_STATUT_SINISTRE", "AB_DESCRIPTION"],
    "AB_PAIEMENT": ["AB_PAYMENT_ID", "AB_POLICY_NUMBER", "AB_DATE_PAIEMENT", "AB_MONTANT", "AB_MODE_PAIEMENT"],
    "AP_USERS": ["AP_USER_ID", "AP_FULL_NAME", "AP_BIRTH_DATE", "AP_MAIL_ADDRESS", "AP_PHONE_NUMBER",
                 "AP_STREET_ADDRESS", "AP_ZIP_CODE", "AP_CREATED_AT", "AP_CUSTOMER_STATUS", "AP_LOYALTY_SCORE"],
    "AP_CONTRACTS": ["AP_CONTRACT_REF", "AP_USER_ID", "AP_PRODUCT_CODE", "AP_START_DATE", "AP_END_DATE",
                     "AP_MONTHLY_PREMIUM", "AP_CONTRACT_STATE", "AP_BROKER_CODE"],
    "AP_CLAIMS": ["AP_SINISTRE_NUM", "AP_CONTRACT_REF", "AP_INCIDENT_DATE", "AP_ESTIMATED_AMOUNT",
                  "AP_CLAIM_STATUS", "AP_CLAIM_COMMENT", "AP_FRAUD_SCORE"],
    "AP_PAYMENTS": ["AP_PAYMENT_REF", "AP_CONTRACT_REF", "AP_PAYMENT_DATETIME", "AP_AMOUNT_PAID",
                    "AP_PAYMENT_CHANNEL", "AP_TRANSACTION_STATUS"],
}


def _columns(db, table):
    with sqlite3.connect(db) as con:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def test_schemas_identiques_a_l_annexe(small_sources):
    for table, cols in ANNEXE.items():
        db = small_sources / (AB_DB if table.startswith("AB_") else AP_DB)
        assert _columns(db, table) == cols, table


def test_volumes_au_ratio_12_pour_5(small_sources):
    with sqlite3.connect(small_sources / AB_DB) as ab, sqlite3.connect(small_sources / AP_DB) as ap:
        n_ab = ab.execute("SELECT COUNT(*) FROM AB_CLIENT").fetchone()[0]
        n_ap = ap.execute("SELECT COUNT(*) FROM AP_USERS").fetchone()[0]
    assert (n_ab, n_ap) == (300, 125)


def test_generation_deterministe(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(a, scale=0.02)
    generate(b, scale=0.02)
    for name in (AB_DB, AP_DB, TRUTH_FILE):
        assert hashlib.sha256((a / name).read_bytes()).digest() == hashlib.sha256((b / name).read_bytes()).digest()


def test_defauts_injectes(small_sources):
    with sqlite3.connect(small_sources / AP_DB) as ap:
        births = [r[0] for r in ap.execute("SELECT AP_BIRTH_DATE FROM AP_USERS")]
        incidents = [r[0] for r in ap.execute("SELECT AP_INCIDENT_DATE FROM AP_CLAIMS")]
        states = {r[0] for r in ap.execute("SELECT AP_CONTRACT_STATE FROM AP_CONTRACTS")}
    assert any("/" in b for b in births), "dates JJ/MM/AAAA attendues"
    assert any(i and ("T" in i or "/" in i) for i in incidents), "horodatages non ISO attendus"
    assert len({s.lower() for s in states}) < len(states), "casse hétérogène attendue"


def test_verite_terrain_reference_des_clients_existants(small_sources):
    pairs = json.loads((small_sources / TRUTH_FILE).read_text(encoding="utf-8"))["paires"]
    assert pairs
    with sqlite3.connect(small_sources / AB_DB) as ab, sqlite3.connect(small_sources / AP_DB) as ap:
        ids = {f"AB-{r[0]}" for r in ab.execute("SELECT AB_CLIENT_ID FROM AB_CLIENT")}
        ids |= {f"AP-{r[0]}" for r in ap.execute("SELECT AP_USER_ID FROM AP_USERS")}
    assert all(a in ids and b in ids for a, b in pairs)


def test_clients_majeurs(small_sources):
    with sqlite3.connect(small_sources / AB_DB) as ab:
        births = [date.fromisoformat(r[0]) for r in ab.execute("SELECT AB_DATE_NAISSANCE FROM AB_CLIENT")]
    assert max(births) <= date(2008, 10, 31)


def test_echeancier():
    dues = _schedule(date(2024, 11, 15), date(2025, 4, 1), 1, date(2025, 1, 1))
    assert dues == [date(2025, 1, 15), date(2025, 2, 15), date(2025, 3, 15)]
    assert _schedule(date(2025, 1, 31), date(2025, 3, 1), 1, date(2025, 1, 1))[1] == date(2025, 2, 28)


def test_poisson_moyenne():
    import random

    rng = random.Random(1)
    draws = [_poisson(rng, 2.0) for _ in range(4000)]
    assert 1.9 < sum(draws) / len(draws) < 2.1
