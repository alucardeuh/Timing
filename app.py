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
import secrets
import tempfile
from datetime import date, datetime, timedelta

from flask import (
    Flask, Response, abort, after_this_request, flash, g, redirect,
    render_template, request,
    send_file, session, url_for,
)

import calculations as calc
import db

app = Flask(__name__)


def _secret_key():
    """Clé de session : variable d'environnement, sinon une clé aléatoire
    persistée en base au premier lancement.

    Une constante en dur dans le code source signifiait que n'importe qui
    disposant du dépôt pouvait forger un cookie de session valide.
    """
    if os.environ.get("TIMING_SECRET"):
        return os.environ["TIMING_SECRET"]
    db.init_db()
    stored = db.get_settings().get("secret_key")
    if not stored or stored == "0":
        stored = secrets.token_hex(32)
        db.set_setting("secret_key", stored)
    return stored


app.secret_key = _secret_key()

PLANNING_WEEKS = 13
PLANNING_WINDOW_DAYS = PLANNING_WEEKS * 7
HOME_WINDOW_DAYS = 56      # 8 semaines
ENTRIES_PER_PAGE = 50
# Un projet terminé depuis moins de N jours reste saisissable dans la grille.
COMPLETED_GRACE_DAYS = 15

# Méthodes qui ne modifient rien : elles n'ont pas besoin de jeton.
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


@app.before_request
def csrf_protect():
    """Jeton CSRF sur toute méthode modifiant l'état.

    Même en local, une page ouverte dans le même navigateur peut poster un
    formulaire vers 127.0.0.1:5062 : les requêtes form-encoded ne déclenchent
    pas de préflight CORS. La suppression définitive d'un projet est protégée
    par la ressaisie du nom, mais supprimer une entrée, une absence, un jalon
    ou un coût ne l'était pas du tout.
    """
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    # CSRF_PROTECT=False permet aux tests de poster sans jeton ; la
    # protection reste active partout ailleurs, et un test dédié la vérifie.
    if request.method in SAFE_METHODS or not app.config.get("CSRF_PROTECT", True):
        return None
    submitted = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not submitted or not secrets.compare_digest(submitted, session["csrf_token"]):
        return render_template(
            "error.html", code=400,
            message="Jeton de sécurité manquant ou expiré. Recharge la page et réessaie.",
        ), 400
    return None


def safe_next(value, fallback):
    """N'accepte qu'un chemin interne comme cible de redirection.

    `redirect(request.form.get("next"))` sans contrôle est une redirection
    ouverte : un champ caché trafiqué pouvait renvoyer vers un site externe.
    """
    candidate = (value or "").strip()
    if candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return fallback

STATUS_LABELS = {
    "provisional": "provisoire", "confirmed": "confirmé",
    "paused": "en pause", "completed": "terminé",
}
PACE_LABELS = {
    "not_started": "pas commencé", "ahead": "en avance",
    "on_track": "dans les clous", "tight": "tendu", "behind": "en retard",
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


def valid_date(value, label):
    """Valide une date ISO isolée (pas forcément issue d'un champ de
    formulaire) et la renvoie, ou lève une FormError lisible."""
    raw = (value or "").strip()
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        raise FormError(f"« {label} » n'est pas une date valide (attendu AAAA-MM-JJ).")
    return raw


def req_date(form, field, label, default_today=False):
    raw = (form.get(field) or "").strip()
    if not raw:
        if default_today:
            return date.today().isoformat()
        raise FormError(f"« {label} » est obligatoire.")
    return valid_date(raw, label)


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
    day_rate, price_total = calc.resolve_price(day_rate, price_total, total_days)

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
        "weekdays": ",".join(sorted(
            {v for v in form.getlist("weekdays") if v.isdigit() and 0 <= int(v) <= 6},
            key=int)) or None,
        "notes": (form.get("notes") or "").strip(),
    }


