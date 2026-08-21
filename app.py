"""
Timing — routes Flask.

Aucune requête SQL ici : tout passe par db.py. Aucun calcul métier non plus :
tout passe par calculations.py. Ce fichier ne fait que trois choses — lire
une requête, valider ce qui arrive, choisir un template.
"""
from __future__ import annotations

import csv
import io
import os
from datetime import date, datetime, timedelta

from flask import (
    Flask, Response, abort, flash, redirect, render_template, request,
    send_file, url_for,
)

import calculations as calc
import db

app = Flask(__name__)
# Clé de session locale mono-utilisateur : elle ne protège que les messages
# flash. Surchargeable par TIMING_SECRET si tu tiens à la changer.
app.secret_key = os.environ.get("TIMING_SECRET", "timing-local-single-user")

PLANNING_WINDOW_DAYS = 91  # 13 semaines
HOME_WINDOW_DAYS = 56      # 8 semaines
ENTRIES_PER_PAGE = 50

STATUS_LABELS = {
    "provisional": "provisoire", "confirmed": "confirmé",
    "paused": "en pause", "completed": "terminé",
}
PACE_LABELS = {
    "not_started": "pas commencé", "ahead": "en avance",
    "on_track": "dans les clous", "behind": "en retard",
}
MILESTONE_LABELS = {"todo": "à facturer", "invoiced": "facturé", "paid": "encaissé"}
ABSENCE_LABELS = {"conges": "congés", "ferie": "férié", "indispo": "indisponible"}
WEEKDAY_NAMES = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


# ------------------------------------------------------------- validation

class FormError(Exception):
    """Erreur de saisie destinée à l'utilisateur, pas une trace technique."""


def req_float(form, field, label, minimum=None, maximum=None, default=None):
    """Lit un nombre dans un formulaire, ou lève une FormError lisible.

    La V1 faisait `float(form.get(...))` directement : un champ vide
    renvoyait une page 500. Ici l'utilisateur reçoit une phrase et retrouve
    son formulaire prérempli.
    """
    raw = (form.get(field) or "").strip().replace(",", ".")
    if raw == "":
        if default is not None:
            return default
        raise FormError(f"« {label} » est obligatoire.")
    try:
        value = float(raw)
    except ValueError:
        raise FormError(f"« {label} » doit être un nombre (reçu : {raw!r}).")
    if minimum is not None and value < minimum:
        raise FormError(f"« {label} » ne peut pas être inférieur à {minimum:g}.")
    if maximum is not None and value > maximum:
        raise FormError(f"« {label} » ne peut pas dépasser {maximum:g}.")
    return value


def opt_float(form, field, label, minimum=None):
    raw = (form.get(field) or "").strip()
    if raw == "":
        return None
    return req_float(form, field, label, minimum=minimum)


def req_date(form, field, label, default_today=False):
    raw = (form.get(field) or "").strip()
    if not raw:
        if default_today:
            return date.today().isoformat()
        raise FormError(f"« {label} » est obligatoire.")
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        raise FormError(f"« {label} » n'est pas une date valide (attendu AAAA-MM-JJ).")
    return raw


def req_choice(form, field, label, allowed, default=None):
    value = (form.get(field) or "").strip() or default
    if value not in allowed:
        raise FormError(f"« {label} » a une valeur inattendue : {value!r}.")
    return value


def req_text(form, field, label, max_len=200):
    value = (form.get(field) or "").strip()
    if not value:
        raise FormError(f"« {label} » est obligatoire.")
    return value[:max_len]


