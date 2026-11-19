"""Tableau de bord : authentification, rôles, sécurité HTTP et API gold."""

from __future__ import annotations

import json
import re

import pytest

from fusion.config import Settings
from fusion.lake import LocalLake
from fusion.web import LoginThrottle, create_app, load_accounts

GOLD = {
    "date_reference": "2026-10-31",
    **{s: {"kpi": {"ok": s}} for s in ("synthese", "clients", "contrats", "sinistres", "finances", "doublons", "qualite")},
}


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("FUSION_ADMIN_PASSWORD", "admin-test-1")
    monkeypatch.setenv("FUSION_USER_PASSWORD", "user-test-1")
    settings = Settings(data_dir=tmp_path)
    settings.gold_path.parent.mkdir(parents=True)
    settings.gold_path.write_text(json.dumps(GOLD), encoding="utf-8")
    LocalLake(settings.lake_dir).write_table("gold", "paires_doublons_detail", [
        {"uid_a": "AB-1", "uid_b": "AP-2", "niveau": "PROBABLE", "score": 0.5},
        {"uid_a": "AB-3", "uid_b": "AP-4", "niveau": "CERTAIN", "score": 0.9},
    ])
    application = create_app(settings)
    application.config["TESTING"] = True
    return application


def _csrf(client, path="/connexion"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def _login(client, user, pwd):
    return client.post("/connexion", data={"identifiant": user, "mot_de_passe": pwd, "csrf": _csrf(client),
                                            "suivant": "/"})


def test_redirection_vers_la_connexion(app):
    c = app.test_client()
    r = c.get("/")
    assert r.status_code == 302 and "/connexion" in r.headers["Location"]
    assert c.get("/api/gold/clients").status_code == 401


def test_connexion_refusee_sans_csrf(app):
    r = app.test_client().post("/connexion", data={"identifiant": "admin", "mot_de_passe": "admin-test-1"})
    assert r.status_code == 400


def test_mauvais_mot_de_passe(app):
    r = _login(app.test_client(), "admin", "faux")
    assert r.status_code == 401 and "incorrect" in r.get_data(as_text=True)


def test_limitation_des_tentatives(app):
    c = app.test_client()
    for _ in range(5):
        _login(c, "admin", "faux")
    r = _login(c, "admin", "admin-test-1")
    assert r.status_code == 429


def test_utilisateur_voit_les_statistiques_sans_donnees_personnelles(app):
    c = app.test_client()
    assert _login(c, "analyste", "user-test-1").status_code == 302
    page = c.get("/").get_data(as_text=True)
    assert "Répartition des clients" in page and 'data-section="qualite"' not in page
    assert c.get("/api/gold/clients").get_json()["clients"]["kpi"]["ok"] == "clients"
    assert c.get("/api/gold/qualite").status_code == 403
    assert c.get("/api/doublons/paires").status_code == 403
    assert c.get("/api/gold/inexistant").status_code == 404
    assert c.get("/api/session").get_json()["role"] == "user"


def test_administrateur(app):
    c = app.test_client()
    _login(c, "ADMIN ", "admin-test-1")  # identifiant insensible à la casse et aux espaces
    assert 'data-section="qualite"' in c.get("/").get_data(as_text=True)
    assert c.get("/api/gold/qualite").status_code == 200
    pairs = c.get("/api/doublons/paires?niveau=PROBABLE").get_json()
    assert pairs["total"] == 1 and pairs["paires"][0]["uid_a"] == "AB-1"
    assert c.get("/api/doublons/paires").get_json()["total"] == 2


def test_relance_du_pipeline_protegee(app, monkeypatch):
    c = app.test_client()
    _login(c, "admin", "admin-test-1")
    assert c.post("/api/pipeline").status_code == 400  # jeton CSRF absent
    calls = []

    def fake_run(settings, incremental=True):
        calls.append(incremental)
        return {"run_id": "r1", "duree_totale_s": 0.1}

    monkeypatch.setattr("fusion.pipeline.run", fake_run)
    token = _csrf(c, "/")
    r = c.post("/api/pipeline", headers={"X-CSRF-Token": token})
    assert r.status_code == 202
    import time

    for _ in range(50):
        if c.get("/api/pipeline").get_json()["etat"] != "en_cours":
            break
        time.sleep(0.02)
    assert c.get("/api/pipeline").get_json()["etat"] == "termine" and calls == [True]


def test_echec_du_pipeline_restitue(app, monkeypatch):
    c = app.test_client()
    _login(c, "admin", "admin-test-1")

    def boom(settings, incremental=True):
        raise RuntimeError("réconciliation en échec")

    monkeypatch.setattr("fusion.pipeline.run", boom)
    c.post("/api/pipeline", headers={"X-CSRF-Token": _csrf(c, "/")})
    import time

    for _ in range(50):
        state = c.get("/api/pipeline").get_json()
        if state["etat"] != "en_cours":
            break
        time.sleep(0.02)
    assert state["etat"] == "echec" and "réconciliation" in state["message"]


def test_entetes_de_securite_et_deconnexion(app):
    c = app.test_client()
    _login(c, "admin", "admin-test-1")
    r = c.get("/")
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    assert c.get("/api/gold/synthese").headers["Cache-Control"] == "no-store"
    c.post("/deconnexion", data={"csrf": _csrf(c, "/")})
    assert c.get("/api/gold/synthese").status_code == 401


def test_redirection_ouverte_refusee(app):
    c = app.test_client()
    r = c.post("/connexion", data={"identifiant": "admin", "mot_de_passe": "admin-test-1", "csrf": _csrf(c),
                                   "suivant": "//evil.example/"})
    assert r.headers["Location"] == "/"


def test_sante_et_gold_absent(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), accounts=load_accounts())
    c = app.test_client()
    assert c.get("/api/sante").get_json() == {"statut": "ok", "version": "1.0.0", "gold": False}
    _login(c, "admin", "Admin-Fusion-2026")
    assert "zone gold est vide" in c.get("/").get_data(as_text=True)
    assert c.get("/api/gold/clients").status_code == 503
    assert c.get("/connexion").status_code == 302  # déjà connecté
    assert c.get("/inconnue").status_code == 404


def test_throttle_fenetre_glissante(monkeypatch):
    t = LoginThrottle(max_attempts=2, window_s=10)
    now = [100.0]
    monkeypatch.setattr("fusion.web.time.monotonic", lambda: now[0])
    t.fail("ip")
    t.fail("ip")
    assert t.blocked("ip")
    now[0] = 111.0
    assert not t.blocked("ip")
