"""
Prévisionnel d'encaissement et coûts refacturables.

Toutes les données nécessaires existaient déjà (due_date, invoiced_at,
paid_at) : il manquait seulement de les croiser avec le délai de paiement
réellement constaté par client.
"""
from datetime import date

from conftest import SETTINGS, agg, make_project, project_data

import calculations as calc


def test_delai_de_paiement_calcule_sur_lhistorique(base):
    """Le délai vient du comportement constaté, pas du contrat."""
    pid = base.create_project(project_data(client="Alpha"))
    base.create_milestone(pid, "Acompte", 5000, None)
    mid = base.list_milestones(pid)[0]["id"]
    base.set_milestone_status(mid, "invoiced", "F-1", dated="2026-06-01")
    base.set_milestone_status(mid, "paid", dated="2026-07-01")

    delays = base.payment_delays()

    assert delays["Alpha"]["days"] == 30.0
    assert delays["Alpha"]["samples"] == 1


def test_projection_place_le_jalon_dans_le_bon_mois(base):
    """Une facture émise aujourd'hui avec 30 jours de délai tombe le mois
    suivant, pas ce mois-ci."""
    today = date(2026, 8, 21)
    milestones = [{
        "label": "Solde", "amount": 10000, "status": "invoiced",
        "invoiced_at": "2026-08-21", "due_date": None,
        "project_name": "P", "project_client": "Alpha",
    }]
    forecast = calc.cash_forecast(milestones, {"Alpha": {"days": 30, "samples": 3}},
                                  months=3, today=today)

    par_mois = {m["month"]: m["amount"] for m in forecast["months"]}
    assert par_mois["2026-09"] == 10000
    assert par_mois["2026-08"] == 0


def test_jalon_attendu_dans_le_passe_est_signale_en_retard(base):
    today = date(2026, 8, 21)
    milestones = [{
        "label": "Acompte", "amount": 3000, "status": "invoiced",
        "invoiced_at": "2026-05-01", "due_date": None,
        "project_name": "P", "project_client": "Alpha",
    }]
    forecast = calc.cash_forecast(milestones, {"Alpha": {"days": 30, "samples": 2}},
                                  months=3, today=today)

    assert len(forecast["overdue"]) == 1
    assert forecast["overdue"][0]["late_days"] > 0


def test_delai_par_defaut_sans_historique(base):
    """Sans facture payée pour ce client, on applique un délai par défaut
    plutôt que de ne rien projeter."""
    today = date(2026, 8, 21)
    milestones = [{
        "label": "Solde", "amount": 4000, "status": "todo",
        "invoiced_at": None, "due_date": "2026-09-15",
        "project_name": "P", "project_client": "Inconnu",
    }]
    forecast = calc.cash_forecast(milestones, {}, months=3, today=today)

    assert forecast["total"] == 4000
    # Marqué « estimé » : repose sur une échéance, pas sur une facture émise
    items = [i for m in forecast["months"] for i in m["items"]]
    assert items[0]["estimated"] is True


def test_frais_refactures_sajoutent_au_prix(base):
    """Traiter tous les coûts comme absorbés sous-estimait le revenu des
    projets avec déplacements ou achats refacturés."""
    project = make_project()  # 20 000 €
    absorbe = calc.project_stats(project, agg(2000, count=20), SETTINGS,
                                 costs={"absorbed": 3000, "rebilled": 0})
    refacture = calc.project_stats(project, agg(2000, count=20), SETTINGS,
                                   costs={"absorbed": 0, "rebilled": 3000})

    assert absorbe["net_of_costs"] == 17000.0
    assert refacture["net_of_costs"] == 23000.0


def test_couts_separes_en_base(base):
    pid = base.create_project(project_data())
    base.create_cost(pid, "Sous-traitance", 2000, None, billable=False)
    base.create_cost(pid, "Déplacement", 500, None, billable=True)

    split = base.costs_by_project()[pid]

    assert split["absorbed"] == 2000
    assert split["rebilled"] == 500
