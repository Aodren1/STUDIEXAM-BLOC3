"""Registre des transformations référencées par mapping.toml.

Signature commune : f(valeur, ctx) -> valeur. `ctx` donne la source, l'entité, l'identifiant de la ligne,
la ligne source complète, les attributs cibles déjà calculés (ordre du fichier) et le journal qualité.
Une transformation ne lève jamais d'exception sur une donnée sale : elle corrige, met à NULL et signale.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fusion import cleaning as c
from fusion import referentiels as ref
from fusion.quality import QualityLog

#: Tolérance au-delà de laquelle un montant de sinistre est jugé aberrant (DQ17).
MONTANT_SINISTRE_MAX = 1_000_000.0


@dataclass
class Ctx:
    src: str  # "AB" | "AP"
    entite: str
    uid: str
    row: dict[str, Any]
    log: QualityLog
    out: dict[str, Any] = field(default_factory=dict)

    def flag(self, code: str, valeur: object = None) -> None:
        self.log.flag(code, self.src, self.entite, self.uid, valeur)


Transform = Callable[[Any, Ctx], Any]
REGISTRY: dict[str, Transform] = {}


def transform(name: str) -> Callable[[Transform], Transform]:
    def deco(fn: Transform) -> Transform:
        REGISTRY[name] = fn
        return fn

    return deco


# ------------------------------------------------------------------ génériques
@transform("texte")
def _texte(v, ctx):
    if v is None:
        return None
    text = str(v).strip()
    return text or None


@transform("majuscules")
def _majuscules(v, ctx):
    return c.strip_accents(str(v)).upper().strip() if v not in (None, "") else None


@transform("prenom")
def _prenom(v, ctx):
    return c.title_case(v) or None


@transform("cle_source")
def _cle_source(v, ctx):
    return None if v in (None, "") else f"{ctx.src}-{str(v).strip()}"


@transform("entier")
def _entier(v, ctx):
    try:
        return None if v in (None, "") else int(v)
    except (TypeError, ValueError):
        return None


@transform("decimal")
def _decimal(v, ctx):
    return c.to_float(v)


@transform("montant")
def _montant(v, ctx):
    f = c.to_float(v)
    return None if f is None else round(f, 2)


@transform("date")
def _date(v, ctx):
    return c.parse_date(v)


@transform("horodatage")
def _horodatage(v, ctx):
    return c.parse_date(v, with_time=True)


# ------------------------------------------------------------------ client
@transform("nom_depuis_nom_complet")
def _nom_complet_nom(v, ctx):
    nom, _, corrected = c.split_full_name(v)
    if corrected:
        ctx.flag("DQ04", v)
    return nom or None


@transform("prenom_depuis_nom_complet")
def _nom_complet_prenom(v, ctx):
    return c.split_full_name(v)[1] or None


@transform("date_naissance_ab")
def _naissance_ab(v, ctx):
    d = c.parse_date(v)
    if d is None and v not in (None, ""):
        ctx.flag("DQ02", v)
    return d


@transform("date_naissance_ap")
def _naissance_ap(v, ctx):
    raw = "" if v is None else str(v).strip()
    d = c.parse_date(raw)
    if d is None:
        ctx.flag("DQ02", raw)
    elif not c.is_iso_date(raw):
        ctx.flag("DQ01", raw)
    return d


@transform("email")
def _email(v, ctx):
    e = c.norm_email(v)
    if e is None:
        ctx.flag("DQ03", v)
    return e


@transform("code_postal")
def _code_postal(v, ctx):
    return c.norm_zip(v) or None


@transform("pays")
def _pays(v, ctx):
    return c.country_from_zip(v)


@transform("region")
def _region(v, ctx):
    return c.region_from_zip(v)


@transform("telephone")
def _telephone(v, ctx):
    tel = c.norm_phone(v, ctx.out.get("pays") or "FR")
    if tel is None:
        ctx.flag("DQ12", v)
    return tel


@transform("adresse")
def _adresse(v, ctx):
    return c.norm_address(v) or None


def _referentiel(table: dict[str, str], v, ctx, canonical_lower: bool) -> str:
    raw = "" if v is None else str(v).strip()
    code = table.get(raw) or table.get(raw.lower()) or table.get(raw.upper())
    canonical = raw == raw.lower() if canonical_lower else raw == raw.upper()
    if code is None or not canonical:
        ctx.flag("DQ05", raw)
    return code or "INCONNU"


@transform("statut_client")
def _statut_client(v, ctx):
    return _referentiel(ref.STATUT_CLIENT, v, ctx, canonical_lower=ctx.src == "AP")


@transform("num_fiscal")
def _num_fiscal(v, ctx):
    value = None if v in (None, "") else str(v).strip()
    if value is None and ctx.out.get("pays") == "FR":
        ctx.flag("DQ11")
    return value


# ------------------------------------------------------------------ contrat
@transform("produit")
def _produit(v, ctx):
    code = "" if v is None else str(v).strip().upper()
    return code if code in ref.PRODUITS else "INCONNU"


@transform("branche")
def _branche(v, ctx):
    code = "" if v is None else str(v).strip().upper()
    return ref.PRODUITS.get(code, ("AUTRE", ""))[0]


@transform("mensuelle_vers_annuelle")
def _annualise(v, ctx):
    return None if v is None else round(v * 12, 2)


@transform("prime_positive")
def _prime_positive(v, ctx):
    if v is None or v <= 0:
        ctx.flag("DQ07", v)
        return None
    return v


@transform("statut_contrat")
def _statut_contrat(v, ctx):
    # AP : la casse attendue est « Active » (application .NET) ; AB : majuscules
    raw = "" if v is None else str(v).strip()
    code = ref.STATUT_CONTRAT.get(raw.upper()) or ref.STATUT_CONTRAT.get(raw.lower())
    canonical = raw == raw.capitalize() if ctx.src == "AP" else raw == raw.upper()
    if code is None or not canonical:
        ctx.flag("DQ05", raw)
    return code or "INCONNU"


@transform("canal_ap")
def _canal_ap(v, ctx):
    return "COURTIER" if v not in (None, "") else "DIGITAL"


@transform("agence")
def _agence(v, ctx):
    return None if v in (None, "") else f"AG-{int(v):03d}"


@transform("distributeur_ap")
def _distributeur_ap(v, ctx):
    return str(v).strip() if v not in (None, "") else "APP-MOBILE"


# ------------------------------------------------------------------ sinistre
@transform("date_sinistre")
def _date_sinistre(v, ctx):
    raw = "" if v is None else str(v).strip()
    d = c.parse_date(raw, with_time=True)
    if d is None:
        ctx.flag("DQ09", raw)
    elif ctx.src == "AP" and not c.is_iso_datetime(raw):
        ctx.flag("DQ08", raw)
    return d


@transform("montant_sinistre")
def _montant_sinistre(v, ctx):
    if v is None or v < 0 or v > MONTANT_SINISTRE_MAX:
        ctx.flag("DQ17", v)
        return None
    return v


@transform("statut_sinistre")
def _statut_sinistre(v, ctx):
    raw = "" if v is None else str(v).strip().upper()
    code = ref.STATUT_SINISTRE.get(raw)
    if code is None:
        ctx.flag("DQ05", v)
    return code or "INCONNU"


# ------------------------------------------------------------------ paiement
@transform("mode_paiement")
def _mode(v, ctx):
    raw = "" if v is None else str(v).strip().upper()
    code = ref.MODE_PAIEMENT.get(raw)
    if code is None:
        ctx.flag("DQ05", v)
    return code or "AUTRE"


@transform("statut_paiement")
def _statut_paiement(v, ctx):
    raw = "" if v is None else str(v).strip().upper()
    code = ref.STATUT_PAIEMENT.get(raw)
    if code is None:
        ctx.flag("DQ05", v)
    return code or "INCONNU"


@transform("statut_paiement_ab")
def _statut_paiement_ab(v, ctx):
    ctx.log.flag_once("DQ13", ctx.src, ctx.entite, "AB_PAIEMENT sans colonne de statut")
    return "REUSSI"