def parse_project_form(form):
    days_per_week = req_float(form, "days_per_week", "Jours / semaine", minimum=0.1, maximum=7)
    duration_value = req_float(form, "duration_value", "Durée", minimum=0.1)
    duration_unit = req_choice(form, "duration_unit", "Unité de durée", ("weeks", "months"), "weeks")
    status = req_choice(form, "status", "Statut", db.PROJECT_STATUSES, "provisional")

    day_rate = opt_float(form, "day_rate", "Taux journalier", minimum=0)
    price_total = opt_float(form, "price_total", "Prix total", minimum=0)
    hours_per_day = opt_float(form, "hours_per_day", "Heures / jour", minimum=0.5)

    total_days = calc.total_days_sold({
        "days_per_week": days_per_week,
        "duration_value": duration_value,
        "duration_unit": duration_unit,
    })
    # Le TJM prime s'il est renseigné : c'est la façon dont on vend, le prix
    # total en découle. Sinon on prend le prix tel quel.
    if day_rate:
        price_total = round(day_rate * total_days, 2)
    elif price_total is None:
        price_total = 0.0

    return {
        "name": req_text(form, "name", "Nom du projet"),
        "client": (form.get("client") or "").strip()[:120],
        "status": status,
        "days_per_week": days_per_week,
        "duration_value": duration_value,
        "duration_unit": duration_unit,
        "day_rate": day_rate,
        "price_total": price_total,
        "hours_per_day": hours_per_day,
        "start_date": req_date(form, "start_date", "Date de début", default_today=True),
        "notes": (form.get("notes") or "").strip(),
    }


def percent_to_hours(percent, project, settings):
    return round(percent / 100 * calc.hours_per_day(project, settings), 2)


# ------------------------------------------------------------- assemblage

def project_rows(projects, settings, aggregates=None, costs=None, milestones=None):
    """Construit [{project, stats}] pour une liste de projets, en une passe.

    Les agrégats arrivent d'une seule requête SQL : plus de get_entries()
    dans une boucle, donc plus de connexion ouverte par projet.
    """
    aggregates = db.entries_aggregate_by_project() if aggregates is None else aggregates
    costs = db.costs_by_project() if costs is None else costs
    milestones = db.milestone_totals_by_project() if milestones is None else milestones
    rows = []
    for p in projects:
        rows.append({
            "project": p,
            "stats": calc.project_stats(
                p, aggregates.get(p["id"], calc.EMPTY_AGG), settings,
                costs=costs.get(p["id"], 0.0),
                invoiced=milestones.get(p["id"], {"total": 0, "invoiced": 0, "paid": 0}),
            ),
        })
    return rows


@app.context_processor
def inject_globals():
    settings = db.get_settings()
    return {
        "currency": settings["currency_symbol"],
        "today": date.today().isoformat(),
        "status_label": STATUS_LABELS,
        "pace_label": PACE_LABELS,
        "milestone_label": MILESTONE_LABELS,
        "absence_label": ABSENCE_LABELS,
    }


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", code=404,
                           message="Cette page n'existe pas."), 404


@app.errorhandler(500)
def server_error(_):
    return render_template("error.html", code=500,
                           message="Quelque chose s'est mal passé côté serveur."), 500


# ------------------------------------------------------------- aujourd'hui

@app.route("/")
def dashboard():
    settings = db.get_settings()
    today = date.today()

    projects = db.list_projects()
    aggregates = db.entries_aggregate_by_project()
    costs = db.costs_by_project()
    milestone_totals = db.milestone_totals_by_project()
    rows = project_rows(projects, settings, aggregates, costs, milestone_totals)

    loggable = [p for p in projects if p["status"] in ("provisional", "confirmed")]
    cards = [r for r in rows if r["project"]["status"] in ("confirmed", "provisional")]
    order = {"behind": 0, "on_track": 1, "not_started": 2, "ahead": 3}
    cards.sort(key=lambda c: order.get(c["stats"]["pace_status"], 2))

    include_provisional = request.args.get("include_provisional", "1") != "0"
    absences = db.list_absences()
    window_start = calc.week_monday(today)
    capacity = calc.daily_capacity(projects, window_start, HOME_WINDOW_DAYS,
                                   include_provisional, settings, absences)

    # Classement de rentabilité : uniquement les projets dont l'indice est
    # fiable. Sans ce filtre, un projet avec une heure saisie arrivait
    # premier avec un ×280 dénué de sens.
    ranked = [{"project": r["project"], "index": r["stats"]["rentability_index"]}
              for r in rows if r["stats"]["rentability_index"] is not None]
    ranked.sort(key=lambda r: r["index"], reverse=True)

    entries_recent = db.entries_by_day((today - timedelta(days=21)).isoformat(), today.isoformat())
    missing = calc.missing_days(entries_recent, settings, absences, today)

    alerts = calc.build_alerts(rows, capacity, db.list_milestones(), missing, settings, today)

    month_start = today.replace(day=1).isoformat()
    year_start = today.replace(month=1, day=1).isoformat()
    revenue = calc.revenue_overview(
        projects, milestone_totals,
        db.invoiced_between(month_start, today.isoformat()),
        db.invoiced_between(year_start, today.isoformat()),
        settings,
    )

    return render_template(
        "dashboard.html",
        cards=cards, ranked=ranked[:3], loggable=loggable,
        capacity=capacity, capacity_summary=calc.capacity_summary(capacity),
        include_provisional=include_provisional,
        today_logged=db.today_summary(today.isoformat()),
        alerts=alerts, revenue=revenue, settings=settings,
        tasks_by_project=db.list_all_active_tasks(),
    )


