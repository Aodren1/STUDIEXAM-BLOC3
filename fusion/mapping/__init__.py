"""Moteur de mapping déclaratif (data mapping as code).

    spec = MappingSpec.load()                 # lit et valide mapping.toml
    record = spec.map_record("client", "AP", ligne_source, journal_qualite)
    spec.export_csv(path)                     # tMap Talend, documentation

Le même objet est sérialisable (`spec.raw`) pour être diffusé aux exécuteurs Spark.
"""

from __future__ import annotations

import csv
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fusion.mapping.transforms import REGISTRY, Ctx
from fusion.quality import QualityLog

DEFAULT_PATH = Path(__file__).with_name("mapping.toml")
ENTITES = ("client", "contrat", "sinistre", "paiement")
SOURCES = ("AB", "AP")


class MappingError(ValueError):
    """Spécification de mapping incohérente (détectée au chargement, jamais en cours de traitement)."""


@dataclass(frozen=True)
class Champ:
    cible: str
    type: str
    regle: str
    colonnes: dict[str, str | None]  # source -> colonne (None : aucune, « =X » : constante)
    transformations: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class Entite:
    nom: str
    tables: dict[str, str]
    cle: str
    topic: str
    champs: tuple[Champ, ...]


class MappingSpec:
    def __init__(self, raw: dict[str, Any]):
        self.raw = raw
        self.version = raw.get("meta", {}).get("version", "?")
        self.entites: dict[str, Entite] = {}
        for nom in ENTITES:
            if nom not in raw:
                raise MappingError(f"entité absente du mapping : {nom}")
            e = raw[nom]
            champs = tuple(
                Champ(
                    cible=ch["cible"],
                    type=ch.get("type", ""),
                    regle=ch.get("regle", ""),
                    colonnes={"AB": ch.get("ab"), "AP": ch.get("ap")},
                    transformations={"AB": tuple(ch.get("t_ab", [])), "AP": tuple(ch.get("t_ap", []))},
                )
                for ch in e.get("champ", [])
            )
            self.entites[nom] = Entite(nom, {"AB": e["table_ab"], "AP": e["table_ap"]}, e["cle"], e["topic"], champs)
        self.validate()

    @classmethod
    def load(cls, path: Path | str = DEFAULT_PATH) -> MappingSpec:
        with open(path, "rb") as fh:
            return cls(tomllib.load(fh))

    # ------------------------------------------------------------------ validation
    def validate(self, source_columns: dict[str, list[str]] | None = None) -> None:
        """Contrôle statique : transformations connues, clé présente, colonnes existantes dans les sources."""
        if source_columns is None:
            from fusion.sources.generator import DDL_SQLITE

            source_columns = {t: [c.split()[0] for c in ddl.split(", ")] for t, ddl in DDL_SQLITE.items()}
        for e in self.entites.values():
            cibles = [ch.cible for ch in e.champs]
            if e.cle not in cibles:
                raise MappingError(f"{e.nom} : la clé {e.cle} n'est pas mappée")
            if len(set(cibles)) != len(cibles):
                raise MappingError(f"{e.nom} : attribut cible en double")
            for ch in e.champs:
                for src in SOURCES:
                    for t in ch.transformations[src]:
                        if t not in REGISTRY:
                            raise MappingError(f"{e.nom}.{ch.cible} : transformation inconnue « {t} »")
                    col = ch.colonnes[src]
                    if col and not col.startswith("=") and col not in source_columns.get(e.tables[src], []):
                        raise MappingError(f"{e.nom}.{ch.cible} : colonne {col} absente de {e.tables[src]}")

    # ------------------------------------------------------------------ exécution
    def map_record(self, entite: str, src: str, row: dict[str, Any], log: QualityLog) -> dict[str, Any]:
        """Applique le mapping à une ligne source et retourne l'enregistrement du modèle unifié."""
        e = self.entites[entite]
        key_col = next(ch.colonnes[src] for ch in e.champs if ch.cible == e.cle)
        ctx = Ctx(src=src, entite=entite, uid=f"{src}-{row.get(key_col)}", row=row, log=log)
        ctx.out["source"] = src
        for ch in e.champs:
            col = ch.colonnes[src]
            chain = ch.transformations[src]
            if col and col.startswith("="):
                value: Any = col[1:]
            elif col:
                value = row.get(col)
            else:
                value = None
            if not col and not chain:
                ctx.out[ch.cible] = None  # attribut propre à l'autre source
                continue
            for t in chain:
                value = REGISTRY[t](value, ctx)
            ctx.out[ch.cible] = value
        return ctx.out

    def columns(self, entite: str) -> list[str]:
        return ["source", *[ch.cible for ch in self.entites[entite].champs]]

    # ------------------------------------------------------------------ export
    def rows(self) -> list[dict[str, str]]:
        out = []
        for e in self.entites.values():
            for ch in e.champs:
                out.append(
                    {
                        "entite": e.nom,
                        "attribut_cible": ch.cible,
                        "type_cible": ch.type,
                        "table_ab": e.tables["AB"],
                        "colonne_ab": ch.colonnes["AB"] or "—",
                        "transformation_ab": " → ".join(ch.transformations["AB"]) or "—",
                        "table_ap": e.tables["AP"],
                        "colonne_ap": ch.colonnes["AP"] or "—",
                        "transformation_ap": " → ".join(ch.transformations["AP"]) or "—",
                        "regle": ch.regle,
                    }
                )
        return out

    def export_csv(self, path: Path | str, base: Path | str | None = None) -> int:
        """Exporte le mapping (CSV « ; »). Le fichier doit être un .csv situé sous `base`
        (répertoire courant par défaut) : un chemin reçu en argument ne peut pas écrire ailleurs."""
        racine = os.path.realpath(base if base is not None else os.getcwd())
        cible = os.path.realpath(os.path.join(racine, path))
        if os.path.commonpath((racine, cible)) != racine:
            raise ValueError(f"export refusé : {path} sort du répertoire {racine}")
        if not cible.lower().endswith(".csv"):
            raise ValueError(f"export refusé : {path} n'est pas un fichier .csv")
        rows = self.rows()
        # chemin confiné juste au-dessus (realpath + commonpath, testé) ; Sonar ne reconnaît pas ce contrôle
        with open(cible, "w", newline="", encoding="utf-8") as fh:  # NOSONAR python:S8707
            w = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter=";")
            w.writeheader()
            w.writerows(rows)
        return len(rows)
