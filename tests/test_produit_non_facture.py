"""
Travail produit et pas encore facturé.

Ni le temps passé ni le CA facturé ne montrent seuls l'écart entre les deux.
Pour quelqu'un qui facture par jalons, c'est pourtant cet écart qui dit
combien d'argent dort dans du travail déjà livré.
"""
from datetime import date

import app as flask_app
import calculations as calc
from conftest import agg, make_project, project_data


def test_produit_moins_facture():
    # 40 jours vendus à 20 000 € = 500 €/jour. 10 jours produits = 5 000 €.
    projet = make_project()
    resultat = calc.work_in_progress(projet, agg(1000), {"invoiced": 2000})

    assert resultat["produced"] == 5000
    assert resultat["invoiced"] == 2000
    assert resultat["wip"] == 3000


def test_facture_d_avance_reste_negatif():
    """Ramener à zéro effacerait une information utile : un acompte encaissé
    avant d'avoir produit n'est pas la même situation qu'un compte à zéro."""
    projet = make_project()
    resultat = calc.work_in_progress(projet, agg(200), {"invoiced": 9000})

    assert resultat["wip"] == -8000


def test_sans_jalon_tout_le_produit_est_non_facture():
    projet = make_project()
    assert calc.work_in_progress(projet, agg(400), {})["wip"] == 2000


def test_la_valorisation_se_fait_au_prix_vendu_pas_au_cout():
    """Valoriser au coût de revient donnerait un stock comptable, pas ce
    qu'il reste à encaisser."""
    projet = make_project(price_total=40000)  # 40 j vendus → 1 000 €/jour
    assert calc.work_in_progress(projet, agg(500), {})["produced"] == 5000


def test_la_page_facturation_affiche_le_produit_non_facture(base):
    flask_app.app.config["CSRF_PROTECT"] = False
    pid = base.create_project(project_data(start_date=date.today().isoformat()))
    base.create_entry(pid, date.today().isoformat(), 100, 7)

    html = flask_app.app.test_client().get("/facturation").data.decode()
    assert "Produit non facturé" in html


def test_le_bruit_sous_un_euro_est_ecarte(base):
    """Une différence d'arrondi de quelques centimes n'a rien à faire dans
    une liste censée signaler de l'argent qui dort."""
    flask_app.app.config["CSRF_PROTECT"] = False
    pid = base.create_project(project_data(start_date=date.today().isoformat(),
                                           price_total=0, day_rate=0))
    base.create_entry(pid, date.today().isoformat(), 100, 7)

    html = flask_app.app.test_client().get("/facturation").data.decode()
    assert "Produit non facturé" not in html.split("stat-label")[0]
