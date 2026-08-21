"""
Le bug le plus sournois de la V1 : changer le réglage "heures par jour"
recalculait tout l'historique de consommation, parce que les jours étaient
dérivés des heures figées à la saisie.
"""
import calculations as calc
from conftest import SETTINGS, agg, make_project


def test_jours_independants_du_reglage_horaire(base):
    """20 jours consommés restent 20 jours, que la journée fasse 7 h ou 8 h.

    En V1, une entrée de 50 % enregistrée à 3,5 h devenait 0,4375 jour après
    un passage à 8 h/jour — soit 12,5 % de consommation évaporée sur tout
    l'historique, sans rien toucher aux données.
    """
    project = make_project()
    a = agg(2000, count=20)

    sept = calc.project_stats(project, a, {**SETTINGS, "default_hours_per_day": 7.0})
    huit = calc.project_stats(project, a, {**SETTINGS, "default_hours_per_day": 8.0})

    assert sept["days_spent"] == huit["days_spent"] == 20.0
    assert sept["pct_consumed"] == huit["pct_consumed"] == 50.0


def test_heures_restent_derivees_pour_affichage(base):
    """Les heures, elles, suivent bien le réglage — c'est leur rôle."""
    project = make_project()
    a = agg(2000, count=20)

    assert calc.project_stats(project, a, {**SETTINGS, "default_hours_per_day": 7.0})["hours_spent"] == 140.0
    assert calc.project_stats(project, a, {**SETTINGS, "default_hours_per_day": 8.0})["hours_spent"] == 160.0


def test_jours_vendus(base):
    """5 j/semaine sur 8 semaines = 40 jours."""
    assert calc.total_days_sold(make_project()) == 40.0


def test_duree_en_mois(base):
    """2 mois = 8,69 semaines (52/12), pas 8."""
    project = make_project(duration_value=2, duration_unit="months", days_per_week=1)
    assert calc.total_days_sold(project) == 8.69
