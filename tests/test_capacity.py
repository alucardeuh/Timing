"""
La carte de charge est l'élément central de l'app. Ces tests figent la
correction la plus importante de la V2 : le diviseur est le nombre de jours
ouvrés, pas 7.
"""
from datetime import date, timedelta

from conftest import SETTINGS, make_project

import calculations as calc


def monday():
    return calc.week_monday(date.today())


def test_projet_plein_temps_remplit_les_jours_ouvres(base):
    """5 jours vendus sur 5 jours travaillés = 100 % du lundi au vendredi.

    C'est LE test de la correction : en V1 le calcul divisait par 7 et
    affichait 71,4 % — un projet qui prenait tout le temps disponible
    passait pour « léger ».
    """
    project = make_project(start_date=(monday() - timedelta(days=30)).isoformat(),
                           duration_value=20)
    cap = calc.daily_capacity([project], monday(), 7, True, SETTINGS)
    ouvres = [d for d in cap if d["level"] != "off"]

    assert len(ouvres) == 5
    assert all(d["pct_total"] == 100.0 for d in ouvres)


def test_week_end_non_charge(base):
    project = make_project(start_date=(monday() - timedelta(days=30)).isoformat(),
                           duration_value=20)
    cap = calc.daily_capacity([project], monday(), 7, True, SETTINGS)

    samedi, dimanche = cap[5], cap[6]
    assert samedi["level"] == "off" and samedi["pct_total"] == 0
    assert dimanche["level"] == "off" and dimanche["off_reason"] == "week-end"


def test_semaine_de_quatre_jours(base):
    """Sur 4 jours travaillés, 4 jours vendus font aussi 100 % : le
    diviseur suit le réglage, il n'est pas codé en dur."""
    settings = {**SETTINGS, "working_days": "0,1,2,3"}
    project = make_project(days_per_week=4,
                           start_date=(monday() - timedelta(days=30)).isoformat(),
                           duration_value=20)
    cap = calc.daily_capacity([project], monday(), 7, True, settings)
    ouvres = [d for d in cap if d["level"] != "off"]

    assert len(ouvres) == 4
    assert all(d["pct_total"] == 100.0 for d in ouvres)


def test_surcharge_detectee(base):
    """Deux projets à 3 j/semaine = 120 % : au-dessus du seuil tempête.
    En V1 le même cas donnait 85,7 % et passait pour simplement 'chargé'."""
    a = make_project(days_per_week=3, start_date=(monday() - timedelta(days=30)).isoformat(), duration_value=20)
    b = make_project(id=2, days_per_week=3, start_date=(monday() - timedelta(days=30)).isoformat(), duration_value=20)
    cap = calc.daily_capacity([a, b], monday(), 7, True, SETTINGS)
    lundi = cap[0]

    assert lundi["pct_total"] == 120.0
    assert lundi["level"] == "storm"


def test_conges_annulent_la_charge(base):
    project = make_project(start_date=(monday() - timedelta(days=30)).isoformat(), duration_value=20)
    absences = [{"label": "Vacances", "kind": "conges",
                 "start_date": monday().isoformat(),
                 "end_date": (monday() + timedelta(days=2)).isoformat()}]
    cap = calc.daily_capacity([project], monday(), 7, True, SETTINGS, absences)

    assert [d["level"] for d in cap[:3]] == ["off", "off", "off"]
    assert cap[0]["off_reason"] == "Vacances"
    assert cap[3]["pct_total"] == 100.0  # jeudi reste chargé


def test_provisoire_exclu_sur_demande(base):
    project = make_project(status="provisional",
                           start_date=(monday() - timedelta(days=30)).isoformat(), duration_value=20)
    avec = calc.daily_capacity([project], monday(), 7, True, SETTINGS)
    sans = calc.daily_capacity([project], monday(), 7, False, SETTINGS)

    assert avec[0]["pct_total"] == 100.0
    assert sans[0]["pct_total"] == 0.0


