"""Règles de qualité des données (DQ) et journal des anomalies.

Les codes DQ01 à DQ15 reprennent le catalogue établi au Bloc 1 et éprouvé en recette au Bloc 2 ;
DQ16 et DQ17 sont ajoutées au Bloc 3 (rejets techniques du bus et montants aberrants).
Principe : on ne supprime jamais une ligne en silence. Elle est corrigée, isolée (est_orphelin)
ou mise en quarantaine, et chaque décision laisse une trace consultable.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Regle:
    code: str
    libelle: str
    gravite: str  # info | mineure | majeure | critique
    action: str


REGLES: dict[str, Regle] = {
    r.code: r
    for r in [
        Regle("DQ01", "Date de naissance hors format YYYY-MM-DD", "mineure", "Convertie au format ISO"),
        Regle("DQ02", "Date de naissance inexploitable ou sentinelle", "majeure", "Mise à NULL, exclue des âges"),
        Regle("DQ03", "E-mail absent ou invalide", "mineure", "Mis à NULL"),
        Regle("DQ04", "Nom complet non canonique (« NOM Prénom », sans casse)", "info", "Découpé en nom / prénom"),
        Regle("DQ05", "Valeur hors référentiel ou casse non conforme", "info", "Mappée sur le référentiel unifié"),
        Regle("DQ06", "Contrat orphelin (client inconnu)", "critique", "Conservé, isolé des statistiques clients"),
        Regle("DQ07", "Prime nulle ou négative", "majeure", "Exclue des indicateurs financiers"),
        Regle("DQ08", "Date de sinistre hors format", "mineure", "Convertie au format ISO"),
        Regle("DQ09", "Date de sinistre inexploitable", "majeure", "Mise à NULL, exclue des séries"),
        Regle("DQ10", "Sinistre orphelin (contrat inconnu)", "critique", "Conservé, isolé des ratios S/P"),
        Regle("DQ11", "Numéro fiscal absent (client français)", "mineure", "Aucune : clé de rapprochement manquante"),
        Regle("DQ12", "Téléphone non normalisable en E.164", "mineure", "Mis à NULL"),
        Regle("DQ13", "Paiement sans statut de transaction (source AB)", "info", "Réputé encaissé (REUSSI)"),
        Regle("DQ14", "Paiement orphelin (contrat inconnu)", "critique", "Conservé, isolé des encaissements"),
        Regle("DQ15", "Paiement plus de 31 jours après la fin du contrat", "majeure", "Conservé, signalé"),
        Regle("DQ16", "Message illisible sur le bus (JSON ou schéma invalide)", "critique", "Envoyé en quarantaine"),
        Regle("DQ17", "Montant de sinistre aberrant (négatif ou > 1 M€)", "majeure", "Exclu des moyennes"),
    ]
}

GRAVITES = ["critique", "majeure", "mineure", "info"]


@dataclass
class Anomalie:
    code: str
    source: str
    entite: str
    uid: str
    valeur: str | None = None


@dataclass
class QualityLog:
    """Journal des anomalies d'une exécution ; une ligne par règle déclenchée."""

    anomalies: list[Anomalie] = field(default_factory=list)
    _seen_structural: set[tuple[str, str]] = field(default_factory=set)

    def flag(self, code: str, source: str, entite: str, uid: str, valeur: object = None) -> None:
        if code not in REGLES:
            raise KeyError(f"règle de qualité inconnue : {code}")
        text = None if valeur is None else str(valeur)[:120]
        self.anomalies.append(Anomalie(code, source, entite, uid, text))

    def flag_once(self, code: str, source: str, entite: str, valeur: object = None) -> None:
        """Règle structurelle (propriété d'une table entière) : tracée une seule fois."""
        if (code, source) not in self._seen_structural:
            self._seen_structural.add((code, source))
            self.flag(code, source, entite, "*", valeur)

    def counts(self) -> dict[str, int]:
        return dict(sorted(Counter(a.code for a in self.anomalies).items()))

    def counts_by(self, *keys: str) -> dict[tuple, int]:
        return dict(Counter(tuple(getattr(a, k) for k in keys) for a in self.anomalies))

    def extend(self, other: QualityLog) -> None:
        self.anomalies.extend(other.anomalies)
