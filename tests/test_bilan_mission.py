"""
Bilan de mission.

Toutes les données existaient déjà, éparpillées sur la fiche projet, le
comparatif et la facturation. Les rassembler ne change aucun calcul, mais
change ce qu'on peut en faire : la fiche sert à piloter une mission en
cours, le bilan sert à chiffrer la suivante.
"""
from datetime import date, timedelta

import app as flask_app
import calculations as calc
from conftest import SETTINGS, agg, make_project, project_data

VIDE = {"absorbed": 0.0, "rebilled": 0.0}
RIEN_FACTURE = {"total": 0, "invoiced": 0, "paid": 0}


def _bilan(project=None, a=None, **over):
    return calc.mission_review(
        project or make_project(), a if a is not None else agg(4000),
        SETTINGS, costs=VIDE, invoiced=RIEN_FACTURE, **over)


def test_le_depassement_est_dit_en_jours_et_en_pourcentage():
    # 40 jours vendus, 47,5 passés.
    bilan = _bilan(a=agg(4750))

    assert bilan["days_sold"] == 40
    assert bilan["days_spent"] == 47.5
    assert bilan["days_gap"] == 7.5
    assert bilan["days_gap_pct"] == 18.8
    assert "47,5 jours passés pour 40 jours vendus" in bilan["constats"][0]


def test_le_bilan_chiffre_ce_qu_il_aurait_fallu_vendre():
    """Un constat, pas un conseil. « Vends 20 % de plus » est une
    recommandation que l'app n'a pas les moyens de donner ; « il aurait
    fallu vendre 47,5 jours » est un fait arithmétique."""
    textes = " ".join(_bilan(a=agg(4750))["constats"])

    assert "il aurait fallu facturer" in textes
    assert "47,5 jours dès le départ" in textes
    # Le montant porte la devise et une séparation des milliers : « 3750 »
    # se relit deux fois.
    assert "3 750€" in textes


def test_un_budget_tenu_est_dit_comme_tel():
    textes = " ".join(_bilan(a=agg(4000))["constats"])
    assert "Budget tenu" in textes


def test_une_economie_n_est_pas_presentee_comme_un_depassement():
    bilan = _bilan(a=agg(3000))
    assert bilan["days_gap"] == -10
    assert "de moins que prévu" in bilan["constats"][0]


def test_l_etalement_calendaire_est_signale():
    """Une mission de huit semaines étalée sur quatre mois n'a pas coûté
    plus de jours, mais elle a occupé la tête plus longtemps."""
    a = agg(4000, count=40,
            first_date=(date.today() - timedelta(days=120)).isoformat())
    bilan = _bilan(a=a)

    assert bilan["weeks_actual"] > bilan["weeks_planned"]
    textes = " ".join(bilan["constats"])
    assert "s'est étalée sur" in textes
    # Virgule décimale, pas point : « 17.3 semaines » n'est pas français.
    assert "17,3 semaines" in textes


def test_sans_tache_nommee_aucun_constat_sur_les_taches():
    """Sur un projet sans découpage, tout le temps tombe dans « Sans
    tâche » et la phrase « 100 % du temps est parti sur Sans tâche »
    n'apprend rien."""
    bilan = _bilan(tasks=[{"name": "Sans tâche", "days": 40}])
    assert not any("Sans tâche" in c for c in bilan["constats"])


def test_une_tache_dominante_est_signalee():
    bilan = _bilan(tasks=[{"name": "Ateliers", "days": 30},
                          {"name": "Rédaction", "days": 10}])
    assert "75 % du temps est parti sur « Ateliers »" in " ".join(bilan["constats"])


def test_le_delai_d_encaissement_est_celui_de_ce_projet():
    """Pas la moyenne du client : c'est cette mission-là qu'on regarde."""
    jalons = [{"status": "paid", "label": "Acompte", "amount": 5000,
               "due_date": None, "invoice_ref": "F-1",
               "invoiced_at": "2026-01-01T00:00:00",
               "paid_at": "2026-02-15T00:00:00"}]
    assert _bilan(milestones=jalons)["payment_delay"] == 45


def test_un_projet_sans_saisie_ne_raconte_rien():
    """Aucun constat inventé sur une mission qui n'a pas commencé."""
    bilan = _bilan(a=agg(0, count=0))
    assert bilan["constats"] == []
    assert bilan["weeks_actual"] is None


def test_la_page_repond_et_porte_les_constats(base):
    flask_app.app.config["CSRF_PROTECT"] = False
    pid = base.create_project(project_data(
        status="completed",
        start_date=(date.today() - timedelta(days=60)).isoformat()))
    base.create_entry(pid, date.today().isoformat(), 100, 7)

    reponse = flask_app.app.test_client().get(f"/projects/{pid}/bilan")
    assert reponse.status_code == 200
    html = reponse.data.decode()
    assert "Bilan de mission" in html
    assert "Vendu / réalisé" in html


def test_la_fiche_projet_mene_au_bilan(base):
    flask_app.app.config["CSRF_PROTECT"] = False
    pid = base.create_project(project_data(start_date=date.today().isoformat()))

    html = flask_app.app.test_client().get(f"/projects/{pid}").data.decode()
    assert f"/projects/{pid}/bilan" in html


def test_un_projet_inexistant_donne_404(base):
    flask_app.app.config["CSRF_PROTECT"] = False
    assert flask_app.app.test_client().get("/projects/9999/bilan").status_code == 404


def test_creer_une_tache_rend_son_id(base):
    """Ne rien renvoyer obligeait l'appelant à relire la tâche par son nom,
    donc à supposer qu'aucune homonyme n'existe sur le même projet."""
    pid = base.create_project(project_data(start_date=date.today().isoformat()))
    premier = base.create_task(pid, "Ateliers")
    second = base.create_task(pid, "Ateliers")

    assert premier is not None
    assert premier != second
