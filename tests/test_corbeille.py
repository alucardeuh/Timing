"""
Corbeille universelle.

La corbeille des projets protégeait les projets. Supprimer une saisie, un
jalon, un coût ou une absence restait définitif, sans confirmation ni retour
en arrière — un clic sur la mauvaise ligne de la grille hebdo effaçait une
journée sans recours.
"""
from datetime import date, timedelta

from conftest import project_data

import app as flask_app
import calculations as calc


def _client():
    flask_app.app.config["CSRF_PROTECT"] = False
    return flask_app.app.test_client()


def _projet(base):
    return base.create_project(project_data(start_date=date.today().isoformat()))


def test_une_saisie_supprimee_part_en_corbeille(base):
    pid = _projet(base)
    base.create_entry(pid, date.today().isoformat(), 100, 7)
    entry_id = base.list_entries(pid)[0]["id"]

    base.delete_entry(entry_id)

    assert base.list_entries(pid) == []
    corbeille = base.list_trash()
    assert len(corbeille) == 1
    assert corbeille[0]["kind"] == "entry"
    assert corbeille[0]["payload"]["percent_of_day"] == 100


def test_restaurer_rend_son_id_d_origine(base):
    """Réinsérer sous un nouvel id casserait tout ce qui pointe vers
    l'ancien. L'id est donc repris quand il est resté libre."""
    pid = _projet(base)
    base.create_entry(pid, date.today().isoformat(), 50, 3.5)
    entry_id = base.list_entries(pid)[0]["id"]

    trash_id = base.delete_entry(entry_id)
    nouvel_id, erreur = base.restore_trash(trash_id)

    assert erreur is None
    assert nouvel_id == entry_id
    assert base.list_entries(pid)[0]["percent_of_day"] == 50
    assert base.list_trash() == []


def test_restaurer_echoue_si_le_projet_a_disparu(base):
    """Une restauration ne doit pas fabriquer un orphelin : la saisie
    pointerait vers un projet inexistant et fausserait tous les agrégats."""
    pid = _projet(base)
    base.create_entry(pid, date.today().isoformat(), 100, 7)
    trash_id = base.delete_entry(base.list_entries(pid)[0]["id"])

    base.archive_project(pid, True)
    base.delete_project_forever(pid)

    nouvel_id, erreur = base.restore_trash(trash_id)
    assert nouvel_id is None
    assert "projet" in erreur.lower()


def test_restaurer_deux_fois_ne_ment_pas(base):
    pid = _projet(base)
    base.create_entry(pid, date.today().isoformat(), 100, 7)
    trash_id = base.delete_entry(base.list_entries(pid)[0]["id"])

    base.restore_trash(trash_id)
    nouvel_id, erreur = base.restore_trash(trash_id)

    assert nouvel_id is None
    assert erreur is not None


def test_jalon_cout_et_absence_passent_aussi_par_la_corbeille(base):
    pid = _projet(base)
    base.create_milestone(pid, "Acompte", 5000, date.today().isoformat())
    base.create_cost(pid, "Licence", 200, date.today().isoformat(), billable=False)
    base.create_absence("Congés", "conges", date.today().isoformat(),
                        (date.today() + timedelta(days=2)).isoformat())

    base.delete_milestone(base.list_milestones(pid)[0]["id"])
    base.delete_cost(base.list_costs(pid)[0]["id"])
    base.delete_absence(base.list_absences()[0]["id"])

    kinds = {item["kind"] for item in base.list_trash()}
    assert kinds == {"milestone", "cost", "absence"}


def test_un_jalon_restaure_retrouve_son_montant(base):
    pid = _projet(base)
    base.create_milestone(pid, "Solde", 12345, date.today().isoformat())
    trash_id = base.delete_milestone(base.list_milestones(pid)[0]["id"])

    base.restore_trash(trash_id)

    assert base.list_milestones(pid)[0]["amount"] == 12345


def test_la_purge_respecte_la_retention(base):
    """Une corbeille qui ne se vide jamais finit par peser plus lourd que
    les données vivantes."""
    pid = _projet(base)
    base.create_entry(pid, date.today().isoformat(), 100, 7)
    base.delete_entry(base.list_entries(pid)[0]["id"])

    assert base.purge_trash(retention_days=30) == 0
    assert len(base.list_trash()) == 1

    assert base.purge_trash(retention_days=0) == 1
    assert base.list_trash() == []


def test_la_route_de_suppression_propose_l_annulation(base):
    """Le lien d'annulation passe par la session, pas par un message flash :
    un flash est du texte échappé, et y coller du HTML aurait ouvert la
    porte à l'injection par un nom de projet."""
    pid = _projet(base)
    base.create_entry(pid, date.today().isoformat(), 100, 7)
    entry_id = base.list_entries(pid)[0]["id"]

    client = _client()
    reponse = client.post(f"/entries/{entry_id}/delete", follow_redirects=True)

    assert reponse.status_code == 200
    html = reponse.data.decode()
    assert "Annuler" in html
    assert "/corbeille/" in html


def test_la_page_corbeille_liste_et_restaure(base):
    pid = _projet(base)
    base.create_entry(pid, date.today().isoformat(), 100, 7)
    entry_id = base.list_entries(pid)[0]["id"]
    trash_id = base.delete_entry(entry_id)

    client = _client()
    assert "Corbeille" in client.get("/corbeille").data.decode()

    client.post(f"/corbeille/{trash_id}/restaurer", follow_redirects=True)
    assert len(base.list_entries(pid)) == 1


def test_les_calculs_ignorent_ce_qui_est_en_corbeille(base):
    """Une ligne en corbeille est supprimée pour de bon du point de vue des
    agrégats : la laisser peser sur la consommation ferait mentir la
    cadence pendant trente jours."""
    pid = _projet(base)
    base.create_entry(pid, date.today().isoformat(), 100, 7)
    base.delete_entry(base.list_entries(pid)[0]["id"])

    agregats = base.entries_aggregate_by_project()
    assert calc.days_spent(agregats.get(pid, calc.EMPTY_AGG)) == 0
