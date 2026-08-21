"""
La carte de charge est l'élément central de l'app. Ces tests figent la
correction la plus importante de la V2 : le diviseur est le nombre de jours
ouvrés, pas 7.
"""
from datetime import date, timedelta

import calculations as calc
from conftest import SETTINGS, make_project


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
    import calculations as calc
    from conftest import agg, make_project

    # 50 % de budget consommé, 50 % de temps écoulé
    project = make_project(start_date=(date.today() - timedelta(days=28)).isoformat())
    assert calc.pace_status(project, agg(2000, count=20)) == "on_track"


def test_cadence_tendue(base):
    """delta = 15 → « tendu », un niveau qui n'existait pas et qui était
    présenté comme normal."""
    from datetime import date, timedelta
    import calculations as calc
    from conftest import agg, make_project

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
    import calculations as calc
    from conftest import SETTINGS, make_project

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
    import calculations as calc
    from conftest import SETTINGS, make_project

    project = make_project(status="completed", days_per_week=5, duration_value=4,
                           start_date=(date.today() - timedelta(days=42)).isoformat())
    lundi = calc.week_monday(date.today())
    cap = calc.daily_capacity([project], lundi, 7, True, SETTINGS)

    assert all(d["pct_total"] == 0 for d in cap)


def test_prolongation_plafonnee(base):
    """Au-delà du plafond, un projet oublié cesse de polluer le planning."""
    from datetime import date, timedelta
    import calculations as calc
    from conftest import SETTINGS, make_project

    # Fini depuis 10 semaines, plafond à 4
    project = make_project(days_per_week=5, duration_value=4,
                           start_date=(date.today() - timedelta(days=98)).isoformat())
    lundi = calc.week_monday(date.today())
    cap = calc.daily_capacity([project], lundi, 7, True, {**SETTINGS, "overrun_weeks": 4})

    assert all(d["pct_total"] == 0 for d in cap)
