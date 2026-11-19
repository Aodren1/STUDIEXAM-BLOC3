"""Génère les deux bases sources simulées, fidèles aux schémas de l'annexe.

    abassurance_oracle.sqlite   : AB_CLIENT, AB_CONTRAT, AB_SINISTRE, AB_PAIEMENT
    assureplus_sqlserver.sqlite : AP_USERS, AP_CONTRACTS, AP_CLAIMS, AP_PAYMENTS
    verite_doublons.json        : paires de doublons réelles (vérité terrain pour mesurer précision et rappel)

Volumétrie au 1/2 000e du réel (12 M et 5 M de clients → 6 000 et 2 500), ratio conservé.
Les défauts constatés lors du profilage des vraies bases sont injectés volontairement :
dates texte dans plusieurs formats, noms complets inversés, casse des statuts, primes mensuelles,
doublons intra et inter-bases, orphelins, téléphones et e-mails mal saisis, paiements tardifs.
Le générateur est déterministe (graine fixe) : deux exécutions produisent les mêmes octets.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

AB_DB = "abassurance_oracle.sqlite"
AP_DB = "assureplus_sqlserver.sqlite"
TRUTH_FILE = "verite_doublons.json"

BASE_AB = 6000
BASE_AP = 2500
SHARED_RATE = 0.14  # part des clients AP déjà clients AB (recouvrement des portefeuilles)
DUP_AB_RATE = 0.02  # doublons internes AB (ressaisie en agence)
DUP_AP_RATE = 0.015  # doublons internes AP (double inscription dans l'application mobile)
PAY_FROM = date(2025, 1, 1)  # historique de paiements chargé

PRENOMS = [
    "Marie", "Jean", "Pierre", "Sophie", "Camille", "Lucas", "Léa", "Hugo", "Chloé", "Louis", "Emma", "Gabriel",
    "Manon", "Arthur", "Inès", "Jules", "Sarah", "Thomas", "Julie", "Nicolas", "Laura", "Antoine", "Céline",
    "Maxime", "Pauline", "Alexandre", "Élodie", "Julien", "Mathilde", "François", "Isabelle", "Philippe",
    "Nathalie", "Stéphane", "Valérie", "Olivier", "Sandrine", "Christophe", "Aurélie", "Mickaël", "Hélène",
    "Benoît", "Anaïs", "Raphaël", "Zoé", "Noah", "Jade", "Adam", "Lina", "Yanis", "Fatima", "Karim", "Amélie",
    "Bastien", "Clément", "Lucie", "Romain", "Margaux", "Théo", "Sven", "Lotte", "Pieter", "Anke", "Joël",
    "Gaëlle", "Hervé", "Brigitte", "Jean-Pierre", "Marie-Claire", "Anne-Sophie",
]
NOMS = [
    "Martin", "Bernard", "Dubois", "Thomas", "Robert", "Richard", "Petit", "Durand", "Leroy", "Moreau", "Simon",
    "Laurent", "Lefèvre", "Michel", "Garcia", "David", "Bertrand", "Roux", "Vincent", "Fournier", "Morel",
    "Girard", "André", "Lefebvre", "Mercier", "Dupont", "Lambert", "Bonnet", "François", "Martinez", "Legrand",
    "Garnier", "Faure", "Rousseau", "Blanc", "Guérin", "Muller", "Henry", "Roussel", "Nicolas", "Perrin",
    "Morin", "Mathieu", "Clément", "Gauthier", "Dumont", "Lopez", "Fontaine", "Chevalier", "Robin", "Masson",
    "Sanchez", "Gérard", "Nguyen", "Boyer", "Denis", "Lemaire", "Duval", "Joly", "Gautier", "Roger", "Roche",
    "Roy", "Noël", "Meyer", "Lucas", "Meunier", "Pérez", "Marchand", "Dufour", "Blanchard", "Le Gall",
    "Kerboul", "Janssens", "Peeters", "Maes", "De Vries", "Van Dijk", "Schmit", "Weber", "Hoffmann", "Benali",
    "Haddad", "Da Silva",
]
# (code postal, ville, poids)
LIEUX = [
    ("75011", "Paris", 30), ("75015", "Paris", 25), ("92100", "Boulogne-Billancourt", 8), ("93200", "Saint-Denis", 7),
    ("94000", "Créteil", 6), ("78000", "Versailles", 6), ("91000", "Évry", 5), ("95000", "Cergy", 5),
    ("77000", "Melun", 4), ("13001", "Marseille", 16), ("06000", "Nice", 8), ("83000", "Toulon", 6),
    ("84000", "Avignon", 5), ("69003", "Lyon", 15), ("38000", "Grenoble", 6), ("42000", "Saint-Étienne", 5),
    ("63000", "Clermont-Ferrand", 5), ("74000", "Annecy", 4), ("31000", "Toulouse", 12), ("34000", "Montpellier", 9),
    ("30000", "Nîmes", 5), ("66000", "Perpignan", 4), ("33000", "Bordeaux", 11), ("87000", "Limoges", 4),
    ("64000", "Pau", 4), ("17000", "La Rochelle", 3), ("44000", "Nantes", 10), ("49000", "Angers", 5),
    ("72000", "Le Mans", 4), ("85000", "La Roche-sur-Yon", 3), ("35000", "Rennes", 8), ("29200", "Brest", 5),
    ("56000", "Vannes", 3), ("59000", "Lille", 11), ("80000", "Amiens", 4), ("62000", "Arras", 3),
    ("67000", "Strasbourg", 9), ("57000", "Metz", 5), ("51100", "Reims", 5), ("54000", "Nancy", 4),
    ("76000", "Rouen", 6), ("14000", "Caen", 5), ("21000", "Dijon", 5), ("25000", "Besançon", 4),
    ("37000", "Tours", 5), ("45000", "Orléans", 5), ("20000", "Ajaccio", 2), ("97400", "Saint-Denis", 2),
    ("1000", "Bruxelles", 9), ("4000", "Liège", 4), ("5000", "Namur", 2), ("9000", "Gand", 3),
    ("L-1009", "Luxembourg", 4), ("L-4002", "Esch-sur-Alzette", 1), ("1012 AB", "Amsterdam", 3),
    ("3011 AA", "Rotterdam", 2),
]
VOIES = [
    "rue de la République", "avenue Jean Jaurès", "boulevard Victor Hugo", "rue Pasteur", "rue du Moulin",
    "place de la Mairie", "rue des Lilas", "avenue de la Gare", "chemin des Vignes", "rue Nationale",
    "allée des Tilleuls", "rue Gambetta", "impasse des Roses", "quai des Chartrons", "rue Saint-Michel", "avenue Foch",
]
ABREV = {"rue": "R", "avenue": "AV", "boulevard": "BD", "place": "PL", "chemin": "CH", "impasse": "IMP"}
DOMAINES = ["gmail.com", "orange.fr", "free.fr", "hotmail.fr", "yahoo.fr", "outlook.com", "laposte.net", "sfr.fr",
            "icloud.com", "proton.me", "skynet.be", "pt.lu"]

# branche AB → (poids, prime annuelle médiane, sigma, fréquence de sinistre annuelle, coût médian)
BRANCHES_AB = {
    "SANTE": (34, 980.0, 0.35, 0.55, 260.0),
    "HABITATION": (28, 310.0, 0.40, 0.07, 1900.0),
    "AUTO": (26, 640.0, 0.45, 0.09, 2600.0),
    "PRO": (12, 2400.0, 0.70, 0.08, 7200.0),
}
# produit AP → (poids, prime mensuelle médiane, sigma, fréquence, coût médian)
PRODUITS_AP = {
    "AUTO_TOUS_RISQUES": (38, 68.0, 0.35, 0.11, 2900.0),
    "AUTO_TIERS": (24, 34.0, 0.30, 0.07, 2100.0),
    "AUTO_FLEX_KM": (12, 29.0, 0.30, 0.06, 2300.0),
    "ASSIST_PREMIUM": (16, 14.0, 0.25, 0.20, 420.0),
    "MRH_ESSENTIEL": (10, 22.0, 0.30, 0.06, 1500.0),
}
DESCRIPTIONS = {
    "SANTE": ["Hospitalisation programmée", "Soins dentaires", "Optique", "Consultation spécialiste"],
    "HABITATION": ["Dégât des eaux", "Bris de glace", "Cambriolage", "Incendie cuisine", "Tempête toiture"],
    "AUTO": ["Accrochage parking", "Bris de pare-brise", "Collision arrière", "Vol du véhicule"],
    "PRO": ["Perte d'exploitation", "Dégât des eaux local", "Responsabilité civile client", "Vol matériel"],
}
COMMENTS_AP = ["Rear-end collision", "Windshield chip", "Breakdown on highway", "Towing requested",
               "Water leak", "Parking scratch", "Theft attempt", "Assistance abroad"]


@dataclass
class Personne:
    prenom: str
    nom: str
    naissance: date
    email: str
    telephone: str  # forme nationale 0XXXXXXXXX (ou LU sans 0)
    numero: int
    voie: str
    cp: str
    ville: str
    pays: str


class Generator:
    def __init__(self, scale: float = 1.0, seed: int = 20261119, ref_date: date = date(2026, 10, 31)):
        self.rng = random.Random(seed)
        self.scale = max(0.05, scale)
        self.ref = ref_date
        self.n_ab = round(BASE_AB * self.scale)
        self.n_ap = round(BASE_AP * self.scale)
        self.truth: list[tuple[str, str]] = []
        self.lieux_total = sum(w for *_, w in LIEUX)

    # ------------------------------------------------------------------ tirages
    def chance(self, p: float) -> bool:
        return self.rng.random() < p

    def weighted(self, weights: dict[str, float]) -> str:
        return self.rng.choices(list(weights), weights=list(weights.values()))[0]

    def lognormal(self, median: float, sigma: float, lo: float, hi: float) -> float:
        return round(min(hi, max(lo, median * math.exp(self.rng.gauss(0, sigma)))), 2)

    def rand_date(self, start: date, end: date) -> date:
        span = max(0, (end - start).days)
        return start + timedelta(days=self.rng.randint(0, span))

    def rand_ts(self, start: date, end: date) -> datetime:
        d = self.rand_date(start, end)
        return datetime(d.year, d.month, d.day, self.rng.randint(7, 21), self.rng.randint(0, 59), self.rng.randint(0, 59))

    # ------------------------------------------------------------------ personnes
    def new_person(self) -> Personne:
        prenom, nom = self.rng.choice(PRENOMS), self.rng.choice(NOMS)
        cp, ville, _ = self.rng.choices(LIEUX, weights=[w for *_, w in LIEUX])[0]
        pays = "LU" if cp.startswith("L-") else "NL" if " " in cp else "BE" if len(cp) == 4 else "FR"
        age = min(92, max(18, int(self.rng.gauss(47, 16))))
        naissance = self.ref - timedelta(days=round(age * 365.25) + self.rng.randint(1, 360))  # âge révolu ≥ 18
        local = f"{_ascii(prenom)}.{_ascii(nom)}".replace(" ", "")
        if self.chance(0.3):
            local += str(self.rng.randint(1, 99))
        if pays == "LU":
            tel = str(self.rng.choice([6, 2])) + "".join(str(self.rng.randint(0, 9)) for _ in range(7))
        elif pays == "BE":
            tel = "04" + str(self.rng.randint(70, 99)) + "".join(str(self.rng.randint(0, 9)) for _ in range(6))
        else:
            tel = "0" + self.rng.choice("1234567") + "".join(str(self.rng.randint(0, 9)) for _ in range(8))
        return Personne(prenom, nom, naissance, f"{local}@{self.rng.choice(DOMAINES)}", tel, self.rng.randint(1, 180),
                        self.rng.choice(VOIES), cp, ville, pays)

    def variant(self, p: Personne) -> Personne:
        """Même personne ressaisie : coquille possible, contact parfois changé, déménagement rare."""
        q = Personne(**p.__dict__)
        if self.chance(0.25) and len(q.nom) > 4:  # coquille de saisie
            i = self.rng.randint(1, len(q.nom) - 2)
            q.nom = q.nom[:i] + q.nom[i + 1] + q.nom[i] + q.nom[i + 2:]
        if self.chance(0.3):
            q.email = q.email.replace("@", f"{self.rng.randint(1, 9)}@")
        if self.chance(0.25):
            q.telephone = q.telephone[:-2] + f"{self.rng.randint(0, 99):02d}"
        if self.chance(0.1):
            q.numero = self.rng.randint(1, 180)
        return q

    # ------------------------------------------------------------------ mise en forme « source » (défauts)
    def fmt_phone(self, tel: str, pays: str) -> str:
        style = self.rng.random()
        if pays == "LU":
            return f"+352 {tel}" if style < 0.6 else tel
        if style < 0.35:
            return " ".join(tel[i:i + 2] for i in range(0, 10, 2))
        if style < 0.55:
            return tel
        if style < 0.70:
            return ".".join(tel[i:i + 2] for i in range(0, 10, 2))
        code = "32" if pays == "BE" else "31" if pays == "NL" else "33"
        if style < 0.85:
            return f"+{code} {tel[1]} {tel[2:4]} {tel[4:6]} {tel[6:8]} {tel[8:10]}"
        if style < 0.92:
            return f"00{code}{tel[1:]}"
        if style < 0.96:
            return f"+{code} (0){tel[1:]}"
        return self.rng.choice(["", "NC", "0000", "06 12 34"])  # non normalisable

    def fmt_email(self, email: str) -> str | None:
        r = self.rng.random()
        if r < 0.03:
            return None
        if r < 0.05:
            return email.replace("@", " at ")
        if r < 0.10:
            return email.upper()
        if r < 0.13:
            return f"  {email} "
        return email

    def fmt_address(self, p: Personne, abbreviate: bool) -> str:
        voie = p.voie
        if abbreviate:
            first, _, rest = voie.partition(" ")
            voie = f"{ABREV.get(first, first)} {rest}".upper()
        return f"{p.numero} {voie}"

    # ------------------------------------------------------------------ génération
    def run(self, out_dir: Path) -> dict:
        out_dir.mkdir(parents=True, exist_ok=True)
        persons_ab = self._clients_ab()
        persons_ap = self._clients_ap(persons_ab)
        contrats_ab = self._contrats_ab(persons_ab)
        contrats_ap = self._contrats_ap(persons_ap)
        sinistres_ab = self._sinistres(contrats_ab, "AB")
        sinistres_ap = self._sinistres(contrats_ap, "AP")
        paiements_ab = self._paiements_ab(contrats_ab)
        paiements_ap = self._paiements_ap(contrats_ap)

        _write_db(out_dir / AB_DB, "oracle", {
            "AB_CLIENT": [r for r, _ in persons_ab], "AB_CONTRAT": [r for r, _ in contrats_ab],
            "AB_SINISTRE": sinistres_ab, "AB_PAIEMENT": paiements_ab,
        })
        _write_db(out_dir / AP_DB, "sqlserver", {
            "AP_USERS": [r for r, _ in persons_ap], "AP_CONTRACTS": [r for r, _ in contrats_ap],
            "AP_CLAIMS": sinistres_ap, "AP_PAYMENTS": paiements_ap,
        })
        (out_dir / TRUTH_FILE).write_text(json.dumps({"paires": self.truth}, indent=0), encoding="utf-8")
        return {
            "AB_CLIENT": len(persons_ab), "AB_CONTRAT": len(contrats_ab), "AB_SINISTRE": len(sinistres_ab),
            "AB_PAIEMENT": len(paiements_ab), "AP_USERS": len(persons_ap), "AP_CONTRACTS": len(contrats_ap),
            "AP_CLAIMS": len(sinistres_ap), "AP_PAYMENTS": len(paiements_ap), "paires_doublons": len(self.truth),
        }

    def _clients_ab(self) -> list[tuple[dict, Personne]]:
        rows: list[tuple[dict, Personne]] = []
        next_id = 100001
        while len(rows) < self.n_ab:
            dup_of = rows[self.rng.randrange(len(rows))] if rows and self.chance(DUP_AB_RATE) else None
            p = self.variant(dup_of[1]) if dup_of else self.new_person()
            created = self.rand_ts(date(1995, 1, 1), self.ref - timedelta(days=20))
            nom = p.nom.upper() if self.chance(0.8) else p.nom
            row = {
                "AB_CLIENT_ID": next_id,
                "AB_NOM": nom if self.chance(0.97) else f" {nom} ",
                "AB_PRENOM": p.prenom if self.chance(0.95) else p.prenom.lower(),
                "AB_DATE_NAISSANCE": p.naissance.isoformat(),
                "AB_EMAIL": self.fmt_email(p.email),
                "AB_TELEPHONE": self.fmt_phone(p.telephone, p.pays),
                "AB_ADRESSE": self.fmt_address(p, abbreviate=self.chance(0.4)),
                "AB_CODE_POSTAL": p.cp if self.chance(0.98) else p.cp.lower(),
                "AB_NUM_FISCAL": None if (p.pays == "FR" and self.chance(0.05)) or p.pays != "FR"
                else str(self.rng.randint(1, 3)) + "".join(str(self.rng.randint(0, 9)) for _ in range(12)),
                "AB_DATE_CREATION": created.strftime("%Y-%m-%d %H:%M:%S.%f"),
                "AB_STATUT_CLIENT": self.weighted({"ACTIF": 74, "INACTIF": 9, "RESILIE": 13, "PROSPECT": 4}),
            }
            if dup_of:
                self.truth.append((f"AB-{dup_of[0]['AB_CLIENT_ID']}", f"AB-{next_id}"))
            rows.append((row, p))
            next_id += 1
        return rows

    def _clients_ap(self, persons_ab: list[tuple[dict, Personne]]) -> list[tuple[dict, Personne]]:
        rows: list[tuple[dict, Personne]] = []
        next_id = 5001
        while len(rows) < self.n_ap:
            source_uid = None
            if rows and self.chance(DUP_AP_RATE):
                twin = rows[self.rng.randrange(len(rows))]
                p, source_uid = self.variant(twin[1]), f"AP-{twin[0]['AP_USER_ID']}"
            elif self.chance(SHARED_RATE):
                twin = persons_ab[self.rng.randrange(len(persons_ab))]
                p, source_uid = self.variant(twin[1]), f"AB-{twin[0]['AB_CLIENT_ID']}"
            else:
                p = self.new_person()
            style = self.rng.random()
            if style < 0.55:
                full = f"{p.prenom} {p.nom.upper()}"
            elif style < 0.80:
                full = f"{p.nom.upper()} {p.prenom}"
            elif style < 0.95:
                full = f"{p.prenom} {p.nom}"
            else:
                full = f"{p.prenom} {p.nom}".lower()
            r = self.rng.random()
            if r < 0.86:
                birth = p.naissance.isoformat()
            elif r < 0.93:
                birth = p.naissance.strftime("%d/%m/%Y")
            elif r < 0.96:
                birth = "1900-01-01"
            elif r < 0.98:
                birth = ""
            else:
                birth = p.naissance.strftime("%Y%d%m")[:10] if p.naissance.day > 12 else "31/02/1985"
            status = self.weighted({"active": 78, "inactive": 7, "churned": 11, "lead": 4})
            row = {
                "AP_USER_ID": next_id,
                "AP_FULL_NAME": full,
                "AP_BIRTH_DATE": birth,
                "AP_MAIL_ADDRESS": self.fmt_email(p.email),
                "AP_PHONE_NUMBER": self.fmt_phone(p.telephone, p.pays),
                "AP_STREET_ADDRESS": self.fmt_address(p, abbreviate=self.chance(0.15)),
                "AP_ZIP_CODE": p.cp,
                "AP_CREATED_AT": self.rand_ts(date(2009, 1, 1), self.ref - timedelta(days=10)).strftime(
                    "%Y-%m-%d %H:%M:%S"),
                "AP_CUSTOMER_STATUS": status if self.chance(0.9) else status.capitalize(),
                "AP_LOYALTY_SCORE": None if self.chance(0.04) else self.rng.randint(0, 100),
            }
            if source_uid:
                self.truth.append((source_uid, f"AP-{next_id}"))
            rows.append((row, p))
            next_id += 1
        return rows

    def _contract_dates(self, client_since: str, statut: str) -> tuple[date, date | None]:
        since = max(date.fromisoformat(client_since[:10]), date(2005, 1, 1))
        debut = self.rand_date(since, self.ref - timedelta(days=15))
        if statut in ("RESILIE", "cancelled"):
            fin = self.rand_date(min(debut + timedelta(days=200), self.ref), self.ref)
        else:
            # échéance annuelle tacitement reconduite : prochaine date anniversaire
            years = max(1, self.ref.year - debut.year + (1 if (self.ref.month, self.ref.day) >= (debut.month, debut.day) else 0))
            fin = _add_years(debut, years)
        return debut, fin

    def _contrats_ab(self, persons: list[tuple[dict, Personne]]) -> list[tuple[dict, str]]:
        rows: list[tuple[dict, str]] = []
        seq = 1
        for client, _ in persons:
            if client["AB_STATUT_CLIENT"] == "PROSPECT":
                continue
            for _ in range(self.rng.choices([1, 2, 3, 4], weights=[52, 30, 13, 5])[0]):
                branche = self.weighted({k: v[0] for k, v in BRANCHES_AB.items()})
                _, med, sigma, *_ = BRANCHES_AB[branche]
                if client["AB_STATUT_CLIENT"] == "RESILIE":
                    statut = "RESILIE"
                else:
                    statut = self.weighted({"ACTIF": 82, "RESILIE": 12, "SUSPENDU": 3, "EN_ATTENTE": 3})
                debut, fin = self._contract_dates(client["AB_DATE_CREATION"], statut)
                prime = self.lognormal(med, sigma, 60, 40000)
                if self.chance(0.004):
                    prime = self.rng.choice([0.0, -prime])
                client_id = client["AB_CLIENT_ID"] if not self.chance(0.003) else 999000 + seq  # orphelin
                rows.append(({
                    "AB_POLICY_NUMBER": f"AB-POL-{seq:07d}", "AB_CLIENT_ID": client_id, "AB_TYPE_ASSURANCE": branche,
                    "AB_DATE_DEBUT": debut.isoformat(), "AB_DATE_FIN": fin.isoformat() if fin else None,
                    "AB_PRIME_ANNUELLE": prime, "AB_STATUT_CONTRAT": statut,
                    "AB_AGENCE_ID": self.rng.randint(1, 48),
                }, branche))
                seq += 1
        return rows

    def _contrats_ap(self, persons: list[tuple[dict, Personne]]) -> list[tuple[dict, str]]:
        rows: list[tuple[dict, str]] = []
        seq = 1
        brokers = [f"BRK-{n:03d}" for n in range(1, 31)]
        for user, _ in persons:
            if user["AP_CUSTOMER_STATUS"].lower() == "lead":
                continue
            for _ in range(self.rng.choices([1, 2, 3], weights=[66, 27, 7])[0]):
                produit = self.weighted({k: v[0] for k, v in PRODUITS_AP.items()})
                _, med, sigma, *_ = PRODUITS_AP[produit]
                state = "cancelled" if user["AP_CUSTOMER_STATUS"].lower() == "churned" else self.weighted(
                    {"active": 84, "cancelled": 10, "suspended": 3, "pending": 3})
                debut, fin = self._contract_dates(user["AP_CREATED_AT"], state)
                state_txt = state.capitalize() if self.chance(0.7) else state.upper() if self.chance(0.5) else state
                user_id = user["AP_USER_ID"] if not self.chance(0.003) else 90000 + seq
                rows.append(({
                    "AP_CONTRACT_REF": f"APC{seq:08d}", "AP_USER_ID": user_id, "AP_PRODUCT_CODE": produit,
                    "AP_START_DATE": f"{debut.isoformat()} 00:00:00",
                    "AP_END_DATE": f"{fin.isoformat()} 00:00:00" if fin else None,
                    "AP_MONTHLY_PREMIUM": self.lognormal(med, sigma, 5, 900) if not self.chance(0.004) else 0.0,
                    "AP_CONTRACT_STATE": state_txt,
                    "AP_BROKER_CODE": self.rng.choice(brokers) if self.chance(0.35) else None,
                }, produit))
                seq += 1
        return rows

    def _sinistres(self, contrats: list[tuple[dict, str]], src: str) -> list[dict]:
        rows: list[dict] = []
        seq = 1
        table = BRANCHES_AB if src == "AB" else PRODUITS_AP
        for contrat, code in contrats:
            *_, freq, cout = table[code]
            debut = date.fromisoformat(str(contrat["AB_DATE_DEBUT" if src == "AB" else "AP_START_DATE"])[:10])
            start = max(debut, date(2022, 1, 1))
            years = max(0.0, (self.ref - start).days / 365.25)
            for _ in range(_poisson(self.rng, freq * years)):
                when = self.rand_ts(start, self.ref)
                montant = self.lognormal(cout, 0.9, 40, 250000)
                age_days = (self.ref - when.date()).days
                if age_days < 45:
                    statut = self.weighted({"OPEN": 60, "UNDER_REVIEW": 40}) if src == "AP" else self.weighted(
                        {"OUVERT": 60, "EN_COURS": 40})
                elif src == "AB":
                    statut = self.weighted({"CLOS": 78, "REFUSE": 9, "EN_COURS": 13})
                else:
                    statut = self.weighted({"SETTLED": 76, "REJECTED": 11, "UNDER_REVIEW": 13})
                if src == "AB":
                    ref = contrat["AB_POLICY_NUMBER"] if not self.chance(0.004) else f"AB-POL-9{seq:06d}"
                    rows.append({
                        "AB_CLAIM_ID": 700000 + seq, "AB_POLICY_NUMBER": ref,
                        "AB_DATE_SINISTRE": when.strftime("%Y-%m-%d %H:%M:%S.%f"),
                        "AB_MONTANT_ESTIME": montant if not self.chance(0.002) else -montant,
                        "AB_STATUT_SINISTRE": statut, "AB_DESCRIPTION": self.rng.choice(DESCRIPTIONS[code]),
                    })
                else:
                    ref = contrat["AP_CONTRACT_REF"] if not self.chance(0.004) else f"APC9{seq:07d}"
                    r = self.rng.random()
                    if r < 0.80:
                        txt = when.strftime("%Y-%m-%d %H:%M:%S")
                    elif r < 0.90:
                        txt = when.strftime("%d/%m/%Y %H:%M")
                    elif r < 0.97:
                        txt = when.strftime("%Y-%m-%dT%H:%M:%S")
                    else:
                        txt = self.rng.choice(["N/A", "", "2025-13-45 10:00:00"])
                    fraud = round(min(99.99, max(0.0, self.rng.gauss(18, 14))), 2)
                    rows.append({
                        "AP_SINISTRE_NUM": 30000 + seq, "AP_CONTRACT_REF": ref, "AP_INCIDENT_DATE": txt,
                        "AP_ESTIMATED_AMOUNT": montant, "AP_CLAIM_STATUS": statut,
                        "AP_CLAIM_COMMENT": self.rng.choice(COMMENTS_AP),
                        "AP_FRAUD_SCORE": fraud if self.chance(0.97) else None,
                    })
                seq += 1
        return rows

    def _paiements_ab(self, contrats: list[tuple[dict, str]]) -> list[dict]:
        rows: list[dict] = []
        seq = 1
        for contrat, _ in contrats:
            prime = contrat["AB_PRIME_ANNUELLE"]
            if prime <= 0 or contrat["AB_STATUT_CONTRAT"] == "EN_ATTENTE":
                continue
            months = self.rng.choices([1, 3, 12], weights=[58, 14, 28])[0]
            mode = "PRELEVEMENT" if months == 1 else self.weighted({"PRELEVEMENT": 40, "CB": 30, "CHEQUE": 18,
                                                                    "VIREMENT": 12})
            debut = date.fromisoformat(contrat["AB_DATE_DEBUT"])
            fin = date.fromisoformat(contrat["AB_DATE_FIN"]) if contrat["AB_DATE_FIN"] else self.ref
            late = contrat["AB_STATUT_CONTRAT"] == "RESILIE" and self.chance(0.03)
            for due in _schedule(debut, min(fin, self.ref), months, PAY_FROM):
                paid = due + timedelta(days=self.rng.randint(0, 6))
                rows.append({"AB_PAYMENT_ID": seq, "AB_POLICY_NUMBER": contrat["AB_POLICY_NUMBER"],
                             "AB_DATE_PAIEMENT": paid.isoformat(), "AB_MONTANT": round(prime * months / 12, 2),
                             "AB_MODE_PAIEMENT": mode})
                seq += 1
            if late and fin < self.ref - timedelta(days=60):  # régularisation tardive (DQ15)
                rows.append({"AB_PAYMENT_ID": seq, "AB_POLICY_NUMBER": contrat["AB_POLICY_NUMBER"],
                             "AB_DATE_PAIEMENT": (fin + timedelta(days=self.rng.randint(40, 55))).isoformat(),
                             "AB_MONTANT": round(prime / 12, 2), "AB_MODE_PAIEMENT": mode})
                seq += 1
        for _ in range(max(1, round(8 * self.scale))):  # paiements orphelins (DQ14)
            rows.append({"AB_PAYMENT_ID": seq, "AB_POLICY_NUMBER": f"AB-POL-8{seq:06d}",
                         "AB_DATE_PAIEMENT": self.rand_date(PAY_FROM, self.ref).isoformat(),
                         "AB_MONTANT": self.lognormal(60, 0.5, 5, 900), "AB_MODE_PAIEMENT": "VIREMENT"})
            seq += 1
        return rows

    def _paiements_ap(self, contrats: list[tuple[dict, str]]) -> list[dict]:
        rows: list[dict] = []
        channels = {"SEPA_DD": 62, "CARD": 24, "APPLE_PAY": 9, "PAYPAL": 5}
        for contrat, _ in contrats:
            mensuel = contrat["AP_MONTHLY_PREMIUM"]
            if mensuel <= 0 or contrat["AP_CONTRACT_STATE"].lower() == "pending":
                continue
            channel = self.weighted(channels)
            debut = date.fromisoformat(contrat["AP_START_DATE"][:10])
            fin = date.fromisoformat(contrat["AP_END_DATE"][:10]) if contrat["AP_END_DATE"] else self.ref
            for due in _schedule(debut, min(fin, self.ref), 1, PAY_FROM):
                status = self.weighted({"SUCCEEDED": 95.2, "FAILED": 3.1, "REFUNDED": 0.7, "PENDING": 1.0})
                if (self.ref - due).days > 10 and status == "PENDING":
                    status = "SUCCEEDED"
                ts = datetime(due.year, due.month, due.day, self.rng.randint(0, 23), self.rng.randint(0, 59),
                              self.rng.randint(0, 59))
                rows.append({"AP_PAYMENT_REF": str(uuid.UUID(int=self.rng.getrandbits(128), version=4)).upper(),
                             "AP_CONTRACT_REF": contrat["AP_CONTRACT_REF"],
                             "AP_PAYMENT_DATETIME": ts.strftime("%Y-%m-%d %H:%M:%S"), "AP_AMOUNT_PAID": mensuel,
                             "AP_PAYMENT_CHANNEL": channel, "AP_TRANSACTION_STATUS": status})
        return rows


# ---------------------------------------------------------------------- utilitaires
def _ascii(s: str) -> str:
    from fusion.cleaning import strip_accents

    return strip_accents(s).lower().replace("'", "")


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 février
        return d.replace(year=d.year + years, day=28)


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, 28))


def _schedule(start: date, end: date, step_months: int, not_before: date) -> list[date]:
    """Échéances d'un contrat entre start et end (exclue), à pas de step_months, à partir de not_before."""
    out, k = [], 0
    while True:
        due = _add_months(start, k * step_months)
        if due >= end:
            return out
        if due >= not_before:
            out.append(due)
        k += 1