def current_settings():
    """Réglages mis en cache pour la durée de la requête.

    db.get_settings() ouvrait une connexion à chaque appel : une fois dans le
    context_processor, puis une seconde fois dans presque chaque route.
    """
    if not hasattr(g, "_settings"):
        g._settings = db.get_settings()
    return g._settings


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
                costs=costs.get(p["id"], {"absorbed": 0.0, "rebilled": 0.0}),
                invoiced=milestones.get(p["id"], {"total": 0, "invoiced": 0, "paid": 0}),
            ),
        })
    return rows


@app.template_filter("weekday_names")
def weekday_names_filter(raw):
    """« 0,2 » → « lun, mer » — lisible dans le planning."""
    noms = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
    return ", ".join(noms[int(p)] for p in str(raw or "").split(",")
                     if p.strip().isdigit() and 0 <= int(p) <= 6)


@app.context_processor
def inject_globals():
    settings = current_settings()
    return {
        "csrf_token": session.get("csrf_token", ""),
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
    settings = current_settings()
    today = date.today()

    projects = db.list_projects()
    aggregates = db.entries_aggregate_by_project()
    costs = db.costs_by_project()
    milestone_totals = db.milestone_totals_by_project()
    rows = project_rows(projects, settings, aggregates, costs, milestone_totals)

    loggable = [p for p in projects if p["status"] in ("provisional", "confirmed")]
    cards = [r for r in rows if r["project"]["status"] in ("confirmed", "provisional")]
    order = {"behind": 0, "tight": 1, "on_track": 2, "not_started": 3, "ahead": 4}
    cards.sort(key=lambda c: order.get(c["stats"]["pace_status"], 2))

    include_provisional = request.args.get("include_provisional", "1") != "0"
    absences = db.list_absences()
    window_start = calc.week_monday(today)
    capacity = calc.daily_capacity(projects, window_start, HOME_WINDOW_DAYS,
                                   include_provisional, settings, absences)

    # Classement de rentabilité : uniquement les projets dont l'indice est
    # fiable. Sans ce filtre, un projet avec une heure saisie arrivait
    # premier avec un ×280 dénué de sens.
    # Trier par indice seul pousse à optimiser un ratio plutôt que de
    # l'argent : un ×1,8 sur 2 000 € pèse moins qu'un ×1,05 sur 40 000 €.
    # Le montant est donc affiché à côté, et le tri est au choix.
    rank_by = request.args.get("rank", "margin")
    ranked = [{"project": r["project"],
               "index": r["stats"]["rentability_index"],
               "net": r["stats"]["net_of_costs"],
               "real_margin": r["stats"]["real_margin"],
               "day_rate": r["stats"]["real_day_rate"]}
              for r in rows if r["stats"]["rentability_index"] is not None]
    sort_keys = {
        "index": lambda r: r["index"] or 0,
        "margin": lambda r: (r["real_margin"] if r["real_margin"] is not None else r["net"]) or 0,
        "day_rate": lambda r: r["day_rate"] or 0,
    }
    ranked.sort(key=sort_keys.get(rank_by, sort_keys["margin"]), reverse=True)

    # Projet le plus récemment saisi : cible des boutons de saisie rapide.
    last_project = None
    if aggregates:
        last_id = max(aggregates, key=lambda k: aggregates[k]["last_date"] or "")
        last_project = next((p for p in loggable if p["id"] == last_id), None)
    if last_project is None and loggable:
        last_project = loggable[0]

    entries_recent = db.entries_by_day((today - timedelta(days=21)).isoformat(), today.isoformat())
    missing = calc.missing_days(entries_recent, settings, absences, today)

    alerts = calc.build_alerts(rows, capacity, db.list_milestones(), missing, settings, today)
    late = calc.late_projects(rows, settings, today)

    month_start = today.replace(day=1).isoformat()
    year_start = today.replace(month=1, day=1).isoformat()
    revenue = calc.revenue_overview(
        projects, milestone_totals,
        db.invoiced_between(month_start, today.isoformat()),
        db.invoiced_between(year_start, today.isoformat()),
        settings,
    )

    break_even = calc.break_even(settings, db.days_spent_between(year_start, today.isoformat()))

    return render_template(
        "dashboard.html",
        cards=cards, ranked=ranked[:5], loggable=loggable, break_even=break_even,
        late=late,
        rank_by=rank_by,
        capacity=capacity, capacity_summary=calc.capacity_summary(capacity),
        capacity_scale=calc.capacity_scale(capacity),
        include_provisional=include_provisional,
        today_logged=db.today_summary(today.isoformat()),
        alerts=alerts, revenue=revenue, settings=settings,
        last_project=last_project,
        logged_today_pct=round(sum(r["total_pct"] for r in db.today_summary(today.isoformat())), 0),
    )


# ------------------------------------------------------------- grille hebdo

@app.route("/semaine")
def week():
    settings = current_settings()
    try:
        offset = int(request.args.get("offset", 0))
    except ValueError:
        offset = 0
    start = calc.week_monday(date.today()) + timedelta(weeks=offset)
    days = [start + timedelta(days=i) for i in range(7)]

    # Un projet passé en « terminé » récemment reste saisissable : sinon,
    # corriger un vendredi oublié imposait de le rouvrir, saisir, refermer.
    recent_completed = (date.today() - timedelta(days=COMPLETED_GRACE_DAYS)).isoformat()
    projects = [p for p in db.list_projects()
                if p["status"] in ("provisional", "confirmed", "paused")
                or (p["status"] == "completed" and (p["updated_at"] or "") >= recent_completed)]
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

    # Lignes demandées via l'URL (?extra=12:none). Elles n'existent que le
    # temps de l'affichage : ajouter une ligne ne doit RIEN écrire en base.
    # L'ancienne version insérait une saisie à 0,01 % pour matérialiser la
    # ligne, ce qui faisait sortir le projet de l'état « pas commencé » et
    # éteignait l'alerte « jour ouvré sans saisie » pour ce lundi.
    extras = list(request.args.getlist("extra"))
    if request.args.get("extra_project"):
        extras.append(f"{request.args['extra_project']}:"
                      f"{request.args.get('extra_task') or 'none'}")
    extras = list(dict.fromkeys(extras))  # dédoublonne en gardant l'ordre

    kept_extras = []
    for raw in extras:
        parsed = parse_grid_key(raw)
        if parsed and parsed[0] in projects_by_id:
            keys.add(parsed)
            kept_extras.append(raw)

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
            # Surtout PAS "values" : en Jinja, row.values résout d'abord
            # l'attribut, donc la méthode dict.values — le champ s'affichait
            # vide quelles que soient les données enregistrées.
            "pcts": [values.get(d.isoformat(), 0) for d in days],
            "total": round(sum(values.values()), 1),
        })

    day_totals = [round(sum(r["pcts"][i] for r in rows), 1) for i in range(7)]
    week_total = round(sum(day_totals), 1)

    return render_template(
        "week.html", rows=rows, days=day_meta, day_totals=day_totals,
        week_total=week_total, offset=offset, projects=projects,
        tasks_by_project=tasks_by_project, start=start, end=days[-1],
        settings=settings, extras=kept_extras,
    )


