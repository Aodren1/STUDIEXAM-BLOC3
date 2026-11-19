"""Ligne de commande.

    python -m fusion seed [--force]        génère les deux bases sources simulées
    python -m fusion extract               publie les nouvelles lignes des sources sur le bus (rôle Talend)
    python -m fusion run [--complet]       exécute le pipeline (incrémental par défaut)
    python -m fusion mapping [fichier]     exporte le mapping en CSV (tMap Talend, documentation)
    python -m fusion serve [--port 8000]   démarre le tableau de bord (waitress)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from fusion.config import Settings


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")
    parser = argparse.ArgumentParser(prog="fusion", description="Plateforme de données AbAssurance × AssurePlus")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_seed = sub.add_parser("seed", help="génère les bases sources")
    p_seed.add_argument("--force", action="store_true")
    sub.add_parser("extract", help="publie les nouvelles lignes sources sur le bus")
    p_run = sub.add_parser("run", help="exécute le pipeline")
    p_run.add_argument("--complet", action="store_true", help="rejeu intégral (bus, lac et filigranes remis à zéro)")
    p_map = sub.add_parser("mapping", help="exporte le mapping en CSV")
    p_map.add_argument("fichier", nargs="?", default="talend/tmap_mapping.csv")
    p_srv = sub.add_parser("serve", help="démarre le tableau de bord")
    p_srv.add_argument("--host", default="127.0.0.1")
    p_srv.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    settings = Settings()

    if args.cmd == "seed":
        from fusion.pipeline import seed

        print(json.dumps(seed(settings, force=args.force), indent=1))
    elif args.cmd == "extract":
        from fusion.pipeline import extract_only

        print(json.dumps(extract_only(settings), indent=1, ensure_ascii=False))
    elif args.cmd == "run":
        from fusion.pipeline import run

        report = run(settings, incremental=not args.complet)
        print(json.dumps({k: report[k] for k in ("run_id", "mode", "duree_totale_s", "etapes")}, indent=1,
                         ensure_ascii=False))
    elif args.cmd == "mapping":
        from fusion.mapping import MappingSpec

        n = MappingSpec.load().export_csv(args.fichier)
        print(f"{n} attributs exportés dans {args.fichier}")
    elif args.cmd == "serve":
        from waitress import serve

        from fusion.web import create_app

        serve(create_app(settings), host=args.host, port=args.port, threads=8)
    return 0


if __name__ == "__main__":
    sys.exit(main())
