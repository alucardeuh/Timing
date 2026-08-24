"""
Lecture par semaine.

La carte de charge répond bien à « est-ce tenable ? », mais elle demande de
décoder des hauteurs et des couleurs pour en tirer le seul chiffre qui sert
à décider : combien de jours il reste. Ce bloc donne ce chiffre directement,
et doit rester d'accord avec la carte au jour près — deux vues de la même
app qui racontent deux histoires, c'est le pire des deux mondes.
"""
from datetime import date, timedelta

from conftest import SETTINGS, project_data

import app as flask_app
import calculations as calc


def _lundi():
    return calc.week_monday(date.today())


def _projet(jours_par_semaine, statut="confirmed", semaines=4, debut=None):
    return {
        "id": 1, "name": "P", "client": "C", "status": statut,
        "days_per_week": jours_par_semaine, "duration_value": semaines,
        "duration_unit": "weeks", "day_rate": 500, "price_total": 10000,
        "hours_per_day": None, "start_date": (debut or _lundi()).isoformat(),
        "color": "#3E8E82", "notes": "", "archived": 0, "weekdays": None,
    }


def test_une_semaine_vide_est_entierement_libre():
    capacite = calc.daily_capacity([], _lundi(), 7, True, SETTINGS)
    ligne = calc.weekly_load(capacite)[0]

    assert ligne["capacity_days"] == 5
    assert ligne["booked_days"] == 0
    assert ligne["free_days"] == 5
    assert ligne["phrase"] == "5 jours libres"


def test_les_jours_engages_correspondent_a_la_carte():
    """Le total de la semaine réagrège exactement les journées de la carte,
    sans recalcul parallèle qui pourrait diverger."""
    capacite = calc.daily_capacity([_projet(2)], _lundi(), 7, True, SETTINGS)
    ligne = calc.weekly_load(capacite)[0]

    attendu = round(sum(j["pct_total"] for j in capacite) / 100, 2)
    assert ligne["booked_days"] == attendu == 2.0
    assert ligne["free_days"] == 3.0


def test_une_surcharge_se_dit_en_jours_de_trop():
    """« 2 jours de trop » se lit sans effort. « 140 % de charge moyenne »
    demande une division mentale par le nombre de jours ouvrés."""
    capacite = calc.daily_capacity([_projet(7)], _lundi(), 7, True, SETTINGS)
    ligne = calc.weekly_load(capacite)[0]

    assert ligne["level"] == "over"
    assert ligne["phrase"] == "2 jours de trop"


def test_le_singulier_est_respecte():
    capacite = calc.daily_capacity([_projet(4)], _lundi(), 7, True, SETTINGS)
    assert calc.weekly_load(capacite)[0]["phrase"] == "1 jour libre"


def test_une_semaine_complete_ne_dit_pas_zero_jour_libre():
    capacite = calc.daily_capacity([_projet(5)], _lundi(), 7, True, SETTINGS)
    assert calc.weekly_load(capacite)[0]["phrase"] == "complet"


def test_la_part_provisoire_est_isolee():
    """Le socle signé et ce qui n'engage à rien ne doivent pas se confondre
    dans un même chiffre : c'est toute la différence entre un problème et
    une hypothèse."""
    projets = [_projet(2), dict(_projet(3, statut="provisional"), id=2)]
    capacite = calc.daily_capacity(projets, _lundi(), 7, True, SETTINGS)
    ligne = calc.weekly_load(capacite)[0]

    assert ligne["booked_days"] == 5.0
    assert ligne["provisional_days"] == 3.0


def test_une_semaine_de_conges_est_dite_non_travaillee():
    """Zéro jour disponible et zéro engagé, ce n'est pas « complet » : il
    n'y a simplement rien à remplir."""
    lundi = _lundi()
    absences = [{"label": "Congés", "kind": "conges", "start_date": lundi.isoformat(),
                 "end_date": (lundi + timedelta(days=6)).isoformat()}]
    capacite = calc.daily_capacity([_projet(2)], lundi, 7, True, SETTINGS, absences)
    ligne = calc.weekly_load(capacite)[0]

    assert ligne["level"] == "off"
    assert ligne["capacity_days"] == 0
    assert "non travaillée" in ligne["phrase"]


def test_la_phrase_d_entete_situe_la_premiere_surcharge():
    lignes = calc.weekly_load(
        calc.daily_capacity([_projet(7)], _lundi(), 14, True, SETTINGS))
    entete = calc.weekly_headline(lignes)

    assert "2 semaines en surcharge" in entete
    assert lignes[0]["label"] in entete


def test_la_phrase_d_entete_ne_promet_pas_zero_jour(monkeypatch):
    """« 0 jour disponibles ailleurs » : la phrase de repli ne doit pas se
    déclencher quand il n'y a rien à replier."""
    lignes = calc.weekly_load(
        calc.daily_capacity([_projet(7)], _lundi(), 14, True, SETTINGS))
    assert "0 jour" not in calc.weekly_headline(lignes)


def test_la_lecture_est_sur_les_deux_pages(base):
    flask_app.app.config["CSRF_PROTECT"] = False
    base.create_project(project_data(start_date=date.today().isoformat()))
    client = flask_app.app.test_client()

    for url in ("/", "/planning"):
        html = client.get(url).data.decode()
        assert "week-read" in html, url
        assert "semaine du" in html, url


def test_le_gantt_est_conserve(base):
    """La vue Calendrier montre les chevauchements, ce que ni la lecture par
    semaine ni la grille d'allocation ne disent."""
    flask_app.app.config["CSRF_PROTECT"] = False
    base.create_project(project_data(start_date=date.today().isoformat()))

    html = flask_app.app.test_client().get("/planning?view=calendrier").data.decode()
    assert "gantt-row" in html


def test_la_carte_jour_par_jour_reste_accessible(base):
    """Repliée, pas supprimée : elle répond à « quel jour précis déborde »,
    une question qui se pose après avoir repéré la semaine."""
    flask_app.app.config["CSRF_PROTECT"] = False
    base.create_project(project_data(start_date=date.today().isoformat()))

    html = flask_app.app.test_client().get("/").data.decode()
    assert "capacity-columns" in html
    assert "détail jour par jour" in html