def parse_grid_key(raw):
    """Décode une clé de ligne de grille « <project_id>:<task_id|none> ».
    Renvoie None si la valeur est illisible."""
    try:
        project_raw, _, task_raw = (raw or "").partition(":")
        project_id = int(project_raw)
    except (TypeError, ValueError):
        return None
    task_id = None
    if task_raw and task_raw != "none":
        try:
            task_id = int(task_raw)
        except ValueError:
            return None
    return (project_id, task_id)


@app.route("/semaine/enregistrer", methods=["POST"])
def save_week():
    settings = current_settings()
    offset = request.form.get("offset", 0)

    # Toutes les cellules sont validées AVANT la moindre écriture, puis
    # écrites en une seule transaction. Une cellule invalide n'annule pas
    # seulement sa propre écriture : elle annule toute la semaine, pour que
    # le message affiché corresponde à l'état réel de la base.
    projects_by_id = {p["id"]: p for p in db.list_projects()}
    cells = []
    try:
        for key, raw in request.form.items():
            if not key.startswith("cell-"):
                continue
            try:
                _, project_raw, task_raw, day_iso = key.split("-", 3)
                project_id = int(project_raw)
            except ValueError:
                raise FormError("Une case de la grille porte un identifiant illisible.")

            project = projects_by_id.get(project_id)
            if not project:
                continue  # projet supprimé entre l'affichage et l'envoi

            # Validation de la date : sans elle, un nom de champ trafiqué
            # écrivait une entrée à date invalide, et la fiche projet
            # tombait en 500 — sur la seule page permettant de la supprimer.
            day_iso = valid_date(day_iso, "Date de la grille")

            value = (raw or "").strip().replace(",", ".")
            percent = float(value) if value else 0.0
            if percent < 0 or percent > 300:
                raise FormError("Un pourcentage doit être compris entre 0 et 300.")

            task_id = int(task_raw) if task_raw != "none" else None
            cells.append((project_id, task_id, day_iso, percent,
                          percent_to_hours(percent, project, settings)))
    except FormError as exc:
        flash(f"{exc} Rien n'a été enregistré.", "error")
        return redirect(url_for("week", offset=offset))
    except ValueError:
        # Uniquement les conversions float()/int() des valeurs de cellules :
        # la validation en amont couvre le reste, donc plus besoin
        # d'attraper AttributeError, qui masquait de vraies erreurs.
        flash("Saisie invalide : seuls des nombres sont acceptés dans la grille. "
              "Rien n'a été enregistré.", "error")
        return redirect(url_for("week", offset=offset))

    db.set_day_totals(cells)
    flash(f"Semaine enregistrée ({len(cells)} case(s) traitée(s)).", "success")
    return redirect(url_for("week", offset=offset))


