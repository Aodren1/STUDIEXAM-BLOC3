"""Fonctions de nettoyage et de normalisation (data cleaning).

Fonctions pures, sans état ni dépendance : elles sont appelées à l'identique par le moteur local
(`fusion.processing`) et par les jobs Spark (`jobs/spark`, via `mapPartitions`), ce qui garantit
que la règle testée ici est exactement celle exécutée sur le cluster.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

from fusion.referentiels import DIAL_CODES, REGIONS_PAR_DEPARTEMENT

_SPACES = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^A-Z0-9 ]+")
_EMAIL = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9\-]+(\.[a-z0-9\-]+)*\.[a-z]{2,}$")
_E164 = re.compile(r"^\+[1-9][0-9]{7,13}$")
_NATIONAL_PHONE = {
    "FR": re.compile(r"^0[1-9][0-9]{8}$"),
    "BE": re.compile(r"^0[1-9][0-9]{7,8}$"),
    "NL": re.compile(r"^0[1-9][0-9]{8}$"),
    "LU": re.compile(r"^[2-9][0-9]{5,8}$"),
}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

#: Formats de date acceptés, du plus courant au plus rare (constatés lors du profilage des sources).
DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y%m%d",
)
#: Valeurs sentinelles utilisées par les applications pour « date inconnue ».
SENTINEL_DATES = {"1900-01-01", "0001-01-01", "9999-12-31"}

ABREVIATIONS_VOIE = {
    "R": "RUE",
    "AV": "AVENUE",
    "AVE": "AVENUE",
    "BD": "BOULEVARD",
    "BLD": "BOULEVARD",
    "PL": "PLACE",
    "CH": "CHEMIN",
    "IMP": "IMPASSE",
    "ALL": "ALLEE",
    "RTE": "ROUTE",
    "QU": "QUAI",
}


# ---------------------------------------------------------------------------- texte
def strip_accents(value: str) -> str:
    """Retire les diacritiques (é → e, ç → c) et décompose les ligatures (œ → oe)."""
    value = value.replace("œ", "oe").replace("Œ", "OE").replace("æ", "ae").replace("Æ", "AE").replace("ß", "ss")
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def norm_text(value: str | None) -> str:
    """Forme canonique de comparaison : majuscules, sans accent ni ponctuation, espaces réduits."""
    if value is None:
        return ""
    text = strip_accents(str(value)).upper()
    text = _NON_ALNUM.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def name_key(full_name: str | None) -> str:
    """Clé de nom insensible à l'ordre (« DUPONT Marie » = « Marie Dupont ») ; initiales ignorées."""
    tokens = sorted(t for t in norm_text(full_name).split(" ") if len(t) > 1)
    return " ".join(tokens)


def title_case(value: str | None) -> str:
    """Prénom en casse de titre, y compris les prénoms composés (jean-pierre → Jean-Pierre)."""
    if not value:
        return ""
    words = _SPACES.sub(" ", str(value).strip()).lower().split(" ")
    return " ".join("-".join(part[:1].upper() + part[1:] for part in w.split("-")) for w in words)


def split_full_name(full_name: str | None) -> tuple[str, str, bool]:
    """Découpe AP_FULL_NAME en (NOM, Prénom, correction_nécessaire).

    Les mots entièrement en capitales sont le nom de famille (« DUPONT Marie », « Marie LE GALL ») ;
    sans indice de casse, l'ordre « Prénom Nom » est supposé (convention de l'application AssurePlus).
    Le booléen indique que la saisie n'était pas canonique (DQ04).
    """
    tokens = [t for t in _SPACES.split((full_name or "").strip()) if t]
    if not tokens:
        return "", "", True
    if len(tokens) == 1:
        return tokens[0].upper(), "", True

    def is_upper(token: str) -> bool:
        return len(token) > 1 and token == token.upper() and token != token.lower()

    upper_idx = [i for i, t in enumerate(tokens) if is_upper(t)]
    if upper_idx and len(upper_idx) < len(tokens):
        nom = " ".join(tokens[i] for i in upper_idx)
        prenom = " ".join(t for i, t in enumerate(tokens) if i not in upper_idx)
        corrected = upper_idx[0] == 0  # « NOM Prénom » : ordre inversé par rapport à la norme
    else:
        prenom, nom = tokens[0], " ".join(tokens[1:])
        corrected = True  # aucune casse exploitable : découpage par position
    return nom.upper(), title_case(prenom), corrected


