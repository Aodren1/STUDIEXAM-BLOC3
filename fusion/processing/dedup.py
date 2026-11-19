"""Étape 3 — rôle Spark : rapprochement des clients (entity resolution) et golden record.

1. Blocage : on ne compare que les comptes qui partagent une clé (e-mail, téléphone, nom + date de
   naissance, nom + code postal). En réel, 17 M de comptes donneraient 1,4 × 10¹⁴ paires : le blocage
   ramène le problème à quelques millions de comparaisons.
2. Score pondéré : somme des concordances par critère (e-mail, téléphone, nom, naissance, adresse).
3. Classement : score ≥ 0,70 « certain » (fusion automatique) ; 0,45 à 0,70 « probable » (file de
   validation métier, jamais fusionné automatiquement) ; en dessous, écarté.
4. Fusion des paires certaines par union-find → identifiant de PERSONNE (golden record), attributs
   choisis par règles de survie (survivorship) documentées.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from fusion.cleaning import name_key

# La somme des poids vaut 1 (vérifié par test) : un score se lit directement comme une probabilité de concordance.
# Les poids hérités du Bloc 2 sommaient à 1,05, ce qui décalait de fait les seuils (défaut D-02 du dossier).
POIDS = {"email": 0.30, "telephone": 0.25, "nom": 0.20, "naissance": 0.15, "adresse": 0.10}
CRITERES = list(POIDS)
SEUIL_CERTAIN = 0.70
SEUIL_PROBABLE = 0.45
MAX_BLOC = 50  # un bloc plus grand (homonymes très fréquents) serait un produit cartésien : ignoré et compté


# ------------------------------------------------------------------ similarités
def jaro_winkler(a: str, b: str) -> float:
    """Similarité de Jaro-Winkler (0 à 1), adaptée aux coquilles de saisie sur les noms propres."""
    if a == b:
        return 1.0 if a else 0.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    window = max(la, lb) // 2 - 1
    ma, mb = [False] * la, [False] * lb
    matches = 0
    for i, ch in enumerate(a):
        for j in range(max(0, i - window), min(lb, i + window + 1)):
            if not mb[j] and b[j] == ch:
                ma[i] = mb[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    sa = [c for c, m in zip(a, ma, strict=True) if m]
    sb = [c for c, m in zip(b, mb, strict=True) if m]
    transpositions = sum(x != y for x, y in zip(sa, sb, strict=True)) / 2
    jaro = (matches / la + matches / lb + (matches - transpositions) / matches) / 3
    prefix = 0
    for x, y in zip(a[:4], b[:4], strict=False):
        if x != y:
            break
        prefix += 1
    return jaro + prefix * 0.1 * (1 - jaro)


def token_similarity(a: str, b: str) -> float:
    """Indice de Jaccard sur les mots (adresses)."""
    ta, tb = set(a.split()), set(b.split())
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def compare(x: dict[str, Any], y: dict[str, Any]) -> dict[str, float]:
    """Concordance de 0 à 1 pour chacun des cinq critères."""
    kx, ky = x["nom_cle"], y["nom_cle"]
    if kx and kx == ky:
        nom = 1.0
    elif kx and ky and jaro_winkler(kx, ky) >= 0.92:
        nom = 0.7
    else:
        nom = 0.0
    adresse = 0.0
    if x.get("code_postal") and x["code_postal"] == y.get("code_postal"):
        adresse = 1.0 if token_similarity(x.get("adresse") or "", y.get("adresse") or "") >= 0.8 else 0.3
    return {
        "email": float(bool(x.get("email")) and x.get("email") == y.get("email")),
        "telephone": float(bool(x.get("telephone")) and x.get("telephone") == y.get("telephone")),
        "nom": nom,
        "naissance": float(bool(x.get("date_naissance")) and x.get("date_naissance") == y.get("date_naissance")),
        "adresse": adresse,
    }


def score(crit: dict[str, float]) -> float:
    return round(sum(POIDS[k] * v for k, v in crit.items()), 3)


# ------------------------------------------------------------------ union-find
class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        root = x
        while self.parent.get(root, root) != root:
            root = self.parent[root]
        while self.parent.get(x, x) != root:  # compression de chemin
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # racine = plus petit identifiant : AB avant AP, AbAssurance (acquéreur) est le référentiel maître
            keep, drop = sorted((ra, rb))
            self.parent[drop] = keep


# ------------------------------------------------------------------ exécution
@dataclass
class DedupResult:
    paires: list[dict[str, Any]]
    personnes: list[dict[str, Any]]
    personne_de: dict[str, str]
    stats: dict[str, Any] = field(default_factory=dict)


def blocking_keys(c: dict[str, Any]) -> list[str]:
    keys = []
    if c.get("email"):
        keys.append("e:" + c["email"])
    if c.get("telephone"):
        keys.append("t:" + c["telephone"])
    if c["nom_cle"] and c.get("date_naissance"):
        keys.append(f"n:{c['nom_cle']}|{c['date_naissance']}")
    if c["nom_cle"] and c.get("code_postal"):
        keys.append(f"a:{c['nom_cle']}|{c['code_postal']}")
    return keys


def run_dedup(clients: list[dict[str, Any]], truth: list[tuple[str, str]] | None = None) -> DedupResult:
    by_uid = {}
    for c in clients:
        by_uid[c["client_uid"]] = {**c, "nom_cle": name_key(f"{c.get('prenom') or ''} {c.get('nom') or ''}")}

    blocks: dict[str, list[str]] = defaultdict(list)
    for uid, c in by_uid.items():
        for k in blocking_keys(c):
            blocks[k].append(uid)
    candidates: set[tuple[str, str]] = set()
    oversized = 0
    for members in blocks.values():
        if len(members) > MAX_BLOC:
            oversized += 1
            continue
        candidates.update(tuple(sorted(p)) for p in combinations(members, 2))

    uf = UnionFind()
    paires = []
    for a, b in sorted(candidates):
        crit = compare(by_uid[a], by_uid[b])
        s = score(crit)
        if s < SEUIL_PROBABLE:
            continue
        niveau = "CERTAIN" if s >= SEUIL_CERTAIN else "PROBABLE"
        paires.append(
            {
                "uid_a": a,
                "uid_b": b,
                "type_paire": f"{a[:2]}-{b[:2]}",
                "score": s,
                "niveau": niveau,
                **{f"c_{k}": v for k, v in crit.items()},
            }
        )
        if niveau == "CERTAIN":
            uf.union(a, b)

    personne_de = {uid: "P-" + uf.find(uid) for uid in by_uid}
    personnes = build_golden_records(by_uid, personne_de)
    n = len(by_uid)
    stats = {
        "comptes": n,
        "blocs": len(blocks),
        "blocs_ignores_trop_grands": oversized,
        "paires_comparees": len(candidates),
        "comparaisons_evitees": n * (n - 1) // 2 - len(candidates),
        "paires_certaines": sum(p["niveau"] == "CERTAIN" for p in paires),
        "paires_probables": sum(p["niveau"] == "PROBABLE" for p in paires),
        "personnes": len(personnes),
        "evaluation": evaluate(paires, truth) if truth is not None else None,
    }
    return DedupResult(paires, personnes, personne_de, stats)


def _most_recent(members: list[dict[str, Any]], attr: str) -> Any:
    valued = [m for m in members if m.get(attr)]
    return max(valued, key=lambda m: m.get("date_creation") or "")[attr] if valued else None


def build_golden_records(by_uid: dict[str, dict[str, Any]], personne_de: dict[str, str]) -> list[dict[str, Any]]:
    """Règles de survie : identité issue d'AbAssurance (référentiel maître) ; coordonnées les plus récentes ;
    date de naissance la plus fréquente parmi les comptes."""
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for uid, pid in personne_de.items():
        clusters[pid].append(by_uid[uid])
    out = []
    for pid, members in sorted(clusters.items()):
        ab = [m for m in members if m["source"] == "AB"]
        ident = max(ab or members, key=lambda m: m.get("date_creation") or "")
        births = Counter(m["date_naissance"] for m in members if m.get("date_naissance"))
        sources = sorted({m["source"] for m in members})
        out.append(
            {
                "personne_id": pid,
                "nom": ident.get("nom"),
                "prenom": ident.get("prenom"),
                "date_naissance": births.most_common(1)[0][0] if births else None,
                "email": _most_recent(members, "email"),
                "telephone": _most_recent(members, "telephone"),
                "code_postal": _most_recent(members, "code_postal"),
                "pays": _most_recent(members, "pays"),
                "region": _most_recent(members, "region"),
                "nb_comptes": len(members),
                "sources": "+".join(sources),
            }
        )
    return out


def evaluate(paires: list[dict[str, Any]], truth: list[tuple[str, str]]) -> dict[str, Any]:
    """Précision et rappel des paires détectées par rapport à la vérité terrain (clusters transitifs)."""
    uf = UnionFind()
    for a, b in truth:
        uf.union(a, b)
    clusters: dict[str, list[str]] = defaultdict(list)
    for uid in {u for pair in truth for u in pair}:
        clusters[uf.find(uid)].append(uid)
    real = sum(len(m) * (len(m) - 1) // 2 for m in clusters.values())
    known = {u for pair in truth for u in pair}
    res = {}
    for label, levels in (("certain", {"CERTAIN"}), ("certain_probable", {"CERTAIN", "PROBABLE"})):
        found = [p for p in paires if p["niveau"] in levels]
        tp = sum(
            1
            for p in found
            if p["uid_a"] in known and p["uid_b"] in known and uf.find(p["uid_a"]) == uf.find(p["uid_b"])
        )
        res[label] = {
            "paires_detectees": len(found),
            "vrais_positifs": tp,
            "paires_reelles": real,
            "precision": round(tp / len(found), 4) if found else None,
            "rappel": round(tp / real, 4) if real else None,
        }
    return res