def _poisson(rng: random.Random, lam: float) -> int:
    threshold, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= threshold:
            return k
        k += 1


DDL_SQLITE = {
    "AB_CLIENT": "AB_CLIENT_ID INTEGER PRIMARY KEY, AB_NOM TEXT, AB_PRENOM TEXT, AB_DATE_NAISSANCE TEXT, AB_EMAIL TEXT, "
                 "AB_TELEPHONE TEXT, AB_ADRESSE TEXT, AB_CODE_POSTAL TEXT, AB_NUM_FISCAL TEXT, AB_DATE_CREATION TEXT, "
                 "AB_STATUT_CLIENT TEXT",
    "AB_CONTRAT": "AB_POLICY_NUMBER TEXT PRIMARY KEY, AB_CLIENT_ID INTEGER, AB_TYPE_ASSURANCE TEXT, AB_DATE_DEBUT TEXT, "
                  "AB_DATE_FIN TEXT, AB_PRIME_ANNUELLE NUMERIC, AB_STATUT_CONTRAT TEXT, AB_AGENCE_ID INTEGER",
    "AB_SINISTRE": "AB_CLAIM_ID INTEGER PRIMARY KEY, AB_POLICY_NUMBER TEXT, AB_DATE_SINISTRE TEXT, "
                   "AB_MONTANT_ESTIME NUMERIC, AB_STATUT_SINISTRE TEXT, AB_DESCRIPTION TEXT",
    "AB_PAIEMENT": "AB_PAYMENT_ID INTEGER PRIMARY KEY, AB_POLICY_NUMBER TEXT, AB_DATE_PAIEMENT TEXT, "
                   "AB_MONTANT NUMERIC, AB_MODE_PAIEMENT TEXT",
    "AP_USERS": "AP_USER_ID INTEGER PRIMARY KEY, AP_FULL_NAME TEXT, AP_BIRTH_DATE TEXT, AP_MAIL_ADDRESS TEXT, "
                "AP_PHONE_NUMBER TEXT, AP_STREET_ADDRESS TEXT, AP_ZIP_CODE TEXT, AP_CREATED_AT TEXT, "
                "AP_CUSTOMER_STATUS TEXT, AP_LOYALTY_SCORE INTEGER",
    "AP_CONTRACTS": "AP_CONTRACT_REF TEXT PRIMARY KEY, AP_USER_ID INTEGER, AP_PRODUCT_CODE TEXT, AP_START_DATE TEXT, "
                    "AP_END_DATE TEXT, AP_MONTHLY_PREMIUM NUMERIC, AP_CONTRACT_STATE TEXT, AP_BROKER_CODE TEXT",
    "AP_CLAIMS": "AP_SINISTRE_NUM INTEGER PRIMARY KEY, AP_CONTRACT_REF TEXT, AP_INCIDENT_DATE TEXT, "
                 "AP_ESTIMATED_AMOUNT NUMERIC, AP_CLAIM_STATUS TEXT, AP_CLAIM_COMMENT TEXT, AP_FRAUD_SCORE NUMERIC",
    "AP_PAYMENTS": "AP_PAYMENT_REF TEXT PRIMARY KEY, AP_CONTRACT_REF TEXT, AP_PAYMENT_DATETIME TEXT, "
                   "AP_AMOUNT_PAID NUMERIC, AP_PAYMENT_CHANNEL TEXT, AP_TRANSACTION_STATUS TEXT",
}


def _write_db(path: Path, dialect: str, tables: dict[str, list[dict]]) -> None:
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA journal_mode = OFF")
        for name, rows in tables.items():
            con.execute(f"CREATE TABLE {name} ({DDL_SQLITE[name]})")
            if rows:
                cols = list(rows[0])
                con.executemany(
                    f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",  # noqa: S608
                    [tuple(r[c] for c in cols) for r in rows],
                )
        con.execute("CREATE TABLE _META (cle TEXT, valeur TEXT)")
        con.execute("INSERT INTO _META VALUES ('dialecte_simule', ?)", (dialect,))
        con.commit()
    finally:
        con.close()


def generate(out_dir: Path, scale: float = 1.0, seed: int = 20261119, ref_date: date = date(2026, 10, 31)) -> dict:
    """Point d'entrée : génère les deux bases dans out_dir et retourne les volumes par table."""
    return Generator(scale=scale, seed=seed, ref_date=ref_date).run(Path(out_dir))
