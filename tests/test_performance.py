"""
Une connexion SQLite par requête, pas une par fonction appelée.

Avant ce correctif, chaque fonction de db.py ouvrait et refermait sa propre
connexion : afficher la fiche d'un projet en ouvrait quatorze d'affilée,
chacune refaisant le mkdir et les PRAGMA de départ. get_db() partage
maintenant une connexion unique via flask.g pour la durée de la requête, et
db.close_db_for_request() la referme au teardown.
"""
import sqlite3

from conftest import project_data

import app as flask_app
import db


def _count_connections(callable_):
    """Compte les sqlite3.connect() réellement ouverts pendant l'appel."""
    original = sqlite3.connect
    count = {"n": 0}

    def counting(*a, **kw):
        count["n"] += 1
        return original(*a, **kw)

    sqlite3.connect = counting
    try:
        callable_()
    finally:
        sqlite3.connect = original
    return count["n"]


def test_une_page_qui_appelle_plusieurs_fonctions_db_ouvre_une_seule_connexion(base):
    """Le dashboard à lui seul appelle une dizaine de fonctions db.*.

    Avant : une connexion par appel (13 mesurées en pratique sur cette
    page). Après : une seule, réutilisée du début à la fin de la requête.
    """
    base.create_project(project_data())
    flask_app.app.config["CSRF_PROTECT"] = False
    client = flask_app.app.test_client()

    n = _count_connections(lambda: client.get("/"))

    assert n == 1


def test_la_connexion_partagee_est_fermee_apres_la_requete(base):
    """Le teardown doit vider `g`, sinon la connexion fuit d'une requête à
    l'autre — ou pire, une requête suivante hérite d'une connexion fermée
    et toute la page tombe en erreur."""
    flask_app.app.config["CSRF_PROTECT"] = False
    client = flask_app.app.test_client()

    with flask_app.app.test_request_context("/"):
        conn = db.get_db()
        assert conn is db.get_db()  # même connexion, deux appels
        flask_app.app.do_teardown_appcontext()

    # Une requête suivante ne doit pas hériter d'un état quelconque : elle
    # doit fonctionner normalement, preuve que rien n'a fui.
    assert client.get("/").status_code == 200


def test_appel_hors_contexte_flask_garde_l_ancien_comportement(base):
    """Les tests et scripts appellent db.* sans requête Flask en cours :
    chaque appel doit garder sa connexion jetable, comme avant ce
    correctif — sinon toute la suite de tests casserait."""
    n = _count_connections(lambda: (
        base.list_projects(), base.list_client_records(), base.get_settings(),
    ))
    assert n == 3


def test_daily_capacity_ne_reparse_pas_les_dates_par_jour(base, monkeypatch):
    """project_is_active_on et planned_end_date étaient rappelés pour
    CHAQUE jour de la fenêtre, pour chaque projet — sur 56 jours, 56 fois
    plus de travail que nécessaire pour une valeur qui ne change pas d'un
    jour à l'autre pour un même projet. Le profil de charge doit être
    précalculé une fois par projet, pas une fois par (jour, projet)."""
    from datetime import date, timedelta

    from conftest import SETTINGS, make_project

    import calculations as calc

    lundi = calc.week_monday(date.today())
    projects = [make_project(id=i, start_date=(lundi - timedelta(days=7)).isoformat(),
                             duration_value=20)
               for i in range(5)]

    calls = {"n": 0}
    original = calc.planned_end_date

    def counting(project):
        calls["n"] += 1
        return original(project)

    monkeypatch.setattr(calc, "planned_end_date", counting)

    window_days = 56
    calc.daily_capacity(projects, lundi, window_days, True, SETTINGS)

    # Une fois par projet (dans le profil), pas une fois par jour de la
    # fenêtre : sans l'optimisation, ce compteur atteindrait 5 × 56 = 280.
    assert calls["n"] == len(projects)
