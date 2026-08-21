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


def test_indice_projete_a_mi_parcours(base):
    """Projet à mi-parcours ayant consommé la moitié de son budget : le
    rythme est celui vendu, l'indice projeté tourne autour de ×1.

    L'extrapolation se fait en JOURS OUVRÉS, pas en jours calendaires — d'où
    un écart de quelques centièmes selon la position des week-ends. C'est
    voulu : projeter sur du calendrier alors qu'on ne consomme que les jours
    ouvrés décalait la projection d'un facteur 7/5.
    """
    from datetime import date, timedelta
    project = make_project(start_date=(date.today() - timedelta(days=28)).isoformat())
    stats = calc.project_stats(project, agg(2000, count=20), SETTINGS)

    assert stats["pct_time_elapsed"] == 50.0
    assert 0.95 <= stats["projected_index"] <= 1.05


def test_indice_projete_muet_au_tout_debut(base):
    """À J+2 avec une demi-journée saisie, l'indice projeté doit valoir None.

    Il divisait par un temps écoulé de ~2 %, ce qui le rendait plus volatil
    que l'indice qu'il était censé remplacer : une demi-journée de plus
    faisait passer de ×2,88 à ×0,96.
    """
    from datetime import date, timedelta
    start = (date.today() - timedelta(days=2)).isoformat()
    project = make_project(start_date=start, duration_value=12)
    stats = calc.project_stats(project, agg(50, count=1, first_date=start), SETTINGS)

    assert stats["projected_index"] is None


def test_taux_reel_masque_sous_le_seuil(base):
    """Le garde-fou de fiabilité doit couvrir TOUS les taux réels.

    L'indice était masqué mais « Marge par jour passé » affichait 40 000 €
    juste à côté de l'encadré expliquant pourquoi l'indice ne l'était pas.
    """
    project = make_project()
    stats = calc.project_stats(project, agg(50), SETTINGS)

    assert stats["rentability_index"] is None
    assert stats["real_day_rate"] is None
    assert stats["real_hourly_rate"] is None


def test_taux_reel_visible_sur_projet_termine(base):
    """Un projet terminé a son chiffre définitif, même sous le seuil."""
    project = make_project(status="completed")
    stats = calc.project_stats(project, agg(400, count=8), SETTINGS)

    assert stats["real_day_rate"] is not None


def test_pace_distingue_pas_commence(base):
    """Aucune saisie ne veut pas dire 'en avance'. En V1 un projet vierge
    affichait fièrement 'en avance' alors qu'il n'avait simplement pas été
    saisi."""
    project = make_project()
    vierge = calc.project_stats(project, calc.EMPTY_AGG, SETTINGS)

    assert vierge["pace_status"] == "not_started"


def test_cout_de_revient_journalier(base):
    """24 000 € de charges sur 180 jours facturables = 133,33 €/jour."""
    settings = {**SETTINGS, "annual_fixed_costs": 24000.0, "billable_days_per_year": 180.0}
    assert calc.cost_day_rate(settings) == 133.33


def test_marge_reelle_deduit_le_cout_du_temps(base):
    """« Prix − coûts directs » n'est pas une marge : ça ignore le coût de
    ton propre temps. Sans coût de revient, aucun indice ne dit si tu gagnes
    de l'argent — seulement si tu tiens ton budget de jours."""
    settings = {**SETTINGS, "annual_fixed_costs": 24000.0, "billable_days_per_year": 180.0}
    project = make_project()  # 20 000 €
    stats = calc.project_stats(project, agg(2000, count=20), settings, costs=2000)

    assert stats["net_of_costs"] == 18000.0        # prix − coûts directs
    assert stats["cost_of_days"] == 2666.6         # 20 jours × 133,33
    assert stats["real_margin"] == 15333.4


def test_marge_reelle_absente_sans_reglage(base):
    """Tant que les charges ne sont pas renseignées, on n'invente pas un
    chiffre : on affiche « net de coûts directs » et on le dit."""
    project = make_project()
    stats = calc.project_stats(project, agg(2000, count=20), SETTINGS, costs=2000)

    assert stats["real_margin"] is None
    assert stats["net_of_costs"] == 18000.0