# ------------------------------------------------------------- grille hebdo

@app.route("/semaine")
def week():
    settings = db.get_settings()
    try:
        offset = int(request.args.get("offset", 0))
    except ValueError:
        offset = 0
    start = calc.week_monday(date.today()) + timedelta(weeks=offset)
    days = [start + timedelta(days=i) for i in range(7)]

    projects = [p for p in db.list_projects() if p["status"] in ("provisional", "confirmed", "paused")]
    projects_by_id = {p["id"]: p for p in projects}
    tasks_by_project = db.list_all_active_tasks()
    task_name = db.task_names()
    cells = db.week_grid_cells(days[0].isoformat(), days[-1].isoformat())

    absences = db.list_absences()
    absence_index = calc.build_absence_index(absences)
    day_meta = [{
        "date": d,
        "iso": d.isoformat(),
        "label": WEEKDAY_NAMES[d.weekday()][:3],
        "num": d.strftime("%d/%m"),
        "is_today": d == date.today(),
        "off_reason": (None if calc.is_working_day(d, settings, absence_index)
                       else (absence_index.get(d) or "week-end")),
    } for d in days]

    # Une ligne par couple (projet, tâche) déjà saisi cette semaine, plus
    # une ligne vierge par projet actif pour pouvoir commencer à saisir.
    keys = set(cells.keys())
    for p in projects:
        if not any(k[0] == p["id"] for k in keys):
            keys.add((p["id"], None))

    def sort_key(key):
        """Trie par nom de projet puis par tâche. Les clés dont le projet
        est archivé sont écartées juste après, pas ici : sqlite3.Row n'a pas
        de .get(), donc on garde la clé de tri strictement simple."""
        project = projects_by_id.get(key[0])
        return (project["name"].lower() if project else "", key[1] or 0)

    rows = []
    for project_id, task_id in sorted(keys, key=sort_key):
        project = projects_by_id.get(project_id)
        if not project:
            continue  # projet archivé : ses saisies restent en base, hors grille
        values = cells.get((project_id, task_id), {})
        rows.append({
            "project": project,
            "task_id": task_id,
            "task_name": task_name.get(task_id) if task_id else None,
            "values": [values.get(d.isoformat(), 0) for d in days],
            "total": round(sum(values.values()), 1),
        })

    day_totals = [round(sum(r["values"][i] for r in rows), 1) for i in range(7)]
    week_total = round(sum(day_totals), 1)

    return render_template(
        "week.html", rows=rows, days=day_meta, day_totals=day_totals,
        week_total=week_total, offset=offset, projects=projects,
        tasks_by_project=tasks_by_project, start=start, end=days[-1],
        settings=settings,
    )


@app.route("/semaine/enregistrer", methods=["POST"])
def save_week():
    settings = db.get_settings()
    saved = 0
    try:
        for key, raw in request.form.items():
            if not key.startswith("cell-"):
                continue
            _, project_id, task_raw, day_iso = key.split("-", 3)
            project = db.get_project(int(project_id))
            if not project:
                continue
            raw = (raw or "").strip().replace(",", ".")
            percent = float(raw) if raw else 0.0
            if percent < 0 or percent > 300:
                raise FormError("Un pourcentage doit être compris entre 0 et 300.")
            task_id = int(task_raw) if task_raw != "none" else None
            db.set_day_total(project["id"], task_id, day_iso, percent,
                             percent_to_hours(percent, project, settings))
            saved += 1
    except FormError as exc:
        flash(str(exc), "error")
    except (ValueError, AttributeError):
        flash("Saisie invalide : seuls des nombres sont acceptés dans la grille.", "error")
    else:
        flash(f"Semaine enregistrée ({saved} case(s) traitée(s)).", "success")
    return redirect(url_for("week", offset=request.form.get("offset", 0)))