def test_cadence_dans_les_clous_nest_pas_en_avance(base):
    """delta = 0 signifie « exactement dans les clous », pas « en avance ».

    L'ancien seuil (delta <= 5 → ahead) mentait dans le sens rassurant,
    ce qui est le pire sens pour un outil d'alerte.
    """
    from datetime import date, timedelta

    from conftest import agg, make_project

    import calculations as calc

    # 50 % de budget consommé, 50 % de temps écoulé
    project = make_project(start_date=(date.today() - timedelta(days=28)).isoformat())
    assert calc.pace_status(project, agg(2000, count=20)) == "on_track"


def test_cadence_tendue(base):
    """delta = 15 → « tendu », un niveau qui n'existait pas et qui était
    présenté comme normal."""
    from datetime import date, timedelta

    from conftest import agg, make_project

    import calculations as calc

    project = make_project(start_date=(date.today() - timedelta(days=28)).isoformat())
    # 65 % consommé pour 50 % écoulé → delta = 15
    assert calc.pace_status(project, agg(2600, count=26)) == "tight"


def test_projet_en_depassement_porte_encore_sa_charge(base):
    """Un projet confirmé dont la fin planifiée est passée continue
    d'occuper des journées.

    Avant, project_is_active_on s'arrêtait à planned_end_date : un projet
    consommé à 130 %, deux semaines après sa fin prévue, portait 0 % de
    charge — exactement au moment où il mange les journées à venir. La carte
    était purement contractuelle et ignorait le réel.
    """
    from datetime import date, timedelta

    from conftest import SETTINGS, make_project

    import calculations as calc

    # Projet de 4 semaines démarré il y a 6 semaines : fini depuis 2 semaines
    project = make_project(days_per_week=5, duration_value=4,
                           start_date=(date.today() - timedelta(days=42)).isoformat())
    lundi = calc.week_monday(date.today())
    cap = calc.daily_capacity([project], lundi, 7, True, {**SETTINGS, "overrun_weeks": 4})
    ouvres = [d for d in cap if d["level"] != "off"]

    assert ouvres[0]["pct_total"] == 100.0
    assert ouvres[0]["has_overrun"] is True


def test_projet_termine_ne_porte_plus_rien(base):
    """La prolongation ne concerne que les projets encore vivants : marquer
    terminé doit libérer la charge immédiatement."""
    from datetime import date, timedelta

    from conftest import SETTINGS, make_project

    import calculations as calc

    project = make_project(status="completed", days_per_week=5, duration_value=4,
                           start_date=(date.today() - timedelta(days=42)).isoformat())
    lundi = calc.week_monday(date.today())
    cap = calc.daily_capacity([project], lundi, 7, True, SETTINGS)

    assert all(d["pct_total"] == 0 for d in cap)


def test_prolongation_plafonnee(base):
    """Au-delà du plafond, un projet oublié cesse de polluer le planning."""
    from datetime import date, timedelta

    from conftest import SETTINGS, make_project

    import calculations as calc

    # Fini depuis 10 semaines, plafond à 4
    project = make_project(days_per_week=5, duration_value=4,
                           start_date=(date.today() - timedelta(days=98)).isoformat())
    lundi = calc.week_monday(date.today())
    cap = calc.daily_capacity([project], lundi, 7, True, {**SETTINGS, "overrun_weeks": 4})

    assert all(d["pct_total"] == 0 for d in cap)


def test_jours_declares_concentrent_la_charge(base):
    """Un projet vendu 2 j/semaine sur lundi et mercredi doit remplir CES
    deux jours, pas s'étaler à 40 % sur les cinq jours ouvrés.

    Le lissage reste le défaut (on ne sait pas toujours quels jours), mais
    il produisait un planning illisible : « ce projet me prend 40 % de tous
    mes jours » n'aide pas à décider quand caser le suivant.
    """
    from datetime import date, timedelta

    from conftest import SETTINGS, make_project

    import calculations as calc

    lundi = calc.week_monday(date.today())
    project = make_project(days_per_week=2, duration_value=20, weekdays="0,2",
                           start_date=(lundi - timedelta(days=7)).isoformat())
    cap = calc.daily_capacity([project], lundi, 7, True, SETTINGS)

    assert cap[0]["pct_total"] == 100.0  # lundi
    assert cap[1]["pct_total"] == 0.0    # mardi
    assert cap[2]["pct_total"] == 100.0  # mercredi


