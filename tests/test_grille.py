"""
Grille hebdomadaire : validation des dates, atomicité de la sauvegarde, et
lignes d'affichage qui ne doivent rien écrire en base.
"""
from datetime import date

import pytest

import calculations as calc
from conftest import project_data


def test_date_invalide_nest_jamais_ecrite(base):
    """Une date malformée arrivant par un nom de champ trafiqué doit être
    rejetée avant écriture.

    Sans ça, l'entrée corrompue partait en base et burndown_points levait
    une ValueError : la fiche projet tombait en 500, or c'est la seule page
    depuis laquelle on peut supprimer cette entrée. Impasse totale.
    """
    import app as flask_app
    flask_app.app.config["CSRF_PROTECT"] = False
    client = flask_app.app.test_client()

    pid = base.create_project(project_data())
    client.post("/semaine/enregistrer", data={"offset": "0",
                                              f"cell-{pid}-none-pas-une-date": "50"})

    assert base.count_entries(pid) == 0


def test_burndown_survit_a_une_date_corrompue(base):
    """Filet de sécurité : une donnée déjà corrompue en base ne doit plus
    jamais produire un 500."""
    pid = base.create_project(project_data())
    base.set_day_total(pid, None, "2026-08-20", 50, 3.5)

    entries = [{"entry_date": "pas-une-date", "percent_of_day": 50},
               {"entry_date": "2026-08-20", "percent_of_day": 50}]
    trace = calc.burndown_points(base.get_project(pid), entries)

    assert trace["actual"]  # un tracé est produit, aucune exception


def test_sauvegarde_tout_ou_rien(base):
    """Une cellule invalide ne doit pas laisser les précédentes committées.

    L'ancienne boucle écrivait cellule par cellule : sur une erreur en 30ᵉ
    position, 29 saisies étaient déjà en base pendant que l'utilisateur
    lisait « saisie invalide ».
    """
    import app as flask_app
    flask_app.app.config["CSRF_PROTECT"] = False
    client = flask_app.app.test_client()

    pid = base.create_project(project_data())
    client.post("/semaine/enregistrer", data={
        "offset": "0",
        f"cell-{pid}-none-2026-08-17": "50",
        f"cell-{pid}-none-2026-08-18": "beaucoup",
    })

    assert base.count_entries(pid) == 0


def test_sauvegarde_valide_ecrit_tout(base):
    import app as flask_app
    flask_app.app.config["CSRF_PROTECT"] = False
    client = flask_app.app.test_client()

    pid = base.create_project(project_data())
    client.post("/semaine/enregistrer", data={
        "offset": "0",
        f"cell-{pid}-none-2026-08-17": "50",
        f"cell-{pid}-none-2026-08-18": "100",
    })

    assert base.count_entries(pid) == 2
    assert base.entries_aggregate_for(pid)["percent_sum"] == 150


def test_ajouter_une_ligne_necrit_rien(base):
    """Afficher une ligne vide dans la grille est un fait d'interface, pas
    une donnée. L'ancienne version insérait une saisie à 0,01 %, ce qui
    faisait sortir le projet de l'état « pas commencé » et éteignait
    l'alerte « jour ouvré sans saisie » pour ce lundi."""
    import app as flask_app
    flask_app.app.config["CSRF_PROTECT"] = False
    client = flask_app.app.test_client()

    pid = base.create_project(project_data())
    response = client.get(f"/semaine?extra={pid}:none")

    assert response.status_code == 200
    assert base.count_entries(pid) == 0
