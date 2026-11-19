"""Règles de nettoyage : un cas nominal et les cas limites rencontrés dans les sources."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from fusion import cleaning as c


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Élodie  Lefèvre", "ELODIE LEFEVRE"),
        ("  d'Arc-en-Ciel ", "D ARC EN CIEL"),
        ("Cœur", "COEUR"),
        (None, ""),
    ],
)
def test_norm_text(raw, expected):
    assert c.norm_text(raw) == expected


def test_name_key_insensible_a_l_ordre_et_aux_initiales():
    assert c.name_key("DUPONT Marie") == c.name_key("marie dupont") == c.name_key("Marie J. Dupont")


@pytest.mark.parametrize(
    ("full", "expected"),
    [
        ("Marie DUPONT", ("DUPONT", "Marie", False)),
        ("DUPONT Marie", ("DUPONT", "Marie", True)),
        ("Marie LE GALL", ("LE GALL", "Marie", False)),
        ("jean-pierre martin", ("MARTIN", "Jean-Pierre", True)),
        ("Marie Dupont", ("DUPONT", "Marie", True)),
        ("Madonna", ("MADONNA", "", True)),
        ("", ("", "", True)),
        (None, ("", "", True)),
    ],
)
def test_split_full_name(full, expected):
    assert c.split_full_name(full) == expected


def test_title_case_prenom_compose():
    assert c.title_case("MARIE-CLAIRE") == "Marie-Claire"
    assert c.title_case("") == ""


def test_norm_address_developpe_les_abreviations():
    assert c.norm_address("12 R de la République") == "12 RUE DE LA REPUBLIQUE"
    assert c.norm_address("3 BD Victor-Hugo") == "3 BOULEVARD VICTOR HUGO"
    assert c.norm_address(None) == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Jean.Dupont@Orange.FR ", "jean.dupont@orange.fr"),
        ("jean.dupont at orange.fr", None),
        ("jean@localhost", None),
        (None, None),
    ],
)
def test_norm_email(raw, expected):
    assert c.norm_email(raw) == expected


@pytest.mark.parametrize(
    ("raw", "country", "expected"),
    [
        ("01 23 45 67 89", "FR", "+33123456789"),
        ("06.12.34.56.78", "FR", "+33612345678"),
        ("0033612345678", "FR", "+33612345678"),
        ("+33 (0)612345678", "FR", "+33612345678"),
        ("0470123456", "BE", "+32470123456"),  # anomalie R1-01 du Bloc 2 : ne doit pas devenir +33
        ("+352 621234567", "LU", "+352621234567"),
        ("621234567", "LU", "+352621234567"),
        ("06 12 34", "FR", None),
        ("NC", "FR", None),
        (None, "FR", None),
    ],
)
def test_norm_phone(raw, country, expected):
    assert c.norm_phone(raw, country) == expected


@pytest.mark.parametrize(
    ("zip_code", "country", "region"),
    [
        ("75011", "FR", "Île-de-France"),
        ("20000", "FR", "Corse"),
        ("97400", "FR", "Outre-mer"),
        ("1000", "BE", None),
        ("L-1009", "LU", None),
        ("1012 AB", "NL", None),
        ("ABC", "INCONNU", None),
        (None, "INCONNU", None),
    ],
)
def test_pays_et_region(zip_code, country, region):
    assert c.country_from_zip(zip_code) == country
    assert c.region_from_zip(zip_code) == region


def test_norm_zip():
    assert c.norm_zip(" l-1009 ") == "L-1009"


@pytest.mark.parametrize(
    ("raw", "with_time", "expected"),
    [
        ("1985-04-12", False, "1985-04-12"),
        ("12/04/1985", False, "1985-04-12"),
        ("12-04-1985", False, "1985-04-12"),
        ("2025-03-02T14:05:00", True, "2025-03-02 14:05:00"),
        ("02/03/2025 14:05", True, "2025-03-02 14:05:00"),
        ("2025-03-02 14:05:00.123456", True, "2025-03-02 14:05:00"),
        ("1900-01-01", False, None),
        ("31/02/1985", False, None),
        ("2025-13-45 10:00:00", True, None),
        ("N/A", False, None),
        ("", False, None),
        ("1850-01-01", False, None),
        (None, False, None),
        (date(2020, 5, 1), False, "2020-05-01"),
        (datetime(2020, 5, 1, 8, 30), True, "2020-05-01 08:30:00"),
    ],
)
def test_parse_date(raw, with_time, expected):
    assert c.parse_date(raw, with_time) == expected


def test_formats_iso():
    assert c.is_iso_date("2020-01-31") and not c.is_iso_date("31/01/2020")
    assert c.is_iso_datetime("2020-01-31 10:00:00") and not c.is_iso_datetime("2020-01-31T10:00:00")


def test_age_revolu():
    ref = date(2026, 10, 31)
    assert c.age_at("2000-10-31", ref) == 26
    assert c.age_at("2000-11-01", ref) == 25
    assert c.age_at(None, ref) is None


@pytest.mark.parametrize(("raw", "expected"), [("12,50", 12.5), ("1 200.00", 1200.0), (7, 7.0), ("abc", None),
                                               ("", None), (None, None)])
def test_to_float(raw, expected):
    assert c.to_float(raw) == expected