def test_sans_jours_declares_la_charge_est_lissee(base):
    from datetime import date, timedelta

    from conftest import SETTINGS, make_project

    import calculations as calc

    lundi = calc.week_monday(date.today())
    project = make_project(days_per_week=2, duration_value=20,
                           start_date=(lundi - timedelta(days=7)).isoformat())
    cap = calc.daily_capacity([project], lundi, 7, True, SETTINGS)

    assert [d["pct_total"] for d in cap[:5]] == [40.0] * 5


def test_grille_allocation_compare_engage_et_disponible(base):
    """La ligne de bas de grille doit dire s'il reste de la place."""
    from datetime import date, timedelta

    from conftest import SETTINGS, make_project

    import calculations as calc

    lundi = calc.week_monday(date.today())
    a = make_project(days_per_week=2, duration_value=20,
                     start_date=(lundi - timedelta(days=7)).isoformat())
    b = make_project(id=2, days_per_week=4, duration_value=20,
                     start_date=(lundi - timedelta(days=7)).isoformat())
    grid = calc.allocation_grid([a, b], lundi, 2, SETTINGS)

    premiere = grid["totals"][0]
    assert premiere["capacity"] == 5      # 5 jours ouvrés
    assert premiere["booked"] == 6.0      # 2 + 4 jours engagés
    assert premiere["free"] == -1.0
    assert premiere["over"] is True


def test_fin_planifiee_est_une_borne_incluse(base):
    """Une semaine vendue = lundi → dimanche, pas lundi → lundi suivant.

    planned_end_date renvoyait `start + semaines * 7`, c'est-à-dire le
    LENDEMAIN de la fin. Un projet d'une semaine démarré le lundi 2 mars
    finissait le lundi 9 : six jours ouvrés de charge pour cinq jours
    vendus, une barre Gantt trop longue, et l'alerte « fin prévue dépassée »
    décalée d'un jour.
    """
    from datetime import date

    import calculations as calc

    project = {"start_date": "2026-03-02", "duration_value": 1,
               "duration_unit": "weeks", "days_per_week": 5}

    assert calc.planned_end_date(project) == date(2026, 3, 8)  # dimanche


def test_une_semaine_vendue_occupe_cinq_jours_ouvres(base):
    """Corollaire du précédent, côté carte de charge : c'est le symptôme
    qu'on voyait réellement à l'écran."""
    from datetime import timedelta

    from conftest import SETTINGS, make_project

    import calculations as calc

    lundi = calc.week_monday(date.today())
    project = make_project(days_per_week=5, duration_value=1,
                           start_date=lundi.isoformat())
    cap = calc.daily_capacity([project], lundi, 14, True,
                              {**SETTINGS, "overrun_weeks": 0})
    charges = [d for d in cap if d["pct_total"] > 0]

    assert len(charges) == 5
    assert charges[-1]["date"] == lundi + timedelta(days=4)  # vendredi, pas lundi


def test_jours_declares_impossibles_affichent_la_surcharge(base):
    """5 jours vendus concentrés sur 2 jours déclarés = 250 %, pas 100 %.

    project_daily_pct plafonnait les jours DÉCLARÉS à 100 % (`min(..., 1.0)`)
    sans plafonner les jours lissés. Résultat : la sur-réservation était
    masquée précisément là où le planning est censé être littéral, et la
    grille d'allocation comptait 2 jours engagés là où le contrat en vendait
    5 — donc annonçait 3 jours libres qui n'existaient pas.
    """
    from datetime import timedelta

    from conftest import SETTINGS, make_project

    import calculations as calc

    lundi = calc.week_monday(date.today())
    project = make_project(days_per_week=5, duration_value=20, weekdays="0,1",
                           start_date=(lundi - timedelta(days=7)).isoformat())
    cap = calc.daily_capacity([project], lundi, 7, True, SETTINGS)

    assert cap[0]["pct_total"] == 250.0
    assert cap[0]["level"] == "storm"

    # La grille d'allocation retrouve bien les 5 jours du contrat.
    grid = calc.allocation_grid([project], lundi, 1, SETTINGS)
    assert grid["totals"][0]["booked"] == 5.0
    assert grid["totals"][0]["free"] == 0.0