# ------------------------------------------------------------- entrées

@app.route("/entries", methods=["POST"])
def create_entry():
    settings = current_settings()
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
    return redirect(safe_next(request.form.get("next"), url_for("dashboard")))


@app.route("/entries/<int:entry_id>/edit", methods=["GET", "POST"])
def edit_entry(entry_id):
    entry = db.get_entry(entry_id)
    if not entry:
        abort(404)
    project = db.get_project(entry["project_id"])
    settings = current_settings()

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

@app.route("/jour")
def day_view():
    """Saisie d'une seule journée, une carte par projet.

    La grille hebdo fait neuf colonnes de champs numériques en scroll
    horizontal : impraticable sur téléphone, alors que la saisie du soir sur
    mobile est justement le cas d'usage principal.
    """
    settings = current_settings()
    try:
        day = calc.parse_date(request.args.get("date") or date.today().isoformat())
    except ValueError:
        day = date.today()

    projects = [p for p in db.list_projects()
                if p["status"] in ("provisional", "confirmed", "paused")]
    cells = db.week_grid_cells(day.isoformat(), day.isoformat())
    # La vue jour n'édite QUE la part « sans tâche ». Le temps rattaché à
    # une tâche est affiché à côté, en lecture seule : sinon, réenregistrer
    # un total qui inclut déjà des saisies par tâche les aurait doublées.
    rows = []
    for p in projects:
        own = cells.get((p["id"], None), {}).get(day.isoformat(), 0)
        tagged = sum(v.get(day.isoformat(), 0)
                     for (pid, task_id), v in cells.items()
                     if pid == p["id"] and task_id is not None)
        rows.append({"project": p, "pct": round(own, 0), "tagged": round(tagged, 0)})

    absence_index = calc.build_absence_index(db.list_absences())

    return render_template(
        "day.html", day=day, rows=rows,
        total=round(sum(r["pct"] + r["tagged"] for r in rows), 0),
        off_reason=(None if calc.is_working_day(day, settings, absence_index)
                    else (absence_index.get(day) or "week-end")),
        prev_day=(day - timedelta(days=1)).isoformat(),
        next_day=(day + timedelta(days=1)).isoformat(),
        weekday=WEEKDAY_NAMES[day.weekday()],
    )