def norm_address(value: str | None) -> str:
    """Adresse canonique : texte normalisé et abréviations de voie développées (R → RUE)."""
    tokens = norm_text(value).split(" ")
    return " ".join(ABREVIATIONS_VOIE.get(t, t) for t in tokens if t)


# ---------------------------------------------------------------------------- contact
def norm_email(value: str | None) -> str | None:
    """E-mail en minuscules et sans espaces ; None si la syntaxe est invalide."""
    if value is None:
        return None
    email = str(value).strip().lower()
    return email if _EMAIL.match(email) else None


def country_from_zip(zip_code: str | None) -> str:
    """Pays déduit du format du code postal (FR 5 chiffres, BE 4, LU L-4, NL 4 chiffres + 2 lettres)."""
    z = (zip_code or "").strip().upper()
    if re.fullmatch(r"[0-9]{5}", z):
        return "FR"
    if re.fullmatch(r"L-?[0-9]{4}", z):
        return "LU"
    if re.fullmatch(r"[0-9]{4} ?[A-Z]{2}", z):
        return "NL"
    if re.fullmatch(r"[0-9]{4}", z):
        return "BE"
    return "INCONNU"


def norm_zip(zip_code: str | None) -> str:
    """Code postal canonique : majuscules, sans espaces superflus (« l-1009 » → « L-1009 »)."""
    return _SPACES.sub(" ", (zip_code or "").strip().upper())


def norm_phone(value: str | None, country: str = "FR") -> str | None:
    """Téléphone au format E.164 (+33…). Un numéro national prend l'indicatif du pays du client."""
    if value is None:
        return None
    digits = re.sub(r"[^0-9+]", "", str(value))
    if not digits:
        return None
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("+33") and len(digits) == 13 and digits[3] == "0":
        digits = "+33" + digits[4:]  # « +33 (0)6… » saisi avec le zéro national
    national = _NATIONAL_PHONE.get(country, _NATIONAL_PHONE["FR"])
    if national.match(digits):
        return "+" + DIAL_CODES.get(country, "33") + digits.lstrip("0")
    return digits if _E164.match(digits) else None


def region_from_zip(zip_code: str | None) -> str | None:
    """Région administrative française déduite du département ; None hors France métropolitaine/DOM."""
    z = (zip_code or "").strip()
    if not re.fullmatch(r"[0-9]{5}", z):
        return None
    dept = z[:2]
    if dept == "20":
        return "Corse"
    if dept == "97":
        return "Outre-mer"
    return REGIONS_PAR_DEPARTEMENT.get(dept)


# ---------------------------------------------------------------------------- dates
def parse_date(value: object, with_time: bool = False) -> str | None:
    """Date (ou horodatage) au format ISO 8601, quel que soit le format d'origine.

    Retourne None pour une valeur vide, une sentinelle (1900-01-01…), une date hors [1900, 2100]
    ou une chaîne qu'aucun format connu ne reconnaît.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    else:
        raw = str(value).strip()
        if not raw or raw.startswith("0000") or raw[:10] in SENTINEL_DATES:
            return None
        raw = raw.split(".")[0]  # fractions de seconde Oracle (TIMESTAMP(6))
        parsed = None
        for fmt in DATE_FORMATS:
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    if not 1900 <= parsed.year <= 2100:
        return None
    return parsed.strftime("%Y-%m-%d %H:%M:%S" if with_time else "%Y-%m-%d")


def is_iso_date(value: object) -> bool:
    return bool(_ISO_DATE.match(str(value or "").strip()))


def is_iso_datetime(value: object) -> bool:
    return bool(_ISO_DATETIME.match(str(value or "").strip()))


def age_at(birth_iso: str | None, ref: date) -> int | None:
    """Âge révolu à la date de référence."""
    if not birth_iso:
        return None
    b = date.fromisoformat(birth_iso[:10])
    return ref.year - b.year - ((ref.month, ref.day) < (b.month, b.day))


def to_float(value: object) -> float | None:
    """Montant numérique ; accepte la virgule décimale française. None si non numérique."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None