@app.route("/semaine/ligne", methods=["POST"])
def add_week_row():
    """Ajoute une ligne (projet, tâche) vide à la grille. La ligne n'existe
    que parce qu'une case y sera saisie : on crée une entrée à 0 % le lundi
    pour matérialiser la clé, supprimée d'elle-même si rien n'est saisi."""
    try:
        project_id = int(request.form.get("project_id"))
    except (TypeError, ValueError):
        flash("Projet invalide.", "error")
        return redirect(url_for("week"))
    task_raw = request.form.get("task_id") or "none"
    task_id = int(task_raw) if task_raw != "none" else None
    offset = request.form.get("offset", 0)
    try:
        start = calc.week_monday(date.today()) + timedelta(weeks=int(offset))
    except ValueError:
        start = calc.week_monday(date.today())
    db.set_day_total(project_id, task_id, start.isoformat(), 0.01, 0)
    flash("Ligne ajoutée — saisis tes pourcentages puis enregistre.", "success")
    return redirect(url_for("week", offset=offset))


# ------------------------------------------------------------- entrées

@app.route("/entries", methods=["POST"])
def create_entry():
    settings = db.get_settings()
    try:
        project = db.get_project(int(request.form.get("project_id") or 0))
        if not project:
            raise FormError("Projet introuvable.")
        percent = req_float(request.form, "percent_of_day", "% du jour", minimum=0.1, maximum=300)
        entry_date = req_date(request.form, "entry_date", "Date", default_today=True)
        task_raw = (request.form.get("task_id") or "").strip()
        task_id = int(task_raw) if task_raw else None
        db.create_entry(project["id"], entry_date, percent,
                        percent_to_hours(percent, project, settings),
                        task_id, (request.form.get("note") or "").strip())
    except FormError as exc:
        flash(str(exc), "error")
    except ValueError:
        flash("Saisie invalide.", "error")
    else:
        flash("Entrée ajoutée.", "success")
    return redirect(request.form.get("next") or url_for("dashboard"))


@app.route("/entries/<int:entry_id>/edit", methods=["GET", "POST"])
def edit_entry(entry_id):
    entry = db.get_entry(entry_id)
    if not entry:
        abort(404)
    project = db.get_project(entry["project_id"])
    settings = db.get_settings()

    if request.method == "POST":
        try:
            percent = req_float(request.form, "percent_of_day", "% du jour", minimum=0.1, maximum=300)
            entry_date = req_date(request.form, "entry_date", "Date")
            task_raw = (request.form.get("task_id") or "").strip()
            task_id = int(task_raw) if task_raw else None
            db.update_entry(entry_id, entry_date, percent,
                            percent_to_hours(percent, project, settings),
                            task_id, (request.form.get("note") or "").strip())
        except FormError as exc:
            flash(str(exc), "error")
            return render_template("entry_form.html", entry=entry, project=project,
                                   tasks=db.list_tasks(project["id"]))
        flash("Entrée modifiée.", "success")
        return redirect(url_for("project_detail", project_id=project["id"]))

    return render_template("entry_form.html", entry=entry, project=project,
                           tasks=db.list_tasks(project["id"]))


@app.route("/entries/<int:entry_id>/delete", methods=["POST"])
def delete_entry(entry_id):
    entry = db.get_entry(entry_id)
    db.delete_entry(entry_id)
    flash("Entrée supprimée.", "success")
    if entry:
        return redirect(url_for("project_detail", project_id=entry["project_id"]))
    return redirect(url_for("dashboard"))


# ------------------------------------------------------------- planning