@app.route("/jour/enregistrer", methods=["POST"])
def save_day():
    settings = current_settings()
    try:
        day_iso = valid_date(request.form.get("date"), "Date")
    except FormError as exc:
        flash(str(exc), "error")
        return redirect(url_for("day_view"))

    projects_by_id = {p["id"]: p for p in db.list_projects()}
    cells = []
    try:
        for key, raw in request.form.items():
            if not key.startswith("day-"):
                continue
            project = projects_by_id.get(int(key[4:]))
            if not project:
                continue
            value = (raw or "").strip().replace(",", ".")
            percent = float(value) if value else 0.0
            if percent < 0 or percent > 300:
                raise FormError("Un pourcentage doit être compris entre 0 et 300.")
            cells.append((project["id"], None, day_iso, percent,
                          percent_to_hours(percent, project, settings)))
    except (FormError, ValueError) as exc:
        message = str(exc) if isinstance(exc, FormError) else "Saisie invalide."
        flash(f"{message} Rien n'a été enregistré.", "error")
        return redirect(url_for("day_view", date=day_iso))

    db.set_day_totals(cells)
    flash("Journée enregistrée.", "success")
    return redirect(url_for("day_view", date=day_iso))


@app.route("/planning")
def planning():
    settings = current_settings()
    include_provisional = request.args.get("include_provisional", "1") != "0"
    view = request.args.get("view", "semaines")
    try:
        offset = int(request.args.get("offset", 0))
    except ValueError:
        offset = 0

    weeks = PLANNING_WEEKS
    window_start = (calc.week_monday(date.today()) - timedelta(days=7)
                    + timedelta(weeks=offset * weeks))
    window_days = weeks * 7

    all_p = db.list_projects()
    projects = [p for p in all_p if p["status"] in ("provisional", "confirmed", "paused")]
    absences = db.list_absences()

    capacity = calc.daily_capacity(all_p, window_start, window_days,
                                   include_provisional, settings, absences)
    grid = calc.allocation_grid(projects, window_start, weeks, settings, absences,
                                include_provisional)

    return render_template(
        "planning.html", grid=grid, capacity=capacity, view=view,
        capacity_summary=calc.capacity_summary(capacity),
        capacity_scale=calc.capacity_scale(capacity),
        window_days=window_days, weeks=weeks,
        rows=calc.gantt_rows(projects, window_start, window_days, settings),
        today_idx=(date.today() - window_start).days,
        week_labels=[{"idx": i, "label": (window_start + timedelta(days=i)).strftime("%d %b")}
                     for i in range(0, window_days, 7)],
        include_provisional=include_provisional,
        offset=offset, window_start=window_start,
        window_end=window_start + timedelta(days=window_days - 1),
        absences=[a for a in absences if a["end_date"] >= date.today().isoformat()],
    )


# ------------------------------------------------------------- projets

@app.route("/projects")
def projects_list():
    settings = current_settings()
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
            return render_template("project_form.html", project=None, form_data=request.form, known_clients=db.list_clients())
        data["color"] = db.next_project_color()
        project_id = db.create_project(data)
        flash("Projet créé.", "success")
        return redirect(url_for("project_detail", project_id=project_id))
    return render_template("project_form.html", project=None, form_data=None, known_clients=db.list_clients())


