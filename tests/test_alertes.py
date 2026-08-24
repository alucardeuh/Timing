"""
Le tableau de bord avait deux façons de dire la même chose : le bandeau
rouge (late_projects, une ligne par projet, toutes ses raisons réunies) et
la liste « À regarder » (build_alerts, une ligne par raison). Un projet en
retard apparaissait dans les deux, avec deux formulations qui se
recoupaient sans se répondre exactement. build_alerts ne parle plus de ce
qui concerne un projet précis (budget, cadence, dépassement) — late_projects
le dit déjà — et garde ce qu'il ne dit pas : jalons, jours non saisis,
surcharge, échéance qui approche.
"""
from datetime import date, timedelta

import calculations as calc
from conftest import SETTINGS, agg, make_project


def test_budget_depasse_ne_duplique_plus_le_bandeau(base):
    """Un projet en dépassement de budget, de cadence et de fin ne doit
    apparaître dans build_alerts pour AUCUNE de ces trois raisons — mais
    doit toujours apparaître dans late_projects pour les trois."""
    debut = date.today() - timedelta(days=60)
    project = make_project(status="confirmed", start_date=debut.isoformat(),
                           duration_value=4)  # fini depuis longtemps
    stats = calc.project_stats(project, agg(4500, count=45, first_date=debut.isoformat()), SETTINGS)
    rows = [{"project": project, "stats": stats}]

    alerts = calc.build_alerts(rows, capacity=[], milestones=[], missing=[])
    assert alerts == []  # plus aucune alerte "projet" : tout est dans le bandeau

    late = calc.late_projects(rows, SETTINGS)
    assert len(late) == 1
    reasons = " ".join(late[0]["reasons"])
    assert "budget" in reasons
    assert "fin prévue dépassée" in reasons


def test_echeance_proche_reste_dans_les_alertes(base):
    """Un projet qui se termine dans 10 jours n'est pas encore en
    dépassement : late_projects ne le voit pas, c'est donc à build_alerts
    de le signaler — la seule chose que le bandeau ne couvre pas."""
    debut = date.today() - timedelta(days=14)
    fin_visee = date.today() + timedelta(days=10)
    span_days = (fin_visee - debut).days + 1  # planned_end_date = début + span - 1
    project = make_project(status="confirmed", start_date=debut.isoformat(),
                           duration_value=span_days / 7)
    assert calc.planned_end_date(project) == fin_visee  # l'hypothèse du test tient

    stats = calc.project_stats(project, agg(500, count=5, first_date=debut.isoformat()), SETTINGS)
    rows = [{"project": project, "stats": stats}]

    late = calc.late_projects(rows, SETTINGS)
    assert late == []  # pas encore en retard

    alerts = calc.build_alerts(rows, capacity=[], milestones=[], missing=[])
    assert any("se termine le" in a["text"] for a in alerts)


def test_jalons_et_jours_non_saisis_toujours_signales(base):
    """Ce que late_projects ne couvre pas du tout doit rester intact."""
    milestones = [{"status": "todo", "due_date": (date.today() - timedelta(days=1)).isoformat(),
                   "label": "Acompte", "project_name": "P", "invoiced_at": None}]
    missing = [date.today() - timedelta(days=1)]

    alerts = calc.build_alerts([], capacity=[], milestones=milestones, missing=missing)

    assert any("Jalon à facturer en retard" in a["text"] for a in alerts)
    assert any("jour(s) ouvré(s) sans saisie" in a["text"] for a in alerts)