@app.route("/planning")
def planning():
    settings = db.get_settings()
    include_provisional = request.args.get("include_provisional", "1") != "0"
    try:
        offset = int(request.args.get("offset", 0))
    except ValueError:
        offset = 0
    window_start = (calc.week_monday(date.today()) - timedelta(days=7)
                    + timedelta(days=offset * PLANNING_WINDOW_DAYS))

    all_p = db.list_projects()
    projects = [p for p in all_p if p["status"] in ("provisional", "confirmed", "paused")]
    rows = calc.gantt_rows(projects, window_start, PLANNING_WINDOW_DAYS)
    absences = db.list_absences()
    capacity = calc.daily_capacity(all_p, window_start, PLANNING_WINDOW_DAYS,
                                   include_provisional, settings, absences)

    week_labels = [{"idx": i, "label": (window_start + timedelta(days=i)).strftime("%d %b")}
                   for i in range(0, PLANNING_WINDOW_DAYS, 7)]

    return render_template(
        "planning.html", rows=rows, capacity=capacity,
        capacity_summary=calc.capacity_summary(capacity),
        window_days=PLANNING_WINDOW_DAYS,
        today_idx=(date.today() - window_start).days,
        week_labels=week_labels, include_provisional=include_provisional,
        offset=offset, window_start=window_start,
        window_end=window_start + timedelta(days=PLANNING_WINDOW_DAYS - 1),
        absences=db.list_absences(upcoming_only=True, today_iso=date.today().isoformat()),
    )


# ------------------------------------------------------------- projets

@app.route("/projects")
def projects_list():
    settings = db.get_settings()
    status_filter = request.args.get("status", "all")
    search = (request.args.get("q") or "").strip()
    client = request.args.get("client") or None
    trash = status_filter == "trash"

    projects = db.list_projects(
        status=None if trash else status_filter,
        archived=trash, search=search or None, client=client,
    )
    return render_template(
        "projects.html", rows=project_rows(projects, settings),
        status_filter=status_filter, counts=db.counts_by_status(),
        search=search, client=client, clients=db.list_clients(), trash=trash,
    )


@app.route("/projects/new", methods=["GET", "POST"])
def new_project():
    if request.method == "POST":
        try:
            data = parse_project_form(request.form)
        except FormError as exc:
            flash(str(exc), "error")
            return render_template("project_form.html", project=None, form_data=request.form)
        data["color"] = db.next_project_color()
        project_id = db.create_project(data)
        flash("Projet créé.", "success")
        return redirect(url_for("project_detail", project_id=project_id))
    return render_template("project_form.html", project=None, form_data=None)


