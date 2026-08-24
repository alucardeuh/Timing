"""
Simulation de charge.

La page Planning répondait à « puis-je accepter un projet de plus ? » à
condition de créer d'abord le projet. Répondre coûtait donc une ligne en
base, une entrée dans le carnet de commandes, une ligne dans les exports et
un projet fantôme à nettoyer si la réponse était non.
"""
from datetime import date, timedelta

import app as flask_app
import calculations as calc
from conftest import SETTINGS, project_data


def _lundi():
    return calc.week_monday(date.today())


def test_le_scenario_n_est_jamais_ecrit_en_base(base):
    flask_app.app.config["CSRF_PROTECT"] = False
    client = flask_app.app.test_client()

    avant = len(base.list_projects())
    reponse = client.get(f"/planning?sim=1&sim_days=3&sim_duration=6"
                         f"&sim_start={date.today().isoformat()}")

    assert reponse.status_code == 200
    assert len(base.list_projects()) == avant


def test_le_scenario_pese_sur_la_charge():
    lundi = _lundi()
    scenario = calc.scenario_project("Test", lundi.isoformat(), 5, 4)

    vide = calc.daily_capacity([], lundi, 14, True, SETTINGS)
    charge = calc.daily_capacity([scenario], lundi, 14, True, SETTINGS)

    assert max(d["pct_total"] for d in vide) == 0
    # 5 jours vendus sur 5 jours ouvrés : 100 % de chaque jour ouvré.
    assert max(d["pct_total"] for d in charge) == 100


def test_le_scenario_compte_meme_provisoires_masques():
    """Le projet simulé est compté comme signé à dessein. En provisoire, il
    disparaissait au moment même où l'on décoche les provisoires,
    c'est-à-dire quand on veut le comparer au socle certain."""
    lundi = _lundi()
    scenario = calc.scenario_project("Test", lundi.isoformat(), 5, 4)

    charge = calc.daily_capacity([scenario], lundi, 14, False, SETTINGS)
    assert max(d["pct_total"] for d in charge) == 100


def test_l_impact_compte_les_semaines_qui_basculent():
    lundi = _lundi()
    scenario = calc.scenario_project("Test", lundi.isoformat(), 5, 4)

    avant = calc.allocation_grid([], lundi, 4, SETTINGS)
    apres = calc.allocation_grid([scenario], lundi, 4, SETTINGS)
    impact = calc.scenario_impact(avant, apres)

    assert impact["free_before"] > impact["free_after"]
    assert impact["over_after"] == 0        # 5 j sur 5 j disponibles : ça tient
    assert impact["feasible"] is True


def test_l_impact_dit_non_quand_ca_ne_tient_pas():
    lundi = _lundi()
    scenario = calc.scenario_project("Trop gros", lundi.isoformat(), 7, 4)

    avant = calc.allocation_grid([], lundi, 4, SETTINGS)
    apres = calc.allocation_grid([scenario], lundi, 4, SETTINGS)
    impact = calc.scenario_impact(avant, apres)

    assert impact["newly_over"] > 0
    assert impact["feasible"] is False


def test_un_scenario_illisible_ne_casse_pas_la_page(base):
    """Les paramètres viennent de l'URL, donc de n'importe où. Une valeur
    absurde doit produire une phrase, pas une page 500."""
    flask_app.app.config["CSRF_PROTECT"] = False
    client = flask_app.app.test_client()

    reponse = client.get("/planning?sim=1&sim_days=beaucoup&sim_duration=6")
    assert reponse.status_code == 200
    assert "nombre" in reponse.data.decode()


def test_le_scenario_ne_pointe_pas_vers_une_fiche_inexistante(base):
    """Le projet simulé n'a pas d'id en base : lui coller un lien vers
    /projects/-1 produisait une 404 au premier clic."""
    flask_app.app.config["CSRF_PROTECT"] = False
    base.create_project(project_data(start_date=date.today().isoformat()))
    client = flask_app.app.test_client()

    html = client.get(f"/planning?sim=1&sim_days=2&sim_duration=6"
                      f"&sim_start={date.today().isoformat()}").data.decode()

    assert "/projects/-1" not in html
    assert "simulation" in html


def test_le_scenario_respecte_les_absences():
    """Un scénario qui ignorerait les congés déclarés promettrait des jours
    qui n'existent pas."""
    lundi = _lundi()
    scenario = calc.scenario_project("Test", lundi.isoformat(), 5, 2)
    absences = [{"label": "Congés", "kind": "conges",
                 "start_date": lundi.isoformat(),
                 "end_date": (lundi + timedelta(days=4)).isoformat()}]

    grille = calc.allocation_grid([scenario], lundi, 2, SETTINGS, absences)
    assert grille["totals"][0]["capacity"] == 0
    assert grille["totals"][0]["booked"] == 0
