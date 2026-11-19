"""MCD (notation Merise) de la base finale, rendu en SVG en niveaux de gris pour le dossier.

Les entités et associations décrites ici correspondent une à une aux tables et clés étrangères de
fusion/warehouse.py (le test tests/test_processing.py::test_entrepot_respecte_le_mcd vérifie l'entrepôt).
"""

from __future__ import annotations

from html import escape

HEAD, LINE, PAD = 24, 14.5, 7

# nom : (x, y, largeur, [identifiant, attributs…])
ENTITES = {
    "PERSONNE": (
        20,
        40,
        210,
        ["id_personne", "nom", "prénom", "date_naissance", "email", "téléphone", "nb_comptes", "sources"],
    ),
    "SYSTÈME_SOURCE": (330, 18, 220, ["code_source", "société", "sgbd", "année_création"]),
    "CLIENT_SOURCE": (
        330,
        190,
        220,
        [
            "client_uid",
            "id_source",
            "nom",
            "prénom",
            "date_naissance",
            "email",
            "téléphone",
            "adresse",
            "date_création",
            "statut",
            "num_fiscal",
            "score_fidélité",
        ],
    ),
    "TERRITOIRE": (20, 490, 210, ["code_postal", "pays", "département", "région"]),
    "PRODUIT": (690, 18, 200, ["code_produit", "libellé", "branche"]),
    "CONTRAT": (690, 220, 200, ["contrat_uid", "id_source", "date_début", "date_fin", "prime_annuelle", "statut"]),
    "DISTRIBUTEUR": (690, 468, 200, ["code_distributeur", "type_canal"]),
    "SINISTRE": (
        990,
        30,
        200,
        ["sinistre_uid", "id_source", "date_survenance", "montant_estimé", "statut", "score_fraude", "description"],
    ),
    "PAIEMENT": (
        990,
        380,
        200,
        ["paiement_uid", "id_source", "date_paiement", "montant", "mode", "statut", "est_tardif"],
    ),
    "RÈGLE_QUALITÉ": (330, 596, 220, ["code_règle", "libellé", "gravité", "action"]),
    "ANOMALIE": (760, 596, 200, ["id_anomalie", "entité", "uid", "valeur"]),
}

# nom : (cx, cy, [(entité, cardinalité, ancre x, ancre y, position x du libellé, position y)], attributs portés)
ASSOCIATIONS = {
    "CORRESPONDRE": (
        125,
        250,
        [("PERSONNE", "1,n", 125, 194, 131, 210), ("CLIENT_SOURCE", "1,1", 330, 262, 304, 256)],
        [],
    ),
    "PROVENIR": (
        440,
        152,
        [("SYSTÈME_SOURCE", "0,n", 440, 112, 446, 126), ("CLIENT_SOURCE", "1,1", 440, 190, 446, 184)],
        [],
    ),
    "ISSU_DE": (620, 140, [("SYSTÈME_SOURCE", "0,n", 550, 76, 556, 70), ("CONTRAT", "1,1", 730, 220, 704, 214)], []),
    "RÉSIDER": (
        276,
        446,
        [("CLIENT_SOURCE", "0,1", 330, 382, 304, 384), ("TERRITOIRE", "0,n", 210, 490, 216, 484)],
        [],
    ),
    "SOUSCRIRE": (620, 330, [("CLIENT_SOURCE", "0,n", 550, 286, 556, 280), ("CONTRAT", "1,1", 690, 286, 664, 280)], []),
    "PORTER_SUR": (790, 160, [("PRODUIT", "0,n", 790, 100, 796, 114), ("CONTRAT", "1,1", 790, 220, 796, 214)], []),
    "DISTRIBUER": (790, 412, [("CONTRAT", "0,1", 790, 342, 796, 356), ("DISTRIBUTEUR", "0,n", 790, 468, 796, 462)], []),
    "DÉCLARER": (940, 208, [("CONTRAT", "0,n", 890, 252, 896, 270), ("SINISTRE", "1,1", 990, 120, 964, 114)], []),
    "RÉGLER": (940, 392, [("CONTRAT", "0,n", 890, 322, 896, 316), ("PAIEMENT", "1,1", 990, 448, 964, 466)], []),
    "RAPPROCHER": (
        440,
        470,
        [("CLIENT_SOURCE", "0,n", 400, 396, 374, 410), ("CLIENT_SOURCE", "0,n", 480, 396, 486, 410)],
        ["score", "niveau", "décision"],
    ),
    "DÉCLENCHER": (
        655,
        642,
        [("RÈGLE_QUALITÉ", "0,n", 550, 642, 556, 636), ("ANOMALIE", "1,1", 760, 642, 734, 636)],
        [],
    ),
}


def height(n_attrs: int) -> float:
    return HEAD + PAD + n_attrs * LINE + PAD


def svg() -> str:
    out = [
        '<svg viewBox="0 0 1210 730" xmlns="http://www.w3.org/2000/svg" role="img" '
        'aria-label="Modèle conceptuel de données de la base unifiée" style="width:100%;height:auto">',
        "<style>.t{font:600 12px Cambria,Georgia,serif}.a{font:11px Cambria,Georgia,serif}"
        ".id{text-decoration:underline;font-weight:600}.as{font:600 10.5px Cambria,Georgia,serif;letter-spacing:.02em}"
        ".c{font:600 11px Cambria,Georgia,serif}.p{font:italic 10px Cambria,Georgia,serif}</style>",
    ]
    # liens d'abord (sous les formes)
    for _name, (cx, cy, links, _attrs) in ASSOCIATIONS.items():
        for _ent, _card, ax, ay, _lx, _ly in links:
            out.append(f'<line x1="{ax}" y1="{ay}" x2="{cx}" y2="{cy}" stroke="#111" stroke-width="1"/>')
    for name, (x, y, w, attrs) in ENTITES.items():
        h = height(len(attrs))
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#fff" stroke="#111" stroke-width="1.1"/>')
        out.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{HEAD}" fill="#e9e9e6" stroke="#111" stroke-width="1.1"/>'
        )
        out.append(f'<text class="t" x="{x + w / 2}" y="{y + 16}" text-anchor="middle">{escape(name)}</text>')
        for i, a in enumerate(attrs):
            cls = "a id" if i == 0 else "a"
            out.append(f'<text class="{cls}" x="{x + 10}" y="{y + HEAD + PAD + 11 + i * LINE}">{escape(a)}</text>')
    for name, (cx, cy, links, attrs) in ASSOCIATIONS.items():
        rx = max(46, len(name) * 4.4 + 14)
        out.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="16" fill="#fff" stroke="#111" stroke-width="1.1"/>')
        out.append(f'<text class="as" x="{cx}" y="{cy + 4}" text-anchor="middle">{escape(name)}</text>')
        if attrs:
            out.append(f'<text class="p" x="{cx}" y="{cy + 30}" text-anchor="middle">{escape(", ".join(attrs))}</text>')
        for _ent, card, _ax, _ay, lx, ly in links:
            out.append(f'<text class="c" x="{lx}" y="{ly}">{card}</text>')
    out.append(
        '<text class="p" x="1200" y="724" text-anchor="end">Identifiants soulignés. SINISTRE, PAIEMENT et '
        "ANOMALIE sont aussi liés à SYSTÈME_SOURCE (0,n – 1,1), liens non dessinés pour la lisibilité.</text>"
    )
    out.append("</svg>")
    return "\n".join(out)


if __name__ == "__main__":
    print(svg())