@app.route("/projects/<int:project_id>")
def project_detail(project_id):
    project = db.get_project(project_id)
    if not project:
        abort(404)
    settings = db.get_settings()
    agg = db.entries_aggregate_for(project_id)
    costs = db.list_costs(project_id)
    costs_total = sum(c["amount"] for c in costs)
    milestones = db.list_milestones(project_id)
    milestone_totals = db.milestone_totals_by_project().get(
        project_id, {"total": 0, "invoiced": 0, "paid": 0})

    stats = calc.project_stats(project, agg, settings, costs_total, milestone_totals)

    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    total_entries = db.count_entries(project_id)
    entries = db.list_entries(project_id, limit=ENTRIES_PER_PAGE,
                              offset=(page - 1) * ENTRIES_PER_PAGE)

    return render_template(
        "project_detail.html",
        project=project, stats=stats, entries=entries,
        tally=calc.tally_segments(project, agg),
        tasks=db.list_tasks(project_id, include_archived=True),
        active_tasks=db.list_tasks(project_id),
        breakdown=db.task_breakdown(project_id),
        burndown=calc.burndown_points(project, db.cumulative_entries(project_id)),
        task_names=db.task_names(),
        milestones=milestones, costs=costs,
        scope_changes=db.list_scope_changes(project_id),
        page=page, total_entries=total_entries,
        total_pages=max(1, -(-total_entries // ENTRIES_PER_PAGE)),
        settings=settings,
    )


@app.route("/projects/<int:project_id>/edit", methods=["GET", "POST"])
def edit_project(project_id):
    project = db.get_project(project_id)
    if not project:
        abort(404)
    if request.method == "POST":
        try:
            data = parse_project_form(request.form)
        except FormError as exc:
            flash(str(exc), "error")
            return render_template("project_form.html", project=project, form_data=request.form)
        changed = db.update_project(project_id, data,
                                    scope_note=(request.form.get("scope_note") or ""))
        if changed:
            flash("Projet mis à jour. Changement de périmètre enregistré : "
                  + ", ".join(changed) + ".", "success")
        else:
            flash("Projet mis à jour.", "success")
        return redirect(url_for("project_detail", project_id=project_id))
    return render_template("project_form.html", project=project, form_data=None)


@app.route("/projects/<int:project_id>/status", methods=["POST"])
def change_status(project_id):
    try:
        db.set_project_status(project_id, request.form.get("status", ""))
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        flash("Statut mis à jour.", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/archive", methods=["POST"])
def archive_project(project_id):
    db.archive_project(project_id, True)
    flash("Projet mis à la corbeille — récupérable depuis l'onglet Corbeille.", "success")
    return redirect(url_for("projects_list"))


@app.route("/projects/<int:project_id>/restore", methods=["POST"])
def restore_project(project_id):
    db.archive_project(project_id, False)
    flash("Projet restauré.", "success")
    return redirect(url_for("projects_list", status="trash"))


@app.route("/projects/<int:project_id>/delete", methods=["POST"])
def delete_project(project_id):
    project = db.get_project(project_id)
    if not project:
        abort(404)
    # Double filet : la suppression définitive n'est possible que depuis la
    # corbeille, et demande de retaper le nom du projet.
    if not project["archived"]:
        flash("Un projet doit d'abord être mis à la corbeille.", "error")
        return redirect(url_for("project_detail", project_id=project_id))
    if (request.form.get("confirm") or "").strip() != project["name"]:
        flash(f"Suppression annulée — il fallait retaper « {project['name']} » exactement.", "error")
        return redirect(url_for("projects_list", status="trash"))
    db.delete_project_forever(project_id)
    flash("Projet supprimé définitivement.", "success")
    return redirect(url_for("projects_list", status="trash"))


# ------------------------------------------------------------- tâches

@app.route("/projects/<int:project_id>/tasks", methods=["POST"])
def create_task(project_id):
    try:
        db.create_task(project_id, req_text(request.form, "name", "Nom de la tâche", 120))
    except FormError as exc:
        flash(str(exc), "error")
    else:
        flash("Tâche ajoutée.", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/tasks/<int:task_id>/rename", methods=["POST"])
def rename_task(task_id):
    project_id = db.get_task_project(task_id)
    try:
        db.rename_task(task_id, req_text(request.form, "name", "Nom de la tâche", 120))
    except FormError as exc:
        flash(str(exc), "error")
    return redirect(url_for("project_detail", project_id=project_id) if project_id
                    else url_for("dashboard"))


@app.route("/tasks/<int:task_id>/archive", methods=["POST"])
def toggle_task_archive(task_id):
    project_id = db.get_task_project(task_id)
    db.set_task_archived(task_id, request.form.get("archived", "1") == "1")
    return redirect(url_for("project_detail", project_id=project_id) if project_id
                    else url_for("dashboard"))


@app.route("/tasks/<int:task_id>/delete", methods=["POST"])
def delete_task(task_id):
    project_id = db.get_task_project(task_id)
    db.delete_task(task_id)
    flash("Tâche supprimée — le temps saisi est conservé, sans étiquette.", "success")
    return redirect(url_for("project_detail", project_id=project_id) if project_id
                    else url_for("dashboard"))


# ------------------------------------------------------------- facturation

@app.route("/facturation")
def billing():
    settings = db.get_settings()
    today = date.today()
    milestones = db.list_milestones()
    projects = db.list_projects()
    milestone_totals = db.milestone_totals_by_project()

    buckets = {"todo": [], "invoiced": [], "paid": []}
    for m in milestones:
        buckets[m["status"]].append(m)

    totals = {k: round(sum(m["amount"] for m in v), 2) for k, v in buckets.items()}
    overdue = [m for m in buckets["todo"] if m["due_date"] and m["due_date"] <= today.isoformat()]

    month_start = today.replace(day=1).isoformat()
    year_start = today.replace(month=1, day=1).isoformat()
    revenue = calc.revenue_overview(
        projects, milestone_totals,
        db.invoiced_between(month_start, today.isoformat()),
        db.invoiced_between(year_start, today.isoformat()),
        settings,
    )

    return render_template(
        "billing.html", buckets=buckets, totals=totals, overdue=overdue,
        revenue=revenue, chart=calc.bar_chart(db.monthly_invoiced(12), "amount"),
        monthly=db.monthly_invoiced(12), settings=settings,
    )


@app.route("/projects/<int:project_id>/milestones", methods=["POST"])
def create_milestone(project_id):
    try:
        db.create_milestone(
            project_id,
            req_text(request.form, "label", "Libellé du jalon", 120),
            req_float(request.form, "amount", "Montant", minimum=0),
            (request.form.get("due_date") or "").strip() or None,
        )
    except FormError as exc:
        flash(str(exc), "error")
    else:
        flash("Jalon ajouté.", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/milestones/<int:milestone_id>/status", methods=["POST"])
def milestone_status(milestone_id):
    milestone = db.get_milestone(milestone_id)
    if not milestone:
        abort(404)
    try:
        db.set_milestone_status(milestone_id, request.form.get("status", ""),
                                (request.form.get("invoice_ref") or "").strip() or None)
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(request.form.get("next")
                    or url_for("project_detail", project_id=milestone["project_id"]))


@app.route("/milestones/<int:milestone_id>/delete", methods=["POST"])
def delete_milestone(milestone_id):
    milestone = db.get_milestone(milestone_id)
    if not milestone:
        abort(404)
    db.delete_milestone(milestone_id)
    flash("Jalon supprimé.", "success")
    return redirect(request.form.get("next")
                    or url_for("project_detail", project_id=milestone["project_id"]))


# ------------------------------------------------------------- coûts

@app.route("/projects/<int:project_id>/costs", methods=["POST"])
def create_cost(project_id):
    try:
        db.create_cost(
            project_id,
            req_text(request.form, "label", "Libellé du coût", 120),
            req_float(request.form, "amount", "Montant", minimum=0),
            (request.form.get("cost_date") or "").strip() or None,
        )
    except FormError as exc:
        flash(str(exc), "error")
    else:
        flash("Coût ajouté.", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/costs/<int:cost_id>/delete", methods=["POST"])
def delete_cost(cost_id):
    project_id = db.get_cost_project(cost_id)
    db.delete_cost(cost_id)
    flash("Coût supprimé.", "success")
    return redirect(url_for("project_detail", project_id=project_id) if project_id
                    else url_for("dashboard"))


# ------------------------------------------------------------- clients

@app.route("/clients")
def clients():
    settings = db.get_settings()
    rows = project_rows(db.list_projects(), settings)
    return render_template("clients.html",
                           clients=calc.client_rollup(rows, db.milestone_totals_by_project()))


# ------------------------------------------------------------- comparatif

@app.route("/comparatif")
def comparatif():
    settings = db.get_settings()
    tab = request.args.get("tab", "rentabilite")
    rows = project_rows(db.list_projects(), settings)
    # Les projets sans indice fiable passent en fin de liste plutôt que de
    # polluer le haut du classement avec des valeurs aberrantes.
    rows.sort(key=lambda r: (r["stats"]["rentability_index"] is None,
                             -(r["stats"]["rentability_index"] or 0)))

    tasks = db.task_breakdown_global()
    total_days = sum(t["days"] for t in tasks) or 1
    for t in tasks:
        t["pct"] = round(t["days"] / total_days * 100, 1)
        t["days"] = round(t["days"], 2)

    activity = db.monthly_activity(12)
    invoiced = db.monthly_invoiced(12)
    return render_template(
        "comparatif.html", rows=rows, task_rows=tasks, tab=tab,
        activity=activity, activity_chart=calc.bar_chart(activity, "days"),
        invoiced=invoiced, invoiced_chart=calc.bar_chart(invoiced, "amount"),
    )


# ------------------------------------------------------------- absences

@app.route("/absences", methods=["GET", "POST"])
def absences():
    if request.method == "POST":
        try:
            start = req_date(request.form, "start_date", "Date de début")
            end = req_date(request.form, "end_date", "Date de fin")
            if end < start:
                raise FormError("La date de fin doit être après la date de début.")
            db.create_absence(
                req_text(request.form, "label", "Libellé", 120),
                req_choice(request.form, "kind", "Type", db.ABSENCE_KINDS, "conges"),
                start, end,
            )
        except (FormError, ValueError) as exc:
            flash(str(exc), "error")
        else:
            flash("Absence enregistrée — la carte de charge en tient compte.", "success")
        return redirect(url_for("absences"))
    return render_template("absences.html", absences=db.list_absences(),
                           settings=db.get_settings())


@app.route("/absences/<int:absence_id>/delete", methods=["POST"])
def delete_absence(absence_id):
    db.delete_absence(absence_id)
    flash("Absence supprimée.", "success")
    return redirect(url_for("absences"))


# ------------------------------------------------------------- réglages

@app.route("/reglages", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        try:
            db.set_setting("default_hours_per_day",
                           req_float(request.form, "default_hours_per_day", "Heures / jour", 0.5, 24))
            db.set_setting("currency_symbol",
                           (request.form.get("currency_symbol") or "€").strip()[:3] or "€")
            warning = req_float(request.form, "peak_threshold_warning", "Seuil chargé", 1, 500)
            danger = req_float(request.form, "peak_threshold_danger", "Seuil tempête", 1, 500)
            if danger <= warning:
                raise FormError("Le seuil « tempête » doit être supérieur au seuil « chargé ».")
            db.set_setting("peak_threshold_warning", warning)
            db.set_setting("peak_threshold_danger", danger)

            days = request.form.getlist("working_days")
            valid = sorted({int(d) for d in days if d.isdigit() and 0 <= int(d) <= 6})
            if not valid:
                raise FormError("Il faut au moins un jour travaillé par semaine.")
            db.set_setting("working_days", ",".join(str(d) for d in valid))

            db.set_setting("min_consumption_pct",
                           req_float(request.form, "min_consumption_pct", "Seuil de fiabilité", 0, 100))
            db.set_setting("budget_alert_pct",
                           req_float(request.form, "budget_alert_pct", "Alerte budget", 1, 200))
            db.set_setting("monthly_revenue_goal",
                           req_float(request.form, "monthly_revenue_goal", "Objectif mensuel", 0, default=0))
            db.set_setting("annual_revenue_goal",
                           req_float(request.form, "annual_revenue_goal", "Objectif annuel", 0, default=0))
        except FormError as exc:
            flash(str(exc), "error")
        else:
            flash("Réglages enregistrés.", "success")
        return redirect(url_for("settings_page"))

    settings = db.get_settings()
    return render_template("settings.html", settings=settings,
                           working_days=calc.working_days_set(settings),
                           weekday_names=WEEKDAY_NAMES)


# ------------------------------------------------------------- exports

def csv_response(rows, filename):
    """CSV avec BOM UTF-8 : sans lui, Excel sous Windows affiche les accents
    en mojibake et l'export devient illisible."""
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return Response(
        "\ufeff" + buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/export/<kind>")
def export(kind):
    stamp = date.today().isoformat()
    exporters = {
        "entries": (db.export_entries, f"timing-saisies-{stamp}.csv"),
        "projects": (db.export_projects, f"timing-projets-{stamp}.csv"),
        "milestones": (db.export_milestones, f"timing-facturation-{stamp}.csv"),
    }
    if kind not in exporters:
        abort(404)
    fetch, filename = exporters[kind]
    return csv_response(fetch(), filename)


@app.route("/export/backup")
def backup():
    """Copie brute du fichier SQLite — la seule sauvegarde qui restaure
    absolument tout, y compris ce qu'aucun export CSV ne couvre."""
    if not db.DB_PATH.exists():
        abort(404)
    return send_file(db.DB_PATH, as_attachment=True,
                     download_name=f"timing-sauvegarde-{date.today().isoformat()}.sqlite3")


if __name__ == "__main__":
    db.init_db()
    port = int(os.environ.get("TIMING_PORT", 5062))
    print(f"Timing disponible sur http://127.0.0.1:{port}")
    # debug=False : le débogueur Werkzeug permet d'exécuter du Python
    # arbitraire depuis une page d'erreur. Mets TIMING_DEBUG=1 uniquement
    # quand tu développes.
    app.run(debug=os.environ.get("TIMING_DEBUG") == "1", port=port, threaded=True)
