"""Tableau de bord de validation (Flask) : lecture seule de la zone gold, deux rôles.

    administrateur : tous les onglets, la liste nominative des paires de doublons à valider,
                     l'onglet « Qualité et exécution » et le relancement du pipeline ;
    utilisateur    : les cinq rubriques statistiques, sans aucune donnée personnelle.

Sécurité : mots de passe hachés (scrypt via Werkzeug), jeton CSRF sur les formulaires, limitation des
tentatives de connexion, cookie de session HttpOnly / SameSite, en-têtes CSP stricts (aucun script tiers).
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from functools import wraps
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from fusion import __version__
from fusion.config import Settings
from fusion.lake import LocalLake

SECTIONS = ("synthese", "clients", "contrats", "sinistres", "finances", "doublons", "qualite")
ADMIN_ONLY = {"qualite"}
MAX_ATTEMPTS = 5
WINDOW_S = 300

#: Comptes de démonstration (surchargés en production par les variables d'environnement).
DEFAULT_ACCOUNTS = {
    "admin": ("FUSION_ADMIN_PASSWORD", "Admin-Fusion-2026", "admin", "Administrateur de la plateforme"),
    "analyste": ("FUSION_USER_PASSWORD", "Analyste-Fusion-2026", "user", "Analyste métier"),
}


def load_accounts() -> dict[str, dict[str, str]]:
    accounts = {}
    for login, (env, default, role, label) in DEFAULT_ACCOUNTS.items():
        accounts[login] = {"hash": generate_password_hash(os.environ.get(env, default)), "role": role, "label": label}
    return accounts


class LoginThrottle:
    """Au-delà de 5 échecs en 5 minutes pour une même adresse, les tentatives sont refusées."""

    def __init__(self, max_attempts: int = MAX_ATTEMPTS, window_s: int = WINDOW_S):
        self.max_attempts, self.window_s = max_attempts, window_s
        self.failures: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def _purge(self, key: str, now: float) -> deque[float]:
        q = self.failures[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        with self.lock:
            return len(self._purge(key, time.monotonic())) >= self.max_attempts

    def fail(self, key: str) -> None:
        with self.lock:
            self._purge(key, time.monotonic()).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self.lock:
            self.failures.pop(key, None)


def create_app(settings: Settings | None = None, accounts: dict[str, dict[str, str]] | None = None) -> Flask:
    settings = settings or Settings()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("FUSION_SECRET_KEY") or secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("FUSION_SECURE_COOKIES") == "1",
        PERMANENT_SESSION_LIFETIME=8 * 3600,
        JSON_SORT_KEYS=False,
    )
    app.extensions["fusion"] = {
        "settings": settings,
        "accounts": accounts or load_accounts(),
        "throttle": LoginThrottle(),
        "pipeline_lock": threading.Lock(),
        "pipeline_state": {"etat": "inactif", "message": None},
    }
    _register(app)
    return app


# ---------------------------------------------------------------------------- aides
def _ext() -> dict[str, Any]:
    from flask import current_app

    return current_app.extensions["fusion"]


def _gold() -> dict[str, Any] | None:
    path: Path = _ext()["settings"].gold_path
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _check_csrf() -> None:
    sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token", "")
    if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
        abort(400, "jeton CSRF invalide")


def login_required(role: str | None = None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if "user" not in session:
                if request.path.startswith("/api/"):
                    abort(401)
                return redirect(url_for("login", suivant=request.path))
            if role and session.get("role") != role:
                abort(403)
            return fn(*args, **kwargs)

        return wrapper

    return deco


# ---------------------------------------------------------------------------- routes
def _register(app: Flask) -> None:  # noqa: C901 - table de routage
    @app.after_request
    def security_headers(resp):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.context_processor
    def inject():
        return {"csrf_token": _csrf_token, "version": __version__}

    @app.get("/connexion")
    def login():
        if "user" in session:
            return redirect(url_for("index"))
        return render_template("login.html", erreur=None, suivant=request.args.get("suivant", "/"))

    @app.post("/connexion")
    def login_post():
        _check_csrf()
        ext = _ext()
        key = request.remote_addr or "?"
        if ext["throttle"].blocked(key):
            return render_template("login.html", erreur="Trop de tentatives. Réessayez dans quelques minutes.",
                                   suivant="/"), 429
        login_ = request.form.get("identifiant", "").strip().lower()
        account = ext["accounts"].get(login_)
        if not account or not check_password_hash(account["hash"], request.form.get("mot_de_passe", "")):
            ext["throttle"].fail(key)
            return render_template("login.html", erreur="Identifiant ou mot de passe incorrect.", suivant="/"), 401
        ext["throttle"].reset(key)
        session.clear()  # nouvelle session : pas de fixation de session
        session.permanent = True
        session.update(user=login_, role=account["role"], label=account["label"])
        target = request.form.get("suivant", "/")
        return redirect(target if target.startswith("/") and not target.startswith("//") else "/")

    @app.post("/deconnexion")
    def logout():
        _check_csrf()
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required()
    def index():
        gold = _gold()
        return render_template("dashboard.html", gold_ok=gold is not None,
                               ref=gold["date_reference"] if gold else None,
                               user=session["user"], role=session["role"], label=session["label"])

    @app.get("/api/sante")
    def health():
        return jsonify({"statut": "ok", "version": __version__, "gold": _ext()["settings"].gold_path.exists()})

    @app.get("/api/session")
    @login_required()
    def whoami():
        return jsonify({"utilisateur": session["user"], "role": session["role"], "libelle": session["label"]})

    @app.get("/api/gold/<section>")
    @login_required()
    def gold_section(section: str):
        if section not in SECTIONS:
            abort(404)
        if section in ADMIN_ONLY and session.get("role") != "admin":
            abort(403)
        gold = _gold()
        if gold is None:
            abort(503, "zone gold absente : exécuter le pipeline")
        return jsonify({"date_reference": gold["date_reference"], "role": session["role"], **{section: gold[section]}})

    @app.get("/api/doublons/paires")
    @login_required(role="admin")
    def duplicate_pairs():
        rows = LocalLake(_ext()["settings"].lake_dir).read_table("gold", "paires_doublons_detail")
        niveau = request.args.get("niveau")
        if niveau in ("CERTAIN", "PROBABLE"):
            rows = [r for r in rows if r["niveau"] == niveau]
        return jsonify({"paires": rows[:200], "total": len(rows)})

    @app.post("/api/pipeline")
    @login_required(role="admin")
    def run_pipeline():
        _check_csrf()
        ext = _ext()
        if not ext["pipeline_lock"].acquire(blocking=False):
            return jsonify(ext["pipeline_state"]), 409
        ext["pipeline_state"].update(etat="en_cours", message=None)

        def job():
            from fusion.pipeline import run

            try:
                report = run(ext["settings"], incremental=True)
                ext["pipeline_state"].update(etat="termine", message=f"exécution {report['run_id']} en "
                                                                     f"{report['duree_totale_s']} s")
            except Exception as exc:  # noqa: BLE001 - l'erreur est restituée à l'administrateur
                ext["pipeline_state"].update(etat="echec", message=str(exc)[:300])
            finally:
                ext["pipeline_lock"].release()

        threading.Thread(target=job, daemon=True).start()
        return jsonify(ext["pipeline_state"]), 202

    @app.get("/api/pipeline")
    @login_required(role="admin")
    def pipeline_status():
        return jsonify(_ext()["pipeline_state"])

    @app.errorhandler(401)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(503)
    def api_error(err):
        if request.path.startswith("/api/"):
            return jsonify({"erreur": err.name, "detail": err.description}), err.code
        return render_template("erreur.html", code=err.code, message=err.description), err.code
