"""
Couche données : validation des statuts, corbeille, historique de périmètre,
grille hebdo, et migrations. Ce sont les garde-fous qui manquaient en V1,
où un statut arbitraire pouvait entrer en base et faire planter l'affichage.
"""
import pytest

from conftest import project_data


def test_statut_invalide_refuse(base):
    pid = base.create_project(project_data())
    with pytest.raises(ValueError):
        base.set_project_status(pid, "n_importe_quoi")
    assert base.get_project(pid)["status"] == "confirmed"


def test_corbeille_puis_restauration(base):
    pid = base.create_project(project_data())
    base.archive_project(pid, True)

    assert base.list_projects() == []
    assert len(base.list_projects(archived=True)) == 1
    assert base.counts_by_status()["trash"] == 1

    base.archive_project(pid, False)
    assert len(base.list_projects()) == 1


def test_changement_de_perimetre_journalise(base):
    """Passer un projet de 40 à 50 jours vendus doit laisser une trace :
    sans elle, impossible d'analyser ses dépassements après coup."""
    pid = base.create_project(project_data())
    changed = base.update_project(pid, project_data(duration_value=10),
                                  scope_note="avenant signé")

    assert changed == ["Durée"]
    trace = base.list_scope_changes(pid)
    assert len(trace) == 1
    assert trace[0]["old_value"] == "8" and trace[0]["new_value"] == "10"
    assert trace[0]["note"] == "avenant signé"


def test_modification_sans_impact_ne_journalise_rien(base):
    pid = base.create_project(project_data())
    changed = base.update_project(pid, project_data(name="Nouveau nom", notes="hop"))

    assert changed == []
    assert base.list_scope_changes(pid) == []


def test_grille_ecrit_un_total_par_case(base):
    """Deux saisies le même jour sur la même clé, puis une modification par
    la grille : il ne doit rester qu'une entrée, à la nouvelle valeur."""
    pid = base.create_project(project_data())
    base.create_entry(pid, "2026-08-20", 30, 2.1)
    base.create_entry(pid, "2026-08-20", 20, 1.4)

    base.set_day_total(pid, None, "2026-08-20", 80, 5.6)

    entries = base.list_entries(pid)
    assert len(entries) == 1
    assert entries[0]["percent_of_day"] == 80


def test_grille_a_zero_supprime(base):
    pid = base.create_project(project_data())
    base.create_entry(pid, "2026-08-20", 50, 3.5)
    base.set_day_total(pid, None, "2026-08-20", 0, 0)

    assert base.list_entries(pid) == []


def test_suppression_tache_conserve_le_temps(base):
    """Supprimer une étiquette ne doit jamais faire disparaître du temps
    saisi — l'entrée repasse simplement en 'Sans tâche'."""
    pid = base.create_project(project_data())
    base.create_task(pid, "Maquettes")
    task_id = base.list_tasks(pid)[0]["id"]
    base.create_entry(pid, "2026-08-20", 50, 3.5, task_id=task_id)

    base.delete_task(task_id)

    entries = base.list_entries(pid)
    assert len(entries) == 1
    assert entries[0]["task_id"] is None


def test_suppression_projet_efface_ses_donnees(base):
    """Là au contraire, la cascade doit fonctionner : plus de projet, plus
    de saisies orphelines."""
    pid = base.create_project(project_data())
    base.create_entry(pid, "2026-08-20", 50, 3.5)
    base.create_milestone(pid, "Acompte", 5000, "2026-09-01")

    base.delete_project_forever(pid)

    assert base.list_entries(pid) == []
    assert base.list_milestones(pid) == []


def test_jalon_paye_recoit_une_date_de_facturation(base):
    """Passer directement de 'à facturer' à 'encaissé' ne doit pas laisser
    de trou dans l'historique de CA mensuel."""
    pid = base.create_project(project_data())
    base.create_milestone(pid, "Solde", 10000, None)
    mid = base.list_milestones(pid)[0]["id"]

    base.set_milestone_status(mid, "paid")

    m = base.get_milestone(mid)
    assert m["paid_at"] and m["invoiced_at"]


def test_agregat_une_seule_requete(base):
    """L'agrégat renvoie la même chose que la somme des entrées, sans avoir
    à les charger une par une."""
    pid = base.create_project(project_data())
    base.create_entry(pid, "2026-08-18", 50, 3.5)
    base.create_entry(pid, "2026-08-19", 100, 7)

    a = base.entries_aggregate_by_project()[pid]
    assert a["percent_sum"] == 150
    assert a["entries_count"] == 2
    assert a["first_date"] == "2026-08-18"


def test_init_db_est_idempotent(base):
    """Relancer les migrations sur une base déjà à jour ne doit rien casser
    — c'est ce qui tourne à chaque démarrage de l'app."""
    pid = base.create_project(project_data())
    base.init_db()
    base.init_db()

    assert base.get_project(pid) is not None


def test_corbeille_sort_des_agregats_de_ca(base):
    """Un projet en corbeille disparaissait de la page Facturation mais
    continuait à peser dans « Facturé ce mois » : deux pages affichaient des
    totaux qui ne se recoupaient pas."""
    pid = base.create_project(project_data())
    base.create_milestone(pid, "Acompte", 5000, None)
    mid = base.list_milestones(pid)[0]["id"]
    base.set_milestone_status(mid, "invoiced", "F-001")

    assert base.invoiced_between("2000-01-01", "2100-01-01") == 5000

    base.archive_project(pid, True)

    assert base.invoiced_between("2000-01-01", "2100-01-01") == 0
    assert base.monthly_invoiced() == []


def test_sauvegarde_contient_les_ecritures_recentes(base):
    """En mode WAL, envoyer le fichier principal seul pouvait produire une
    sauvegarde en retard sur la base réelle."""
    import sqlite3
    import tempfile

    pid = base.create_project(project_data())
    base.create_entry(pid, "2026-08-20", 50, 3.5)

    handle = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    handle.close()
    base.backup_to(handle.name)

    conn = sqlite3.connect(handle.name)
    count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    conn.close()

    assert count == 1


def test_recherche_echappe_les_jokers(base):
    """Un « % » ou un « _ » tapé par l'utilisateur agissait comme joker et
    la recherche renvoyait n'importe quoi."""
    base.create_project(project_data(name="Refonte 100%"))
    base.create_project(project_data(name="Refonte AB"))

    assert [p["name"] for p in base.list_projects(search="100%")] == ["Refonte 100%"]


def test_antidater_une_facturation(base):
    """Marquer le 3 août un jalon facturé le 28 juillet rangeait le CA dans
    le mauvais mois, sans moyen de corriger."""
    pid = base.create_project(project_data())
    base.create_milestone(pid, "Acompte", 5000, None)
    mid = base.list_milestones(pid)[0]["id"]

    base.set_milestone_status(mid, "invoiced", "F-001", dated="2026-07-28")

    assert base.get_milestone(mid)["invoiced_at"] == "2026-07-28"
    assert {m["month"] for m in base.monthly_invoiced()} == {"2026-07"}


def test_modifier_un_jalon(base):
    pid = base.create_project(project_data())
    base.create_milestone(pid, "Acompte", 5000, "2026-09-01")
    mid = base.list_milestones(pid)[0]["id"]

    base.update_milestone(mid, "Acompte 40%", 8000, "2026-10-01")

    m = base.get_milestone(mid)
    assert (m["label"], m["amount"], m["due_date"]) == ("Acompte 40%", 8000, "2026-10-01")