@app.route("/projects/<int:project_id>")
def project_detail(project_id):
    project = db.get_project(project_id)
    if not project:
        abort(404)
    settings = current_settings()
    agg = db.entries_aggregate_for(project_id)
    costs = db.list_costs(project_id)
    cost_split = {
        "absorbed": sum(c["amount"] for c in costs if not c["billable"]),
        "rebilled": sum(c["amount"] for c in costs if c["billable"]),
    }
    milestones = db.list_milestones(project_id)
    milestone_totals = db.milestone_totals_by_project().get(
        project_id, {"total": 0, "invoiced": 0, "paid": 0})

    stats = calc.project_stats(project, agg, settings, cost_split, milestone_totals)

    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    date_from = (request.args.get("from") or "").strip() or None
    date_to = (request.args.get("to") or "").strip() or None
    task_filter = (request.args.get("task") or "").strip() or None
    total_entries = db.count_entries(project_id, date_from, date_to, task_filter)
    entries = db.list_entries(project_id, limit=ENTRIES_PER_PAGE,
                              offset=(page - 1) * ENTRIES_PER_PAGE,
                              date_from=date_from, date_to=date_to, task_id=task_filter)

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
        date_from=date_from, date_to=date_to, task_filter=task_filter,
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
            return render_template("project_form.html", project=project, form_data=request.form, known_clients=db.list_clients())
        changed = db.update_project(project_id, data,
                                    scope_note=(request.form.get("scope_note") or ""))
        if changed:
            flash("Projet mis à jour. Changement de périmètre enregistré : "
                  + ", ".join(changed) + ".", "success")
        else:
            flash("Projet mis à jour.", "success")
        return redirect(url_for("project_detail", project_id=project_id))
    return render_template("project_form.html", project=project, form_data=None, known_clients=db.list_clients())


