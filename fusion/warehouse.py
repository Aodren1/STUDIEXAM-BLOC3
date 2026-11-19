"""Entrepôt unifié : implémentation physique (MLD) du MCD de la base finale.

Les clés étrangères sont déclarées ET appliquées (PRAGMA foreign_keys) : un enregistrement qui violerait
une cardinalité du MCD (contrat sans client, sinistre sans contrat…) ne peut pas y entrer. Il est placé en
QUARANTAINE avec son motif, jamais supprimé. Le même DDL sert de base au schéma Hive des tables Parquet.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from fusion.quality import REGLES, QualityLog
from fusion.referentiels import PRODUITS, SOURCES, libelle
from fusion.sqlsafe import ident, insert_sql

DDL = """
CREATE TABLE systeme_source (
  code_source   CHAR(2)      PRIMARY KEY,
  societe       VARCHAR(40)  NOT NULL,
  sgbd          VARCHAR(40)  NOT NULL,
  annee_creation SMALLINT
);
CREATE TABLE territoire (
  code_postal   VARCHAR(10)  PRIMARY KEY,
  pays          CHAR(2)      NOT NULL,
  departement   CHAR(3),
  region        VARCHAR(40)
);
CREATE TABLE produit (
  code_produit  VARCHAR(30)  PRIMARY KEY,
  libelle       VARCHAR(60)  NOT NULL,
  branche       VARCHAR(12)  NOT NULL CHECK (branche IN ('AUTO','HABITATION','SANTE','PRO','ASSISTANCE','AUTRE'))
);
CREATE TABLE distributeur (
  code_distributeur VARCHAR(30) PRIMARY KEY,
  type_canal    VARCHAR(10)  NOT NULL CHECK (type_canal IN ('AGENCE','COURTIER','DIGITAL')),
  code_source   CHAR(2)      NOT NULL REFERENCES systeme_source(code_source)
);
CREATE TABLE personne (
  personne_id   VARCHAR(24)  PRIMARY KEY,
  nom           VARCHAR(100),
  prenom        VARCHAR(100),
  date_naissance DATE,
  email         VARCHAR(150),
  telephone     VARCHAR(16),
  nb_comptes    SMALLINT     NOT NULL CHECK (nb_comptes >= 1),
  sources       VARCHAR(5)   NOT NULL
);
CREATE TABLE client_source (
  client_uid    VARCHAR(20)  PRIMARY KEY,
  code_source   CHAR(2)      NOT NULL REFERENCES systeme_source(code_source),
  source_id     VARCHAR(30)  NOT NULL,
  personne_id   VARCHAR(24)  NOT NULL REFERENCES personne(personne_id),
  nom           VARCHAR(100),
  prenom        VARCHAR(100),
  date_naissance DATE,
  email         VARCHAR(150),
  telephone     VARCHAR(16),
  adresse       VARCHAR(255),
  code_postal   VARCHAR(10)  REFERENCES territoire(code_postal),
  date_creation TIMESTAMP,
  statut        VARCHAR(12)  NOT NULL,
  num_fiscal    VARCHAR(30),
  score_fidelite SMALLINT    CHECK (score_fidelite BETWEEN 0 AND 100),
  UNIQUE (code_source, source_id)
);
CREATE TABLE contrat (
  contrat_uid   VARCHAR(40)  PRIMARY KEY,
  code_source   CHAR(2)      NOT NULL REFERENCES systeme_source(code_source),
  source_id     VARCHAR(30)  NOT NULL,
  client_uid    VARCHAR(20)  NOT NULL REFERENCES client_source(client_uid),
  code_produit  VARCHAR(30)  NOT NULL REFERENCES produit(code_produit),
  code_distributeur VARCHAR(30) REFERENCES distributeur(code_distributeur),
  date_debut    DATE,
  date_fin      DATE,
  prime_annuelle DECIMAL(10,2) CHECK (prime_annuelle > 0),
  statut        VARCHAR(12)  NOT NULL
);
CREATE TABLE sinistre (
  sinistre_uid  VARCHAR(20)  PRIMARY KEY,
  code_source   CHAR(2)      NOT NULL REFERENCES systeme_source(code_source),
  source_id     VARCHAR(30)  NOT NULL,
  contrat_uid   VARCHAR(40)  NOT NULL REFERENCES contrat(contrat_uid),
  date_survenance TIMESTAMP,
  montant_estime DECIMAL(12,2) CHECK (montant_estime >= 0),
  statut        VARCHAR(16)  NOT NULL,
  score_fraude  DECIMAL(5,2),
  description   TEXT
);
CREATE TABLE paiement (
  paiement_uid  VARCHAR(40)  PRIMARY KEY,
  code_source   CHAR(2)      NOT NULL REFERENCES systeme_source(code_source),
  source_id     VARCHAR(40)  NOT NULL,
  contrat_uid   VARCHAR(40)  NOT NULL REFERENCES contrat(contrat_uid),
  date_paiement TIMESTAMP,
  montant       DECIMAL(10,2),
  mode          VARCHAR(12)  NOT NULL,
  statut        VARCHAR(10)  NOT NULL,
  est_tardif    BOOLEAN      NOT NULL DEFAULT 0
);
CREATE TABLE paire_doublon (
  uid_a         VARCHAR(20)  NOT NULL REFERENCES client_source(client_uid),
  uid_b         VARCHAR(20)  NOT NULL REFERENCES client_source(client_uid),
  score         DECIMAL(4,3) NOT NULL,
  niveau        VARCHAR(8)   NOT NULL CHECK (niveau IN ('CERTAIN','PROBABLE')),
  decision      VARCHAR(10)  NOT NULL DEFAULT 'AUTO',
  PRIMARY KEY (uid_a, uid_b),
  CHECK (uid_a < uid_b)
);
CREATE TABLE regle_qualite (
  code_regle    CHAR(4)      PRIMARY KEY,
  libelle       VARCHAR(120) NOT NULL,
  gravite       VARCHAR(8)   NOT NULL,
  action        VARCHAR(120) NOT NULL
);
CREATE TABLE anomalie_qualite (
  id            INTEGER      PRIMARY KEY,
  code_regle    CHAR(4)      NOT NULL REFERENCES regle_qualite(code_regle),
  code_source   CHAR(2)      NOT NULL REFERENCES systeme_source(code_source),
  entite        VARCHAR(10)  NOT NULL,
  uid           VARCHAR(60)  NOT NULL,
  valeur        VARCHAR(120)
);
CREATE TABLE quarantaine (
  entite        VARCHAR(10)  NOT NULL,
  uid           VARCHAR(40)  NOT NULL,
  motif         VARCHAR(4)   NOT NULL REFERENCES regle_qualite(code_regle),
  contenu       TEXT         NOT NULL,
  PRIMARY KEY (entite, uid)
);
CREATE INDEX ix_client_personne ON client_source(personne_id);
CREATE INDEX ix_contrat_client  ON contrat(client_uid);
CREATE INDEX ix_sinistre_contrat ON sinistre(contrat_uid);
CREATE INDEX ix_paiement_contrat ON paiement(contrat_uid);
CREATE INDEX ix_paiement_date   ON paiement(date_paiement);
"""

TABLES = frozenset(re.findall(r"CREATE TABLE (\w+)", DDL))
ORPHAN_RULE = {"contrat": "DQ06", "sinistre": "DQ10", "paiement": "DQ14"}


def _insert(con: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    cols = list(rows[0])
    sql = insert_sql(table, cols, TABLES)
    con.executemany(sql, [tuple(r[c] for c in cols) for r in rows])


def build_warehouse(path: Path, tables: dict[str, list[dict[str, Any]]], dedup, log: QualityLog) -> dict[str, int]:
    tmp = Path(str(path) + ".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript(DDL)
        _insert(
            con,
            "systeme_source",
            [
                {"code_source": k, "societe": v["societe"], "sgbd": v["sgbd"], "annee_creation": v["creation"]}
                for k, v in SOURCES.items()
            ],
        )
        _insert(
            con,
            "regle_qualite",
            [
                {"code_regle": r.code, "libelle": r.libelle, "gravite": r.gravite, "action": r.action}
                for r in REGLES.values()
            ],
        )
        territoires = {}
        for c in tables["client"]:
            if c.get("code_postal"):
                territoires[c["code_postal"]] = {
                    "code_postal": c["code_postal"],
                    "pays": c["pays"] or "??",
                    "departement": c["code_postal"][:2] if c["pays"] == "FR" else None,
                    "region": c.get("region"),
                }
        _insert(con, "territoire", list(territoires.values()))
        produits = {code: {"code_produit": code, "libelle": lib, "branche": br} for code, (br, lib) in PRODUITS.items()}
        produits["INCONNU"] = {"code_produit": "INCONNU", "libelle": libelle("INCONNU"), "branche": "AUTRE"}
        _insert(con, "produit", list(produits.values()))
        distributeurs = {}
        for c in tables["contrat"]:
            if c.get("code_distributeur"):
                distributeurs[c["code_distributeur"]] = {
                    "code_distributeur": c["code_distributeur"],
                    "type_canal": c["canal"],
                    "code_source": c["source"],
                }
        _insert(con, "distributeur", list(distributeurs.values()))
        _insert(
            con,
            "personne",
            [
                {
                    k: p[k]
                    for k in (
                        "personne_id",
                        "nom",
                        "prenom",
                        "date_naissance",
                        "email",
                        "telephone",
                        "nb_comptes",
                        "sources",
                    )
                }
                for p in dedup.personnes
            ],
        )
        _insert(
            con,
            "client_source",
            [
                {
                    "client_uid": c["client_uid"],
                    "code_source": c["source"],
                    "source_id": c["source_id"],
                    "personne_id": dedup.personne_de[c["client_uid"]],
                    "nom": c["nom"],
                    "prenom": c["prenom"],
                    "date_naissance": c["date_naissance"],
                    "email": c["email"],
                    "telephone": c["telephone"],
                    "adresse": c["adresse"],
                    "code_postal": c["code_postal"],
                    "date_creation": c["date_creation"],
                    "statut": c["statut"],
                    "num_fiscal": c["num_fiscal"],
                    "score_fidelite": c["score_fidelite"],
                }
                for c in tables["client"]
            ],
        )

        quarantaine = []
        keep: dict[str, list[dict[str, Any]]] = {}
        for entite in ("contrat", "sinistre", "paiement"):
            keep[entite] = []
            for r in tables[entite]:
                if r.get("est_orphelin"):
                    quarantaine.append(
                        {
                            "entite": entite,
                            "uid": r[f"{entite}_uid"],
                            "motif": ORPHAN_RULE[entite],
                            "contenu": json.dumps(r, ensure_ascii=False, default=str),
                        }
                    )
                else:
                    keep[entite].append(r)
        _insert(
            con,
            "contrat",
            [
                {
                    "contrat_uid": c["contrat_uid"],
                    "code_source": c["source"],
                    "source_id": c["source_id"],
                    "client_uid": c["client_uid"],
                    "code_produit": c["code_produit"],
                    "code_distributeur": c["code_distributeur"],
                    "date_debut": c["date_debut"],
                    "date_fin": c["date_fin"],
                    "prime_annuelle": c["prime_annuelle"],
                    "statut": c["statut"],
                }
                for c in keep["contrat"]
            ],
        )
        _insert(
            con,
            "sinistre",
            [
                {
                    "sinistre_uid": s["sinistre_uid"],
                    "code_source": s["source"],
                    "source_id": s["source_id"],
                    "contrat_uid": s["contrat_uid"],
                    "date_survenance": s["date_survenance"],
                    "montant_estime": s["montant_estime"],
                    "statut": s["statut"],
                    "score_fraude": s["score_fraude"],
                    "description": s["description"],
                }
                for s in keep["sinistre"]
            ],
        )
        _insert(
            con,
            "paiement",
            [
                {
                    "paiement_uid": p["paiement_uid"],
                    "code_source": p["source"],
                    "source_id": p["source_id"],
                    "contrat_uid": p["contrat_uid"],
                    "date_paiement": p["date_paiement"],
                    "montant": p["montant"],
                    "mode": p["mode"],
                    "statut": p["statut"],
                    "est_tardif": int(bool(p.get("est_tardif"))),
                }
                for p in keep["paiement"]
            ],
        )
        _insert(
            con,
            "paire_doublon",
            [
                {
                    "uid_a": p["uid_a"],
                    "uid_b": p["uid_b"],
                    "score": p["score"],
                    "niveau": p["niveau"],
                    "decision": "AUTO" if p["niveau"] == "CERTAIN" else "A_VALIDER",
                }
                for p in dedup.paires
            ],
        )
        _insert(
            con,
            "anomalie_qualite",
            [
                {"code_regle": a.code, "code_source": a.source, "entite": a.entite, "uid": a.uid, "valeur": a.valeur}
                for a in log.anomalies
            ],
        )
        _insert(con, "quarantaine", quarantaine)
        con.commit()
        counts = {
            t: con.execute(f"SELECT COUNT(*) FROM {ident(t, TABLES)}").fetchone()[0]  # nosec B608  # noqa: S608
            for t in sorted(TABLES)
        }
    finally:
        con.close()
    tmp.replace(path)  # bascule atomique : le tableau de bord ne lit jamais un entrepôt à moitié écrit
    return counts
