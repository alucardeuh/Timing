"""
Résilience quand la base devient indisponible en cours de requête (verrou,
disque plein, fichier corrompu). Le cas qui compte le plus est celui de la
page d'erreur elle-même : inject_globals() tourne pour CHAQUE template, y
compris error.html, via current_settings(). Sans repli, une panne de base
faisait échouer le gestionnaire d'erreur 500 en tentant de relire les
réglages — la personne se retrouvait avec l'écran brut de Werkzeug plutôt
qu'avec un message compréhensible.
"""
from conftest import project_data

import app as flask_app
import db


def test_page_erreur_500_survit_a_une_base_indisponible(base, monkeypatch):
    import sqlite3

    pid = base.create_project(project_data())

    def panne(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "get_db", panne)

    flask_app.app.config["CSRF_PROTECT"] = False
    client = flask_app.app.test_client()
    response = client.get(f"/projects/{pid}")

    assert response.status_code == 500
    assert "mal passé".encode() in response.data
    # Preuve que la page d'erreur a bien fini de s'afficher, et pas planté
    # une seconde fois en cours de rendu.
    assert response.data.strip().endswith(b"</html>")


def test_current_settings_retombe_sur_les_reglages_par_defaut(base, monkeypatch):
    """Test plus direct de la fonction elle-même, hors gestionnaire
    d'erreur : si get_settings() lève, current_settings() ne doit pas
    propager, et doit renvoyer des valeurs exploitables par le reste du
    code (float là où FLOAT_SETTINGS l'attend)."""
    def panne():
        raise RuntimeError("base indisponible")

    monkeypatch.setattr(db, "get_settings", panne)

    with flask_app.app.test_request_context("/"):
        settings = flask_app.current_settings()

    assert settings["currency_symbol"] == db.DEFAULT_SETTINGS["currency_symbol"]
    assert isinstance(settings["default_hours_per_day"], float)