@app.route("/projects/<int:project_id>/status", methods=["POST"])
def change_status(project_id):
    try:
        db.set_project_status(project_id, request.form.get("status", ""))
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        flash("Statut mis à jour.", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/duplicate", methods=["POST"])
def duplicate_project(project_id):
    source = db.get_project(project_id)
    if not source:
        abort(404)
    # Démarrage préréglé au lendemain de la fin du projet source.
    start = (calc.planned_end_date(source) + timedelta(days=1)).isoformat()
    new_id = db.duplicate_project(project_id, start)
    flash("Projet reconduit — vérifie les dates et le prix avant de confirmer.", "success")
    return redirect(url_for("edit_project", project_id=new_id))


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
    settings = current_settings()
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

    delays = db.payment_delays()
    forecast = calc.cash_forecast(db.open_milestones(), delays, months=3, today=today)
    monthly = db.monthly_invoiced(12)

    return render_template(
        "billing.html", buckets=buckets, totals=totals, overdue=overdue,
        revenue=revenue, chart=calc.bar_chart(monthly, "amount"),
        monthly=monthly, settings=settings,
        forecast=forecast, delays=delays,
        default_delay=calc.DEFAULT_PAYMENT_DELAY,
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
        dated = (request.form.get("dated") or "").strip() or None
        if dated:
            dated = valid_date(dated, "Date")
        db.set_milestone_status(milestone_id, request.form.get("status", ""),
                                (request.form.get("invoice_ref") or "").strip() or None,
                                dated=dated)
    except (ValueError, FormError) as exc:
        flash(str(exc), "error")
    return redirect(safe_next(request.form.get("next"),
                              url_for("project_detail", project_id=milestone["project_id"])))


@app.route("/milestones/<int:milestone_id>/edit", methods=["POST"])
def edit_milestone(milestone_id):
    milestone = db.get_milestone(milestone_id)
    if not milestone:
        abort(404)
    try:
        db.update_milestone(
            milestone_id,
            req_text(request.form, "label", "Libellé du jalon", 120),
            req_float(request.form, "amount", "Montant", minimum=0),
            (request.form.get("due_date") or "").strip() or None,
        )
    except FormError as exc:
        flash(str(exc), "error")
    else:
        flash("Jalon modifié.", "success")
    return redirect(safe_next(request.form.get("next"),
                              url_for("project_detail", project_id=milestone["project_id"])))


@app.route("/milestones/<int:milestone_id>/delete", methods=["POST"])
def delete_milestone(milestone_id):
    milestone = db.get_milestone(milestone_id)
    if not milestone:
        abort(404)
    db.delete_milestone(milestone_id)
    flash("Jalon supprimé.", "success")
    return redirect(safe_next(request.form.get("next"),
                              url_for("project_detail", project_id=milestone["project_id"])))


# ------------------------------------------------------------- coûts

@app.route("/projects/<int:project_id>/costs", methods=["POST"])
def create_cost(project_id):
    try:
        db.create_cost(
            project_id,
            req_text(request.form, "label", "Libellé du coût", 120),
            req_float(request.form, "amount", "Montant", minimum=0),
            (request.form.get("cost_date") or "").strip() or None,
            billable=request.form.get("billable") == "1",
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
    settings = current_settings()
    rows = project_rows(db.list_projects(), settings)
    return render_template(
        "clients.html",
        clients=calc.client_rollup(rows, db.milestone_totals_by_project(), settings),
        cost_day_rate=calc.cost_day_rate(settings),
    )


# ------------------------------------------------------------- comparatif

@app.route("/comparatif")
def comparatif():
    settings = current_settings()
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


@app.route("/absences/jour", methods=["POST"])
def mark_day_off():
    """Déclare une journée non travaillée en un clic.

    Sert à clore l'alerte « jour ouvré sans saisie » pour un jour où il n'y
    avait effectivement rien à saisir. Une alerte qui revient chaque jour
    sans moyen de la traiter est une alerte qu'on apprend à ignorer.
    """
    try:
        day = req_date(request.form, "date", "Date")
        db.create_absence("Journée non travaillée", "indispo", day, day)
    except (FormError, ValueError) as exc:
        flash(str(exc), "error")
    else:
        flash(f"Journée du {day} déclarée non travaillée.", "success")
    return redirect(safe_next(request.form.get("next"), url_for("dashboard")))


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
            db.set_setting("annual_fixed_costs",
                           req_float(request.form, "annual_fixed_costs", "Charges fixes annuelles", 0, default=0))
            db.set_setting("billable_days_per_year",
                           req_float(request.form, "billable_days_per_year", "Jours facturables par an", 1, 366, default=180))
            db.set_setting("overrun_weeks",
                           req_float(request.form, "overrun_weeks", "Prolongation maximale", 0, 52, default=4))
        except FormError as exc:
            flash(str(exc), "error")
        else:
            flash("Réglages enregistrés.", "success")
        return redirect(url_for("settings_page"))

    settings = current_settings()
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
        # Un générateur de PDF maison serait hors-jeu d'ici 2027 avec la
        # facturation électronique obligatoire au format structuré. Le bon
        # investissement est que Timing reste la source de vérité et exporte
        # proprement vers l'outil qui émettra.
        "to-invoice": (db.export_to_invoice, f"timing-a-facturer-{stamp}.csv"),
    }
    if kind not in exporters:
        abort(404)
    fetch, filename = exporters[kind]
    return csv_response(fetch(), filename)


@app.route("/export/backup")
def backup():
    """Sauvegarde complète et cohérente de la base.

    Passe par sqlite3.Connection.backup() plutôt que d'envoyer le fichier
    principal tel quel : en mode WAL, les transactions récentes vivent dans
    le fichier -wal et n'auraient pas été incluses. La sauvegarde téléchargée
    aurait pu être en retard sur la base réelle.
    """
    if not db.DB_PATH.exists():
        abort(404)
    handle = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    handle.close()
    db.backup_to(handle.name)

    @after_this_request
    def _cleanup(response):
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        return response

    return send_file(handle.name, as_attachment=True, mimetype="application/vnd.sqlite3",
                     download_name=f"timing-sauvegarde-{date.today().isoformat()}.sqlite3")


if __name__ == "__main__":
    db.init_db()
    port = int(os.environ.get("TIMING_PORT", 5062))
    print(f"Timing disponible sur http://127.0.0.1:{port}")
    # debug=False : le débogueur Werkzeug permet d'exécuter du Python
    # arbitraire depuis une page d'erreur. Mets TIMING_DEBUG=1 uniquement
    # quand tu développes.
    app.run(debug=os.environ.get("TIMING_DEBUG") == "1", port=port, threaded=True)
