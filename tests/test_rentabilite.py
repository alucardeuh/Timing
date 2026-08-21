"""
L'indice de rentabilité affichait ×280 sur une heure saisie et remontait en
tête du classement de la page d'accueil. Ces tests figent les deux garde-fous
mis en place : seuil de fiabilité, et indice projeté pour les projets en cours.
"""
import calculations as calc
from conftest import SETTINGS, agg, make_project


def test_indice_masque_sur_une_saisie_minuscule(base):
    """Une demi-journée sur 40 jours vendus = 1,25 % de budget consommé.
    En dessous du seuil de 20 %, l'indice doit valoir None plutôt que
    d'afficher un chiffre spectaculaire et faux."""
    project = make_project()
    stats = calc.project_stats(project, agg(50), SETTINGS)

    assert stats["pct_consumed"] == 1.2
    assert stats["rentability_index"] is None
    assert stats["index_reliable"] is False


def test_indice_visible_au_dessus_du_seuil(base):
    """20 jours consommés sur 40 vendus = 50 % : au-dessus du seuil.
    Le projet a coûté deux fois moins de jours que vendu, donc ×2."""
    project = make_project()
    stats = calc.project_stats(project, agg(2000, count=20), SETTINGS)

    assert stats["pct_consumed"] == 50.0
    assert stats["rentability_index"] == 2.0


def test_projet_termine_toujours_fiable(base):
    """Un projet terminé a son chiffre définitif, quel que soit le volume
    saisi : c'est le seul cas où un faible % de consommation est une vraie
    information (le projet a coûté moins cher que prévu)."""
    project = make_project(status="completed")
    stats = calc.project_stats(project, agg(400, count=8), SETTINGS)

    assert stats["index_reliable"] is True
    assert stats["rentability_index"] is not None


def test_couts_reduisent_la_marge(base):
    """20 jours passés, 20 000 € vendus = 1000 €/jour. Avec 5 000 € de
    sous-traitance, la marge tombe à 750 €/jour."""
    project = make_project()
    sans = calc.project_stats(project, agg(2000, count=20), SETTINGS, costs=0)
    avec = calc.project_stats(project, agg(2000, count=20), SETTINGS, costs=5000)

    assert sans["real_day_rate"] == 1000.0
    assert avec["real_day_rate"] == 750.0
    assert avec["margin"] == 15000.0


def test_indice_projete_reste_lisible_tot(base):
    """Sur un projet à mi-parcours ayant consommé la moitié de son budget,
    l'indice projeté vaut ×1 : le rythme est exactement celui vendu."""
    from datetime import date, timedelta
    project = make_project(start_date=(date.today() - timedelta(days=28)).isoformat())
    stats = calc.project_stats(project, agg(2000, count=20), SETTINGS)

    assert stats["pct_time_elapsed"] == 50.0
    assert stats["projected_index"] == 1.0


def test_pace_distingue_pas_commence(base):
    """Aucune saisie ne veut pas dire 'en avance'. En V1 un projet vierge
    affichait fièrement 'en avance' alors qu'il n'avait simplement pas été
    saisi."""
    project = make_project()
    vierge = calc.project_stats(project, calc.EMPTY_AGG, SETTINGS)

    assert vierge["pace_status"] == "not_started"
