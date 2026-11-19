"""Étape 4 — rôle Spark : agrégats de la zone gold, consommés tels quels par le tableau de bord.

Un document JSON par exécution, découpé par rubrique : synthese, clients, contrats, sinistres, finances,
doublons, qualite. Aucune donnée personnelle n'y figure, sauf la liste des paires de doublons, réservée
au rôle administrateur et servie par une route distincte.

Conventions d'indicateurs (validées avec le métier) :
  - portefeuille actif   : contrats au statut ACTIF, non orphelins ;
  - primes annualisées   : somme des primes annuelles du portefeuille actif ;
  - encaissements        : paiements au statut REUSSI (les remboursements sont déduits) ;
  - ratio S/P (12 mois)  : montants estimés des sinistres survenus sur 12 mois glissants
                           / primes acquises sur la même période (approximées par les encaissements) ;
  - fréquence            : sinistres survenus sur 12 mois / contrats actifs.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Any

from fusion.cleaning import age_at
from fusion.quality import GRAVITES, REGLES, QualityLog
from fusion.referentiels import BRANCHES, libelle

SOURCES = ("AB", "AP")
TRANCHES_AGE = [(18, 25), (26, 35), (36, 45), (46, 55), (56, 65), (66, 75), (76, 120)]
TRANCHES_MONTANT = [0, 250, 500, 1000, 2500, 5000, 10000, 25000, float("inf")]
SEUIL_FRAUDE = 60.0


def _months(ref: date, n: int) -> list[str]:
    out, y, m = [], ref.year, ref.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def _by_source(rows, key) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(SOURCES, 0))
    for r in rows:
        out[key(r)][r["source"]] += 1
    return out


def _series(categories: list[str], counts: dict[str, dict[str, float]], digits: int = 0) -> dict[str, Any]:
    """Format commun des graphiques : catégories + une série par source."""
    return {
        "categories": categories,
        "libelles": [libelle(c) for c in categories],
        "series": {s: [round(counts.get(c, {}).get(s, 0), digits) for c in categories] for s in SOURCES},
    }


def _pct(num: float, den: float, digits: int = 1) -> float | None:
    return round(100 * num / den, digits) if den else None


def _tranche(value: float, bornes: list[float]) -> int:
    for i in range(len(bornes) - 1):
        if bornes[i] <= value < bornes[i + 1]:
            return i
    return len(bornes) - 2


def build_gold(
    tables: dict[str, list[dict[str, Any]]],
    dedup,
    log: QualityLog,
    ref: date,
    *,
    reconciliation: list[dict[str, Any]] | None = None,
    run: dict[str, Any] | None = None,
) -> dict:
    clients = tables["client"]
    contrats = [c for c in tables["contrat"] if not c.get("est_orphelin")]
    contrats_by_uid = {c["contrat_uid"]: c for c in contrats}
    sinistres = [s for s in tables["sinistre"] if not s.get("est_orphelin")]
    paiements = [p for p in tables["paiement"] if not p.get("est_orphelin")]
    for rec in sinistres + paiements:
        rec["_branche"] = contrats_by_uid[rec["contrat_uid"]]["branche"]

    doc = {
        "date_reference": ref.isoformat(),
        "clients": _clients(clients, contrats, dedup, ref),
        "contrats": _contrats(contrats, ref),
        "sinistres": _sinistres(sinistres, contrats, ref),
        "finances": _finances(paiements, sinistres, contrats, ref),
        "doublons": _doublons(dedup),
        "qualite": _qualite(log, tables, reconciliation or [], run or {}),
    }
    doc["synthese"] = _synthese(doc)
    return doc


# ---------------------------------------------------------------------------- clients
def _clients(clients, contrats, dedup, ref):
    personne_de = dedup.personne_de
    actifs_par_client = Counter(c["client_uid"] for c in contrats if c["statut"] == "ACTIF")
    actifs_par_personne: Counter[str] = Counter()
    for uid, n in actifs_par_client.items():
        if uid in personne_de:
            actifs_par_personne[personne_de[uid]] += n
    portefeuille = Counter(p["sources"] for p in dedup.personnes)

    fr = [c for c in clients if c.get("region")]
    regions = _by_source(fr, lambda c: c["region"])
    region_order = sorted(regions, key=lambda r: -sum(regions[r].values()))
    pays = _by_source(clients, lambda c: c.get("pays") or "INCONNU")
    statuts = _by_source(clients, lambda c: c["statut"])

    ages: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(SOURCES, 0))
    sans_age = Counter()
    for c in clients:
        a = age_at(c.get("date_naissance"), ref)
        if a is None:
            sans_age[c["source"]] += 1
            continue
        lo, hi = next(((lo, hi) for lo, hi in TRANCHES_AGE if lo <= a <= hi), TRANCHES_AGE[-1])
        ages[f"{lo}–{hi}" if hi < 120 else f"{lo} et +"][c["source"]] += 1
    age_labels = [f"{lo}–{hi}" if hi < 120 else f"{lo} et +" for lo, hi in TRANCHES_AGE]

    cohortes = _by_source(clients, lambda c: (c.get("date_creation") or "????")[:4])
    annees = [str(y) for y in range(ref.year - 14, ref.year + 1)]

    equip = Counter(min(n, 4) for n in actifs_par_personne.values())
    n_personnes = len(dedup.personnes)
    communs = portefeuille.get("AB+AP", 0)
    return {
        "kpi": {
            "comptes": {s: sum(c["source"] == s for c in clients) for s in SOURCES},
            "comptes_total": len(clients),
            "personnes": n_personnes,
            "clients_communs": communs,
            "taux_recouvrement_ap": _pct(communs, sum(1 for p in dedup.personnes if "AP" in p["sources"])),
            "personnes_actives": len(actifs_par_personne),
            "age_median": median(a for c in clients if (a := age_at(c.get("date_naissance"), ref)) is not None)
            if clients
            else None,
            "sans_date_naissance": dict(sans_age),
        },
        "par_region": {**_series(region_order, regions), "libelles": region_order},
        "par_pays": _series([p for p in ("FR", "BE", "LU", "NL", "INCONNU") if p in pays], pays),
        "par_statut": _series(
            [s for s in ("ACTIF", "INACTIF", "RESILIE", "PROSPECT", "INCONNU") if s in statuts], statuts
        ),
        "par_age": {**_series(age_labels, ages), "libelles": age_labels},
        "cohortes": {**_series(annees, cohortes), "libelles": annees},
        "portefeuille": {
            "categories": ["AB", "AP", "AB+AP"],
            "libelles": ["AbAssurance seule", "AssurePlus seule", "Clients des deux"],
            "valeurs": [portefeuille.get("AB", 0), portefeuille.get("AP", 0), communs],
        },
        "multi_equipement": {
            "categories": ["1", "2", "3", "4+"],
            "valeurs": [equip.get(1, 0), equip.get(2, 0), equip.get(3, 0), equip.get(4, 0)],
        },
    }


# ---------------------------------------------------------------------------- contrats
def _contrats(contrats, ref):
    actifs = [c for c in contrats if c["statut"] == "ACTIF"]
    branches = _by_source(actifs, lambda c: c["branche"])
    statuts = _by_source(contrats, lambda c: c["statut"])
    canaux = _by_source(actifs, lambda c: c["canal"])
    primes: dict[str, dict[str, list[float]]] = defaultdict(lambda: {s: [] for s in SOURCES})
    for c in actifs:
        if c.get("prime_annuelle"):
            primes[c["branche"]][c["source"]].append(c["prime_annuelle"])
    prime_moy = {b: {s: (sum(v) / len(v) if v else 0) for s, v in d.items()} for b, d in primes.items()}

    mois = _months(ref, 24)
    souscriptions = _by_source([c for c in contrats if c.get("date_debut")], lambda c: c["date_debut"][:7])
    distributeurs = Counter((c["code_distributeur"], c["canal"], c["source"]) for c in actifs)
    branche_order = [b for b in BRANCHES if b in branches]
    durees = [(ref - date.fromisoformat(c["date_debut"])).days / 365.25 for c in actifs if c.get("date_debut")]
    primes_actives = [c["prime_annuelle"] for c in actifs if c.get("prime_annuelle")]
    return {
        "kpi": {
            "contrats": len(contrats),
            "actifs": {s: sum(c["source"] == s for c in actifs) for s in SOURCES},
            "actifs_total": len(actifs),
            "primes_annualisees": round(sum(primes_actives), 2),
            "prime_moyenne": round(sum(primes_actives) / len(primes_actives), 2) if primes_actives else None,
            "anciennete_moyenne_ans": round(sum(durees) / len(durees), 1) if durees else None,
            "taux_resiliation": _pct(
                statuts.get("RESILIE", {}).get("AB", 0) + statuts.get("RESILIE", {}).get("AP", 0), len(contrats)
            ),
        },
        "par_branche": _series(branche_order, branches),
        "prime_moyenne_branche": _series(branche_order, prime_moy, digits=2),
        "par_statut": _series(
            [s for s in ("ACTIF", "SUSPENDU", "EN_ATTENTE", "RESILIE", "INCONNU") if s in statuts], statuts
        ),
        "par_canal": _series([c for c in ("AGENCE", "COURTIER", "DIGITAL") if c in canaux], canaux),
        "souscriptions_mensuelles": {**_series(mois, souscriptions), "libelles": mois},
        "top_distributeurs": [
            {"code": code, "canal": libelle(canal), "source": src, "contrats_actifs": n}
            for (code, canal, src), n in distributeurs.most_common(10)
        ],
    }


# ---------------------------------------------------------------------------- sinistres
def _sinistres(sinistres, contrats, ref):
    debut_12m = (ref - timedelta(days=365)).isoformat()
    dated = [s for s in sinistres if s.get("date_survenance")]
    recents = [s for s in dated if s["date_survenance"][:10] > debut_12m]
    mois = _months(ref, 24)
    par_mois = _by_source(dated, lambda s: s["date_survenance"][:7])
    statuts = _by_source(sinistres, lambda s: s["statut"])
    branches = _by_source(sinistres, lambda s: s["_branche"])
    montants = [s["montant_estime"] for s in sinistres if s.get("montant_estime") is not None]
    cout: dict[str, dict[str, list[float]]] = defaultdict(lambda: {s: [] for s in SOURCES})
    for s in sinistres:
        if s.get("montant_estime") is not None:
            cout[s["_branche"]][s["source"]].append(s["montant_estime"])
    cout_moy = {b: {s: (sum(v) / len(v) if v else 0) for s, v in d.items()} for b, d in cout.items()}
    tranches = _by_source(
        [s for s in sinistres if s.get("montant_estime") is not None],
        lambda s: str(_tranche(s["montant_estime"], TRANCHES_MONTANT)),
    )
    tranche_labels = [
        f"{int(TRANCHES_MONTANT[i]):,} – {int(TRANCHES_MONTANT[i + 1]):,} €".replace(",", " ")
        for i in range(len(TRANCHES_MONTANT) - 2)
    ] + [f"{int(TRANCHES_MONTANT[-2]):,} € et +".replace(",", " ")]
    scores = [s["score_fraude"] for s in sinistres if s.get("score_fraude") is not None]
    fraude = Counter(min(int(v // 10), 9) for v in scores)
    actifs = sum(c["statut"] == "ACTIF" for c in contrats)
    ouverts = [s for s in sinistres if s["statut"] in ("OUVERT", "EN_INSTRUCTION")]
    clos = [s for s in sinistres if s["statut"] in ("CLOS_INDEMNISE", "REFUSE")]
    branche_order = [b for b in BRANCHES if b in branches]
    return {
        "kpi": {
            "sinistres": len(sinistres),
            "survenus_12_mois": len(recents),
            "ouverts": len(ouverts),
            "montant_total": round(sum(montants), 2),
            "cout_moyen": round(sum(montants) / len(montants), 2) if montants else None,
            "cout_median": round(median(montants), 2) if montants else None,
            "taux_refus": _pct(sum(s["statut"] == "REFUSE" for s in clos), len(clos)),
            "frequence_12_mois": _pct(len(recents), actifs),
            "suspicion_fraude": sum(v >= SEUIL_FRAUDE for v in scores),
        },
        "mensuels": {**_series(mois, par_mois), "libelles": mois},
        "par_statut": _series(
            [s for s in ("OUVERT", "EN_INSTRUCTION", "CLOS_INDEMNISE", "REFUSE", "INCONNU") if s in statuts], statuts
        ),
        "par_branche": _series(branche_order, branches),
        "cout_moyen_branche": _series(branche_order, cout_moy, digits=2),
        "tranches_montant": {
            **_series([str(i) for i in range(len(tranche_labels))], tranches),
            "libelles": tranche_labels,
        },
        "score_fraude": {
            "categories": [f"{10 * i}–{10 * i + 9}" for i in range(10)],
            "valeurs": [fraude.get(i, 0) for i in range(10)],
            "seuil": SEUIL_FRAUDE,
            "note": "Score fourni par AssurePlus uniquement ; AbAssurance n'a pas de modèle de fraude.",
        },
    }


# ---------------------------------------------------------------------------- finances
def _finances(paiements, sinistres, contrats, ref):
    debut_12m = (ref - timedelta(days=365)).isoformat()
    mois = [m for m in _months(ref, 22)]
    encaisse: dict[str, dict[str, float]] = defaultdict(lambda: dict.fromkeys(SOURCES, 0.0))
    modes: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(SOURCES, 0))
    statuts_ap = Counter()
    encaisse_branche_12m: Counter[str] = Counter()
    total_12m = dict.fromkeys(SOURCES, 0.0)
    for p in paiements:
        if p["source"] == "AP":
            statuts_ap[p["statut"]] += 1
        if p["statut"] not in ("REUSSI", "REMBOURSE") or p.get("montant") is None or not p.get("date_paiement"):
            continue
        signed = p["montant"] if p["statut"] == "REUSSI" else -p["montant"]
        encaisse[p["date_paiement"][:7]][p["source"]] += signed
        if p["statut"] == "REUSSI":
            modes[p["mode"]][p["source"]] += 1
        if p["date_paiement"][:10] > debut_12m:
            total_12m[p["source"]] += signed
            encaisse_branche_12m[p["_branche"]] += signed

    charge_12m: Counter[str] = Counter()
    for s in sinistres:
        recent = s.get("date_survenance") and s["date_survenance"][:10] > debut_12m
        if recent and s.get("montant_estime") and s["statut"] != "REFUSE":
            charge_12m[s["_branche"]] += s["montant_estime"]
    sp = {
        b: round(100 * charge_12m[b] / encaisse_branche_12m[b], 1) if encaisse_branche_12m[b] else None
        for b in BRANCHES
        if b in encaisse_branche_12m
    }
    primes_branche: dict[str, dict[str, float]] = defaultdict(lambda: dict.fromkeys(SOURCES, 0.0))
    for c in contrats:
        if c["statut"] == "ACTIF" and c.get("prime_annuelle"):
            primes_branche[c["branche"]][c["source"]] += c["prime_annuelle"]
    tentatives_ap = sum(statuts_ap.values())
    total = sum(total_12m.values())
    return {
        "kpi": {
            "encaisse_12_mois": round(total, 2),
            "encaisse_12_mois_source": {s: round(v, 2) for s, v in total_12m.items()},
            "charge_sinistres_12_mois": round(sum(charge_12m.values()), 2),
            "ratio_sp_12_mois": round(100 * sum(charge_12m.values()) / total, 1) if total else None,
            "taux_echec_paiement_ap": _pct(statuts_ap.get("ECHOUE", 0), tentatives_ap, 2),
            "primes_annualisees": round(sum(sum(v.values()) for v in primes_branche.values()), 2),
        },
        "encaissements_mensuels": {**_series(mois, encaisse, digits=2), "libelles": mois},
        "par_mode": _series(
            [m for m in ("PRELEVEMENT", "CARTE", "CHEQUE", "VIREMENT", "PORTEFEUILLE", "AUTRE") if m in modes], modes
        ),
        "ratio_sp_branche": {
            "categories": list(sp),
            "libelles": [libelle(b) for b in sp],
            "valeurs": list(sp.values()),
        },
        "primes_branche": _series([b for b in BRANCHES if b in primes_branche], primes_branche, digits=2),
        "statuts_paiement_ap": {
            "categories": list(statuts_ap),
            "libelles": [libelle(s) for s in statuts_ap],
            "valeurs": list(statuts_ap.values()),
        },
    }


# ---------------------------------------------------------------------------- doublons
def _doublons(dedup):
    paires = dedup.paires
    matrice = {niv: {a: {b: 0 for b in SOURCES} for a in SOURCES} for niv in ("CERTAIN", "PROBABLE")}
    for p in paires:
        a, b = p["type_paire"].split("-")
        matrice[p["niveau"]][a][b] += 1
        if a != b:
            matrice[p["niveau"]][b][a] += 1
    from fusion.processing.dedup import CRITERES

    cooc = [[0] * len(CRITERES) for _ in CRITERES]
    for p in paires:
        ok = [p[f"c_{k}"] >= 0.7 for k in CRITERES]
        for i in range(len(CRITERES)):
            for j in range(len(CRITERES)):
                cooc[i][j] += ok[i] and ok[j]
    hist = Counter(min(int(p["score"] * 20), 19) for p in paires)
    tailles = Counter(min(p["nb_comptes"], 4) for p in dedup.personnes if p["nb_comptes"] > 1)
    s = dedup.stats
    return {
        "kpi": {
            "comptes": s["comptes"],
            "personnes": s["personnes"],
            "comptes_fusionnes": s["comptes"] - s["personnes"],
            "taux_doublons": _pct(s["comptes"] - s["personnes"], s["comptes"]),
            "paires_certaines": s["paires_certaines"],
            "paires_probables": s["paires_probables"],
            "paires_comparees": s["paires_comparees"],
            "comparaisons_evitees": s["comparaisons_evitees"],
            "blocs_ignores": s["blocs_ignores_trop_grands"],
            "evaluation": s["evaluation"],
        },
        "matrice_sources": matrice,
        "matrice_criteres": {"criteres": ["E-mail", "Téléphone", "Nom", "Naissance", "Adresse"], "valeurs": cooc},
        "histogramme_scores": {
            "categories": [f"{i / 20:.2f}" for i in range(9, 20)],
            "valeurs": [hist.get(i, 0) for i in range(9, 20)],
            "seuils": {"probable": 0.45, "certain": 0.70},
        },
        "taille_grappes": {
            "categories": ["2", "3", "4+"],
            "valeurs": [tailles.get(2, 0), tailles.get(3, 0), tailles.get(4, 0)],
        },
    }


# ---------------------------------------------------------------------------- qualité
def _qualite(log, tables, reconciliation, run):
    by = log.counts_by("code", "source")
    regles = []
    for code, r in REGLES.items():
        n = {s: by.get((code, s), 0) for s in SOURCES}
        regles.append(
            {
                "code": code,
                "libelle": r.libelle,
                "gravite": r.gravite,
                "action": r.action,
                **n,
                "total": n["AB"] + n["AP"],
            }
        )
    gravite = Counter()
    for r in regles:
        gravite[r["gravite"]] += r["total"]
    return {
        "regles": regles,
        "par_gravite": {"categories": GRAVITES, "valeurs": [gravite.get(g, 0) for g in GRAVITES]},
        "volumes_silver": {e: len(rows) for e, rows in tables.items()},
        "orphelins": {
            e: sum(1 for r in tables[e] if r.get("est_orphelin")) for e in ("contrat", "sinistre", "paiement")
        },
        "reconciliation": reconciliation,
        "execution": run,
    }


# ---------------------------------------------------------------------------- synthèse
def _synthese(doc):
    c, ct, s, f, d = doc["clients"], doc["contrats"], doc["sinistres"], doc["finances"], doc["doublons"]
    return {
        "personnes": c["kpi"]["personnes"],
        "comptes": c["kpi"]["comptes"],
        "clients_communs": c["kpi"]["clients_communs"],
        "contrats_actifs": ct["kpi"]["actifs"],
        "primes_annualisees": ct["kpi"]["primes_annualisees"],
        "encaisse_12_mois": f["kpi"]["encaisse_12_mois"],
        "ratio_sp_12_mois": f["kpi"]["ratio_sp_12_mois"],
        "sinistres_ouverts": s["kpi"]["ouverts"],
        "taux_doublons": d["kpi"]["taux_doublons"],
        "anomalies_critiques": doc["qualite"]["par_gravite"]["valeurs"][0],
        "portefeuille": c["portefeuille"],
        "branches": ct["par_branche"],
        "encaissements": f["encaissements_mensuels"],
    }
