"""
Protections des routes qui modifient l'état.
"""
from conftest import project_data

import app as flask_app


def client(csrf=True):
    flask_app.app.config["CSRF_PROTECT"] = csrf
    return flask_app.app.test_client()


def test_post_sans_jeton_rejete(base):
    """Une page ouverte dans le même navigateur peut poster vers
    127.0.0.1:5062 sans déclencher de préflight CORS. Supprimer une entrée,
    une absence, un jalon ou un coût n'était protégé par rien."""
    pid = base.create_project(project_data())
    entry_id = base.create_entry(pid, "2026-08-20", 50, 3.5)

    response = client(csrf=True).post(f"/entries/{entry_id}/delete")

    assert response.status_code == 400
    assert base.count_entries(pid) == 1  # rien supprimé


def test_post_avec_jeton_accepte(base):
    pid = base.create_project(project_data())
    entry_id = base.create_entry(pid, "2026-08-20", 50, 3.5)

    c = client(csrf=True)
    with c.session_transaction() as sess:
        sess["csrf_token"] = "jeton-de-test"
    response = c.post(f"/entries/{entry_id}/delete", data={"csrf_token": "jeton-de-test"})

    assert response.status_code in (302, 303)
    assert base.count_entries(pid) == 0


def test_redirection_externe_refusee(base):
    """`next` était utilisé sans contrôle : un champ caché trafiqué pouvait
    renvoyer vers un site externe."""
    assert flask_app.safe_next("https://exemple.test/piege", "/") == "/"
    assert flask_app.safe_next("//exemple.test", "/") == "/"
    assert flask_app.safe_next("/projects/3", "/") == "/projects/3"
    assert flask_app.safe_next("", "/") == "/"


def test_secret_key_absente_des_reglages_publics(base):
    """get_settings() alimente les templates (`settings=settings` sur
    plusieurs pages) : la clé de session ne doit jamais s'y trouver, sinon
    un `{{ settings }}` ajouté par erreur l'exposerait dans le HTML."""
    base.set_setting("secret_key", "un-secret-tres-important")

    assert "secret_key" not in base.get_settings()
    assert base.get_setting_raw("secret_key") == "un-secret-tres-important"


def test_drapeaux_de_migration_absents_des_reglages_publics(base):
    """Même chose pour les indicateurs de migration : utiles en interne,
    sans intérêt (et sans sens) affichés sur la page Réglages."""
    settings = base.get_settings()
    assert "clients_backfilled" not in settings
    assert "clients_name_nocase_migrated" not in settings


def test_supprimer_une_entree_deja_supprimee_ne_ment_pas(base):
    """Un double clic, ou deux onglets ouverts sur la même fiche, peut
    envoyer deux suppressions pour la même entrée. La seconde ne doit pas
    afficher « supprimée » comme si elle avait fait quelque chose."""
    pid = base.create_project(project_data())
    entry_id = base.create_entry(pid, "2026-08-20", 50, 3.5)
    base.delete_entry(entry_id)

    response = client(csrf=False).post(f"/entries/{entry_id}/delete", follow_redirects=True)

    # Jinja échappe l'apostrophe en entité HTML : &#39;, pas '.
    assert b"n&#39;existe plus" in response.data


def test_supprimer_un_cout_deja_supprime_ne_ment_pas(base):
    pid = base.create_project(project_data())
    base.create_cost(pid, "Sous-traitance", 500, "2026-08-20", billable=False)
    cost_id = base.list_costs(pid)[0]["id"]
    base.delete_cost(cost_id)

    response = client(csrf=False).post(f"/costs/{cost_id}/delete", follow_redirects=True)

    assert b"n&#39;existe plus" in response.data


def test_supprimer_une_absence_deja_supprimee_ne_ment_pas(base):
    base.create_absence("Congés", "conges", "2026-08-20", "2026-08-21")
    absence_id = base.list_absences()[0]["id"]
    base.delete_absence(absence_id)

    response = client(csrf=False).post(f"/absences/{absence_id}/delete", follow_redirects=True)

    assert b"n&#39;existe plus" in response.data
