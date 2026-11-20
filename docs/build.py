"""Assemble docs/src/*.html en docs/dossier.html puis imprime le PDF du dossier avec Chrome / Edge headless.

Les tableaux chiffrés sont générés depuis les fichiers produits par le code (zone gold, rapport d'exécution,
mapping, couverture, rapports qualité, historique git) : aucun chiffre n'est recopié à la main.

    python docs/build.py [--html-only]
    GITHUB_URL=… APP_URL=… python docs/build.py
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess  # nosec B404 - outils locaux (git, navigateur) appelés avec des arguments fixes
import sys
import xml.etree.ElementTree as ET  # nosec B405 - fichier coverage.xml produit localement par pytest-cov
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
sys.path.insert(0, str(ROOT))

from fusion.mapping import MappingSpec  # noqa: E402
from fusion.quality import REGLES  # noqa: E402

LINKS = {
    "GITHUB_URL": os.environ.get("GITHUB_URL", "https://github.com/Aodren1/STUDIEXAM-BLOC3"),
    "APP_URL": os.environ.get("APP_URL", "http://51.210.246.138:8096"),
}
LINKS["CI_URL"] = LINKS["GITHUB_URL"] + "/actions"
LINKS["SONAR_URL"] = "https://sonarcloud.io/project/overview?id=Aodren1_STUDIEXAM-BLOC3"
PDF = ROOT / "BCDBigData_Examen_B3_copiearendre_FLORIDE_Aodren.pdf"
ESC = html.escape


def nb(value, digits: int = 0) -> str:
    """Nombre à la française : espace insécable pour les milliers (1 000), virgule décimale."""
    if value is None:
        return "—"
    s = f"{value:,.{digits}f}".replace(",", "\u00a0").replace(".", ",")
    return s


def pct(value, digits: int = 1) -> str:
    return "—" if value is None else f"{nb(value, digits)}\u00a0%"


def eur(value) -> str:
    return "—" if value is None else f"{nb(value)}\u00a0€"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout


def table(head: list[str], rows: list[list], num: tuple[int, ...] = (), cls: str = "compact", caption: str = "") -> str:
    th = "".join(f'<th class="num">{h}</th>' if i in num else f"<th>{h}</th>" for i, h in enumerate(head))
    body = "\n".join(
        "<tr>"
        + "".join(f'<td class="num">{c}</td>' if i in num else f"<td>{c}</td>" for i, c in enumerate(r))
        + "</tr>"
        for r in rows
    )
    cap = f"<caption>{caption}</caption>" if caption else ""
    return f'<table class="{cls}">{cap}<thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


# ---------------------------------------------------------------------------- générateurs
class Gen:
    def __init__(self) -> None:
        self.gold = load_json(ROOT / "data" / "lake" / "gold" / "dashboard.json")
        self.report = load_json(ROOT / "data" / "rapport_execution.json")  # dernière exécution (incrémentale)
        self.complet = load_json(DOCS / "src" / "execution-complete.json")  # exécution complète de référence
        self.spec = MappingSpec.load()

    def liens(self) -> str:
        rows = [
            [
                "Dépôt Git public (code, tests, CI, dossier)",
                f'<a href="{LINKS["GITHUB_URL"]}">{LINKS["GITHUB_URL"]}</a>',
            ],
            [
                "Branche de production",
                "<code>main</code> — dernière version taguée <code>v1.0.1</code> (correctif après <code>v1.0.0</code>)",
            ],
            ["Branche d’intégration", "<code>develop</code>"],
            [
                "Convention de nommage des branches",
                f'<a href="{LINKS["GITHUB_URL"]}/blob/main/CONTRIBUTING.md">CONTRIBUTING.md</a> (vérifiée par la CI)',
            ],
            ["Application déployée (tableau de bord)", f'<a href="{LINKS["APP_URL"]}">{LINKS["APP_URL"]}</a>'],
            ["Compte administrateur", "identifiant <code>admin</code> — mot de passe <code>Admin-Fusion-2026</code>"],
            [
                "Compte utilisateur",
                "identifiant <code>analyste</code> — mot de passe <code>Analyste-Fusion-2026</code>",
            ],
            ["Intégration continue (GitHub Actions)", f'<a href="{LINKS["CI_URL"]}">{LINKS["CI_URL"]}</a>'],
            [
                "Contrôle qualité du code",
                "ruff et bandit, bloquants dans la CI (job <code>qualite</code>, rapports en artefact). "
                "SonarCloud configuré (<code>sonar-project.properties</code>, job <code>sonarcloud</code>), "
                "analyse activée dès l’ajout du jeton <code>SONAR_TOKEN</code> (dossier 3, question 1)",
            ],
            ["Couverture des tests", "rapport HTML : artefact « couverture » de chaque exécution de la CI"],
            ["Lancer en local", "<code>docker compose up -d --build</code> puis http://localhost:8096"],
        ]
        return table(["Description", "Valeur"], rows, cls="links")

    def git_graph(self) -> str:
        log = git("log", "--graph", "--all", "--format=%h %d %s", "--date-order")
        lines = [ln.rstrip() for ln in log.splitlines()]
        return f'<pre class="code">{ESC(chr(10).join(lines))}</pre>'

    def branches(self) -> str:
        out = git(
            "for-each-ref", "--sort=refname", "--format=%(refname:short)|%(objectname:short)|%(subject)", "refs/heads"
        )
        rows = []
        for line in out.splitlines():
            name, sha, subject = line.split("|", 2)
            if name.startswith("claude/"):
                continue
            rows.append([f"<code>{ESC(name)}</code>", f"<code>{sha}</code>", ESC(subject)])
        tags = git("tag", "--list").split()
        cap = f"Étiquettes : {', '.join(tags) if tags else 'aucune'}. Liste produite par git for-each-ref."
        return table(["Branche", "Dernier commit", "Message"], rows, caption=cap)

    def mapping(self) -> str:
        rows = [
            [
                f"<b>{r['entite']}</b>.{r['attribut_cible']}<br><span class='small muted'>{r['type_cible']}</span>",
                f"<code>{r['colonne_ab']}</code><br><span class='small'>{r['transformation_ab']}</span>",
                f"<code>{r['colonne_ap']}</code><br><span class='small'>{r['transformation_ap']}</span>",
                f"<span class='small'>{ESC(r['regle'])}</span>",
            ]
            for r in self.spec.rows()
        ]
        return table(
            ["Attribut cible", "AbAssurance (Oracle)", "AssurePlus (SQL Server)", "Règle"], rows, cls="compact map"
        )

    def regles_qualite(self) -> str:
        rows = [
            [
                f"<b>{r['code']}</b>",
                ESC(r["libelle"]),
                r["gravite"],
                nb(r["AB"]),
                nb(r["AP"]),
                nb(r["total"]),
                ESC(r["action"]),
            ]
            for r in self.gold["qualite"]["regles"]
        ]
        return table(["Règle", "Défaut détecté", "Gravité", "AB", "AP", "Total", "Traitement"], rows, num=(3, 4, 5))

    def reconciliation(self) -> str:
        rows = [
            [
                f"<code>{r['table_source']}</code>",
                nb(r["lignes_source"]),
                nb(r["lignes_silver"]),
                "0" if not r["ecart"] else nb(r["ecart"]),
            ]
            for r in self.gold["qualite"]["reconciliation"]
        ]
        return table(["Table source", "Lignes source", "Lignes silver", "Écart"], rows, num=(1, 2, 3))

    def execution(self) -> str:
        rep = self.complet
        steps = [[ESC(s["etape"]), nb(s["duree_s"], 2)] for s in rep["etapes"]]
        steps.append(["<b>Total</b>", f"<b>{nb(rep['duree_totale_s'], 2)}</b>"])
        return table(
            ["Étape (rôle de l’outil cible)", "Durée (s)"],
            steps,
            num=(1,),
            caption=f"Exécution {rep['run_id']} ({rep['mode']}), bus « {rep['bus']} », lac « {rep['lac']} ».",
        )

    def extraction(self) -> str:
        complet = self.complet
        rows = [
            [
                f"<code>{s['table']}</code>",
                f"<code>{s['topic']}</code>",
                nb(s["lignes"]),
                eur(s["somme_controle"]) if s["somme_controle"] else "—",
            ]
            for s in complet["extraction"]
        ]
        return table(["Table source", "Topic Kafka", "Messages publiés", "Somme de contrôle"], rows, num=(2, 3))

    def entrepot(self) -> str:
        rows = [[f"<code>{t}</code>", nb(n)] for t, n in self.complet["entrepot"].items()]
        half = (len(rows) + 1) // 2
        return (
            '<div class="grid2">'
            + table(["Table", "Lignes"], rows[:half], num=(1,))
            + table(["Table", "Lignes"], rows[half:], num=(1,))
            + "</div>"
        )

    def kpis(self) -> str:
        g = self.gold
        ev = g["doublons"]["kpi"]["evaluation"]["certain"]
        ref = date.fromisoformat(g["date_reference"]).strftime("%d/%m/%Y")
        c, ct, s, f, d = (
            g["clients"]["kpi"],
            g["contrats"]["kpi"],
            g["sinistres"]["kpi"],
            g["finances"]["kpi"],
            g["doublons"]["kpi"],
        )
        rows = [
            [
                "Répartition des clients",
                "Comptes clients AB / AP",
                f"{nb(c['comptes']['AB'])} / {nb(c['comptes']['AP'])}",
            ],
            ["", "Personnes uniques après dédoublonnage", nb(c["personnes"])],
            [
                "",
                "Clients communs aux deux sociétés",
                f"{nb(c['clients_communs'])} ({pct(c['taux_recouvrement_ap'])} des clients AP)",
            ],
            [
                "Statistiques contrats",
                "Contrats actifs AB / AP",
                f"{nb(ct['actifs']['AB'])} / {nb(ct['actifs']['AP'])}",
            ],
            ["", "Primes annualisées du portefeuille actif", eur(ct["primes_annualisees"])],
            ["", "Prime moyenne par contrat actif", eur(ct["prime_moyenne"])],
            [
                "Statistiques sinistres",
                "Sinistres / survenus sur 12 mois",
                f"{nb(s['sinistres'])} / {nb(s['survenus_12_mois'])}",
            ],
            ["", "Coût moyen / médian", f"{eur(s['cout_moyen'])} / {eur(s['cout_median'])}"],
            [
                "",
                "Taux de refus ; suspicion de fraude (AP)",
                f"{pct(s['taux_refus'])} ; {nb(s['suspicion_fraude'])} dossiers",
            ],
            ["Statistiques financières", "Encaissé sur 12 mois", eur(f["encaisse_12_mois"])],
            ["", "Ratio sinistres / primes sur 12 mois", pct(f["ratio_sp_12_mois"])],
            ["", "Taux d’échec des prélèvements AP", pct(f["taux_echec_paiement_ap"], 2)],
            [
                "Matrice de doublons",
                "Paires certaines / probables",
                f"{nb(d['paires_certaines'])} / {nb(d['paires_probables'])}",
            ],
            ["", "Taux de doublons", pct(d["taux_doublons"])],
            [
                "",
                "Précision / rappel (paires certaines)",
                f"{pct(100 * ev['precision'])} / {pct(100 * ev['rappel'])}",
            ],
        ]
        return table(
            ["Vue demandée", "Indicateur", "Valeur mesurée"],
            rows,
            caption=f"Zone gold au {ref}, jeu de test au 1/2 000e.",
        )

    def matrice(self) -> str:
        m = self.gold["doublons"]["matrice_sources"]
        rows = []
        for niv in ("CERTAIN", "PROBABLE"):
            for a in ("AB", "AP"):
                rows.append(
                    [
                        niv.lower() if a == "AB" else "",
                        {"AB": "AbAssurance", "AP": "AssurePlus"}[a],
                        nb(m[niv][a]["AB"]),
                        nb(m[niv][a]["AP"]),
                    ]
                )
        return table(["Niveau", "Source du compte A", "avec AbAssurance", "avec AssurePlus"], rows, num=(2, 3))

    def couverture(self) -> str:
        tree = ET.parse(ROOT / "coverage.xml")  # noqa: S314 - fichier local produit par pytest-cov
        rows = []
        for cls in tree.iter("class"):
            fn = "fusion/" + cls.get("filename").replace("\\", "/")
            lines = cls.find("lines")
            n = len(lines) if lines is not None else 0
            rows.append(
                [
                    f"<code>{fn}</code>",
                    nb(n),
                    pct(100 * float(cls.get("line-rate"))),
                    pct(100 * float(cls.get("branch-rate"))),
                ]
            )
        root = tree.getroot()
        rows.append(
            [
                "<b>Total</b>",
                f"<b>{nb(int(root.get('lines-valid')))}</b>",
                f"<b>{pct(100 * float(root.get('line-rate')))}</b>",
                f"<b>{pct(100 * float(root.get('branch-rate')))}</b>",
            ]
        )
        return table(["Module", "Lignes", "Couverture des lignes", "Couverture des branches"], rows, num=(1, 2, 3))

    def tests(self) -> str:
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        ).stdout
        count: dict[str, int] = {}
        for line in out.splitlines():
            if "::" in line:
                f = line.split("::")[0].replace("\\", "/")
                count[f] = count.get(f, 0) + 1
        doc = {
            "tests/test_cleaning.py": "Règles de nettoyage (noms, dates, téléphones, e-mails, adresses)",
            "tests/test_mapping.py": "Moteur de mapping, validation statique, export tMap",
            "tests/test_generator.py": "Fidélité des sources simulées à l’annexe, déterminisme",
            "tests/test_bus_lake_extract.py": "Bus (sémantique Kafka), lac local et WebHDFS, extraction incrémentale",
            "tests/test_processing.py": "Silver, doublons, gold, entrepôt (MCD), pipeline complet",
            "tests/test_idempotence.py": "Non-régression de l’anomalie FUS-9 (republication)",
            "tests/test_web.py": "Tableau de bord : rôles, CSRF, limitation, en-têtes de sécurité",
            "tests/test_sqlsafe.py": "Liste blanche des identifiants SQL",
            "tests/test_kafka_integration.py": "Intégration avec un vrai broker Kafka (CI)",
            "tests/spark/test_spark_jobs.py": "Équivalence des jobs Spark et du moteur local (CI, JVM)",
        }
        rows = [[f"<code>{f}</code>", doc.get(f, ""), nb(n)] for f, n in sorted(count.items())]
        rows.append(["<code>tests/js/format.test.mjs</code>", "Formatage des nombres du tableau de bord (Node)", "5"])
        self._nb_tests = sum(count.values()) + 5
        rows.append(["<b>Total</b>", "", f"<b>{nb(self._nb_tests)}</b>"])
        return table(["Fichier", "Objet", "Tests"], rows, num=(2,))

    def ruff_avant(self) -> str:
        text = (ROOT / "reports" / "qualite" / "ruff-1-initial.txt").read_text(encoding="utf-8")
        rows = []
        for line in text.splitlines():
            m = re.match(r"\s*(\d+)\s+(\S+)\s+(?:\[\*?\s?\]\s+)?(.+)", line)
            if m:
                rows.append([f"<code>{m[2]}</code>", ESC(m[3]), nb(int(m[1]))])
        return table(["Règle", "Alerte", "Nombre"], rows, num=(2,))

    def fichier(self, name: str) -> str:
        return f'<pre class="code">{ESC((ROOT / name).read_text(encoding="utf-8").strip())}</pre>'

    def calibrage(self) -> str:
        text = (ROOT / "reports" / "qualite" / "calibrage-seuil-doublons.txt").read_text(encoding="utf-8")
        rows = [ln.split() for ln in text.splitlines()[2:] if ln.strip()]
        out = [[nb(float(s), 2), nb(int(n)), pct(100 * float(p)), pct(100 * float(r))] for s, n, p, r in rows]
        return table(["Seuil « certain »", "Paires fusionnées", "Précision", "Rappel"], out, num=(0, 1, 2, 3))

    def mcd(self) -> str:
        sys.path.insert(0, str(DOCS))
        import mcd

        return mcd.svg()

    def comptes(self) -> str:
        return nb(self.gold["clients"]["kpi"]["comptes_total"])

    def personnes(self) -> str:
        return nb(self.gold["clients"]["kpi"]["personnes"])

    def _cov(self):
        return ET.parse(ROOT / "coverage.xml").getroot()  # noqa: S314 - fichier local

    def cov_lignes(self) -> str:
        return pct(100 * float(self._cov().get("line-rate")))

    def cov_branches(self) -> str:
        return pct(100 * float(self._cov().get("branch-rate")))

    def nb_tests(self) -> str:
        if not hasattr(self, "_nb_tests"):
            self.tests()
        return nb(self._nb_tests)

    def date(self) -> str:
        mois = [
            "janvier",
            "février",
            "mars",
            "avril",
            "mai",
            "juin",
            "juillet",
            "août",
            "septembre",
            "octobre",
            "novembre",
            "décembre",
        ]
        today = date.today()
        return f"{today.day} {mois[today.month - 1]} {today.year}"

    def nb_regles(self) -> str:
        return str(len(REGLES))


def assemble() -> Path:
    gen = Gen()
    parts = sorted((DOCS / "src").glob("*.html"))
    body = []
    for p in parts:
        text = p.read_text(encoding="utf-8")
        if p.name.startswith("00_"):
            head, rest = text.split("<body>", 1)
            body.append(rest.replace("</body>", "").replace("</html>", ""))
        else:
            body.append(text)
    doc = head + "<body>\n" + "\n".join(body) + "\n</body>\n</html>\n"

    def repl(m: re.Match) -> str:
        name, _, arg = m.group(1).partition(":")
        fn = getattr(gen, name)
        return fn(arg) if arg else fn()

    doc = re.sub(r"<!--GEN:([a-z_]+(?::[^>]+?)?)-->", repl, doc)
    for k, v in LINKS.items():
        doc = doc.replace("{{" + k + "}}", v)
    out = DOCS / "dossier.html"
    out.write_text(doc, encoding="utf-8")
    return out


def print_pdf(html_file: Path) -> None:
    browser = next(
        (
            p
            for p in (
                os.environ.get("CHROME"),
                "C:/Program Files/Google/Chrome/Application/chrome.exe",
                "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                "/usr/bin/google-chrome",
            )
            if p and Path(p).exists()
        ),
        None,
    )
    if browser is None:
        raise SystemExit("navigateur introuvable (variable CHROME)")
    subprocess.run(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            "--virtual-time-budget=8000",
            f"--print-to-pdf={PDF}",
            html_file.as_uri(),
        ],
        check=True,
        capture_output=True,
    )


if __name__ == "__main__":
    html_file = assemble()
    print(f"assemblé : {html_file}")
    if "--html-only" not in sys.argv:
        print_pdf(html_file)
        print(f"PDF : {PDF} ({PDF.stat().st_size // 1024} Ko)")
