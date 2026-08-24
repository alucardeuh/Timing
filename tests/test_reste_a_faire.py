"""
Reste à faire déclaré et terminaison prévue.

La cadence comparait le budget consommé au temps écoulé. C'est un prorata :
il suppose que le travail avance au rythme du calendrier. Deux projets à
90 % de budget, l'un à trois jours de la fin, l'autre à trois semaines,
s'affichaient donc exactement pareil.
"""
from datetime import date, datetime, timedelta

from conftest import SETTINGS, agg, make_project, project_data

import app as flask_app
import calculations as calc


def _projet(remaining=None, age_days=0, **over):
    projet = make_project(**over)
    projet["remaining_days"] = remaining
    projet["remaining_updated_at"] = (
        None if remaining is None
        else (datetime.now() - timedelta(days=age_days)).isoformat(timespec="seconds")
    )
    return projet


def test_sans_estimation_rien_ne_change(base=None):
    """Un projet sans reste à faire déclaré garde le comportement d'avant :
    la nouveauté ne doit rien casser pour qui ne s'en sert pas."""
    projet = make_project()  # ne porte même pas la clé
    assert calc.declared_remaining(projet) is None
    assert calc.estimate_at_completion(projet, agg(1000)) is None
    assert calc.pace_basis(projet) == "elapsed"


def test_terminaison_prevue_et_ecart():
    # 40 jours vendus, 10 consommés, 40 déclarés restants → 50 à terminaison.
    projet = _projet(remaining=40)
    a = agg(1000)  # 10 jours

    assert calc.estimate_at_completion(projet, a) == 50
    assert calc.eac_drift(projet, a) == 10
    assert calc.eac_drift_pct(projet, a) == 25.0


def test_le_depassement_annonce_sort_avant_le_depassement_reel():
    """La seule alerte qui puisse sonner AVANT les faits, donc la seule qui
    laisse encore le temps de renégocier : 20 % de budget consommé, mais un
    reste à faire qui fait sortir le projet de son enveloppe."""
    projet = _projet(remaining=45)
    a = agg(800)  # 8 jours sur 40 vendus = 20 %

    assert calc.pct_consumed(projet, a) == 20.0
    # Terminaison à 53 j pour 40 vendus : 32,5 % de dépassement annoncé,
    # alors que le compteur de budget affiche encore 80 % de disponible.
    assert calc.eac_drift(projet, a) == 13
    assert calc.pace_status(projet, a) == "behind"


def test_une_estimation_fraiche_pilote_la_cadence():
    projet = _projet(remaining=2, age_days=1)
    a = agg(3000)  # 30 jours consommés sur 40 → 32 à terminaison, sous budget

    assert calc.pace_basis(projet) == "declared"
    assert calc.pace_status(projet, a) == "ahead"


def test_une_estimation_perimee_repasse_au_prorata():
    """Une estimation vieille de deux mois ne doit pas peser autant qu'une
    estimation du jour : ce serait pire que pas d'estimation du tout."""
    projet = _projet(remaining=2, age_days=calc.REMAINING_FRESH_DAYS + 5)
    a = agg(3000)

    assert calc.pace_basis(projet) == "elapsed"
    attendu = calc.pct_consumed(projet, a) - calc.pct_time_elapsed(projet)
    assert calc.pace_delta(projet, a) == attendu


def test_un_projet_pas_commence_reste_pas_commence():
    """Déclarer un reste à faire ne doit pas faire croire qu'on a commencé :
    sans aucune saisie, il n'y a rien à comparer."""
    projet = _projet(remaining=10)
    assert calc.pace_status(projet, agg(0, count=0)) == "not_started"


def test_une_valeur_illisible_est_ignoree():
    """Une colonne texte remplie à la main, un import bancal : la valeur est
    ignorée plutôt que de faire exploser toutes les pages d'un coup."""
    projet = _projet(remaining=None)
    projet["remaining_days"] = "beaucoup"
    assert calc.declared_remaining(projet) is None
    assert calc.pace_basis(projet) == "elapsed"


def test_l_alerte_de_depassement_annonce_apparait():
    projet = _projet(remaining=40)
    rows = [{"project": projet,
             "stats": calc.project_stats(projet, agg(800), SETTINGS)}]

    alertes = calc.build_alerts(rows, [], [], [])
    textes = " ".join(a["text"] for a in alertes)
    assert "reste à faire annoncé dépasse le budget" in textes


def test_une_estimation_perimee_est_signalee():
    projet = _projet(remaining=5, age_days=calc.REMAINING_FRESH_DAYS + 10)
    rows = [{"project": projet,
             "stats": calc.project_stats(projet, agg(1000), SETTINGS)}]

    textes = " ".join(a["text"] for a in calc.build_alerts(rows, [], [], []))
    assert "reste à faire date de" in textes


def test_la_route_enregistre_et_efface(base):
    flask_app.app.config["CSRF_PROTECT"] = False
    pid = base.create_project(project_data(start_date=date.today().isoformat()))
    client = flask_app.app.test_client()

    client.post(f"/projects/{pid}/reste", data={"remaining_days": "7,5"},
                follow_redirects=True)
    assert base.get_project(pid)["remaining_days"] == 7.5

    # Champ vidé = estimation effacée, pas remise à zéro : « zéro jour
    # restant » et « je ne sais pas » sont deux affirmations différentes.
    client.post(f"/projects/{pid}/reste", data={"remaining_days": ""},
                follow_redirects=True)
    assert base.get_project(pid)["remaining_days"] is None


def test_la_route_refuse_une_valeur_absurde(base):
    flask_app.app.config["CSRF_PROTECT"] = False
    pid = base.create_project(project_data(start_date=date.today().isoformat()))
    client = flask_app.app.test_client()

    reponse = client.post(f"/projects/{pid}/reste", data={"remaining_days": "-3"},
                          follow_redirects=True)
    assert reponse.status_code == 200
    assert base.get_project(pid)["remaining_days"] is None
