"""
Protections des routes qui modifient l'état.
"""
import app as flask_app
from conftest import project_data


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
