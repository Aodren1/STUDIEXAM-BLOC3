"""Référentiels du modèle unifié : codes cibles et tables de correspondance des valeurs sources.

Toute valeur source absente de ces tables est mappée sur INCONNU et tracée (règle DQ05),
jamais devinée : un nouveau libellé côté source doit faire l'objet d'une décision métier.
"""

from __future__ import annotations

SOURCES = {
    "AB": {"societe": "AbAssurance", "sgbd": "Oracle 19c", "schema": "AB_*", "creation": 1930},
    "AP": {"societe": "AssurePlus", "sgbd": "Microsoft SQL Server 2019", "schema": "AP_*", "creation": 1970},
}

DIAL_CODES = {"FR": "33", "BE": "32", "NL": "31", "LU": "352"}

STATUT_CLIENT = {
    "ACTIF": "ACTIF",
    "INACTIF": "INACTIF",
    "RESILIE": "RESILIE",
    "PROSPECT": "PROSPECT",
    "active": "ACTIF",
    "inactive": "INACTIF",
    "churned": "RESILIE",
    "lead": "PROSPECT",
}
STATUT_CONTRAT = {
    "ACTIF": "ACTIF",
    "RESILIE": "RESILIE",
    "SUSPENDU": "SUSPENDU",
    "EN_ATTENTE": "EN_ATTENTE",
    "active": "ACTIF",
    "cancelled": "RESILIE",
    "suspended": "SUSPENDU",
    "pending": "EN_ATTENTE",
}
STATUT_SINISTRE = {
    "OUVERT": "OUVERT",
    "EN_COURS": "EN_INSTRUCTION",
    "CLOS": "CLOS_INDEMNISE",
    "REFUSE": "REFUSE",
    "OPEN": "OUVERT",
    "UNDER_REVIEW": "EN_INSTRUCTION",
    "SETTLED": "CLOS_INDEMNISE",
    "REJECTED": "REFUSE",
}
STATUT_PAIEMENT = {"SUCCEEDED": "REUSSI", "FAILED": "ECHOUE", "REFUNDED": "REMBOURSE", "PENDING": "EN_ATTENTE"}
MODE_PAIEMENT = {
    "PRELEVEMENT": "PRELEVEMENT",
    "CB": "CARTE",
    "CHEQUE": "CHEQUE",
    "VIREMENT": "VIREMENT",
    "SEPA_DD": "PRELEVEMENT",
    "CARD": "CARTE",
    "APPLE_PAY": "PORTEFEUILLE",
    "PAYPAL": "PORTEFEUILLE",
}

#: Produits : code source → (branche du modèle unifié, libellé)
PRODUITS = {
    "SANTE": ("SANTE", "Complémentaire santé"),
    "HABITATION": ("HABITATION", "Multirisque habitation"),
    "AUTO": ("AUTO", "Automobile"),
    "PRO": ("PRO", "Multirisque professionnelle"),
    "AUTO_TOUS_RISQUES": ("AUTO", "Auto tous risques"),
    "AUTO_TIERS": ("AUTO", "Auto au tiers"),
    "AUTO_FLEX_KM": ("AUTO", "Auto au kilomètre"),
    "ASSIST_PREMIUM": ("ASSISTANCE", "Assistance premium"),
    "MRH_ESSENTIEL": ("HABITATION", "Habitation essentiel"),
}
BRANCHES = ["AUTO", "HABITATION", "SANTE", "PRO", "ASSISTANCE"]

LIBELLES = {
    "AUTO": "Automobile",
    "HABITATION": "Habitation",
    "SANTE": "Santé",
    "PRO": "Professionnelle",
    "ASSISTANCE": "Assistance",
    "ACTIF": "Actif",
    "INACTIF": "Inactif",
    "RESILIE": "Résilié",
    "PROSPECT": "Prospect",
    "SUSPENDU": "Suspendu",
    "EN_ATTENTE": "En attente",
    "OUVERT": "Ouvert",
    "EN_INSTRUCTION": "En instruction",
    "CLOS_INDEMNISE": "Clos indemnisé",
    "REFUSE": "Refusé",
    "REUSSI": "Réussi",
    "ECHOUE": "Échoué",
    "REMBOURSE": "Remboursé",
    "PRELEVEMENT": "Prélèvement",
    "CARTE": "Carte",
    "CHEQUE": "Chèque",
    "VIREMENT": "Virement",
    "PORTEFEUILLE": "Portefeuille mobile",
    "AGENCE": "Agence",
    "COURTIER": "Courtier",
    "DIGITAL": "Application mobile",
    "INCONNU": "Inconnu",
    "FR": "France",
    "BE": "Belgique",
    "LU": "Luxembourg",
    "NL": "Pays-Bas",
}

_REGIONS = {
    "Auvergne-Rhône-Alpes": ["01", "03", "07", "15", "26", "38", "42", "43", "63", "69", "73", "74"],
    "Bourgogne-Franche-Comté": ["21", "25", "39", "58", "70", "71", "89", "90"],
    "Bretagne": ["22", "29", "35", "56"],
    "Centre-Val de Loire": ["18", "28", "36", "37", "41", "45"],
    "Grand Est": ["08", "10", "51", "52", "54", "55", "57", "67", "68", "88"],
    "Hauts-de-France": ["02", "59", "60", "62", "80"],
    "Île-de-France": ["75", "77", "78", "91", "92", "93", "94", "95"],
    "Normandie": ["14", "27", "50", "61", "76"],
    "Nouvelle-Aquitaine": ["16", "17", "19", "23", "24", "33", "40", "47", "64", "79", "86", "87"],
    "Occitanie": ["09", "11", "12", "30", "31", "32", "34", "46", "48", "65", "66", "81", "82"],
    "Pays de la Loire": ["44", "49", "53", "72", "85"],
    "Provence-Alpes-Côte d'Azur": ["04", "05", "06", "13", "83", "84"],
}
REGIONS_PAR_DEPARTEMENT = {d: region for region, depts in _REGIONS.items() for d in depts}


def libelle(code: str | None) -> str:
    """Libellé d'affichage d'un code de référentiel."""
    if code is None:
        return "Non renseigné"
    return LIBELLES.get(code, code)
