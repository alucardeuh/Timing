from datetime import date, datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash, abort

import db
import calculations as calc

app = Flask(__name__)
app.secret_key = "timing-local-dev-key"

TASK_TAG_NONE = "Sans tâche"
PLANNING_WINDOW_DAYS = 91  # 13 semaines
HOME_WINDOW_DAYS = 56  # 8 semaines


# ---------------------------------------------------------------- helpers

def get_project_or_404(project_id):
    conn = db.get_db()
    project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if project is None:
        abort(404)
    return project


def get_entries(project_id):
    conn = db.get_db()
    rows = conn.execute(
        "SELECT * FROM entries WHERE project_id = ? ORDER BY entry_date DESC, id DESC",
        (project_id,),
    ).fetchall()
    conn.close()
    return rows


def get_tasks(project_id, include_archived=False):
    conn = db.get_db()
    if include_archived:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY name", (project_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? AND archived = 0 ORDER BY name",
            (project_id,),
        ).fetchall()
    conn.close()
    return rows


def tasks_by_id(project_id):
    return {t["id"]: t for t in get_tasks(project_id, include_archived=True)}


def all_projects(status=None):
    conn = db.get_db()
    if status:
        rows = conn.execute(
            "SELECT * FROM projects WHERE status = ? ORDER BY start_date", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM projects ORDER BY start_date").fetchall()
    conn.close()
    return rows


def parse_project_form(form):
    duration_unit = form.get("duration_unit", "weeks")
    days_per_week = float(form.get("days_per_week"))
    duration_value = float(form.get("duration_value"))
    day_rate = form.get("day_rate") or None
    price_total = form.get("price_total") or None
    hours_per_day = form.get("hours_per_day") or None

    tmp_project = {
        "days_per_week": days_per_week,
        "duration_value": duration_value,
        "duration_unit": duration_unit,
    }
    total_days = calc.total_days_sold(tmp_project)

    if day_rate:
        day_rate = float(day_rate)
        price_total = round(day_rate * total_days, 2)
    elif price_total:
        price_total = float(price_total)
        day_rate = None
    else:
        price_total = 0.0
        day_rate = None

    return {
        "name": form.get("name", "").strip(),
        "client": form.get("client", "").strip(),
        "status": form.get("status", "provisional"),
        "days_per_week": days_per_week,
        "duration_value": duration_value,
        "duration_unit": duration_unit,
        "day_rate": day_rate,
        "price_total": price_total,
        "hours_per_day": float(hours_per_day) if hours_per_day else None,
        "start_date": form.get("start_date") or date.today().isoformat(),
        "notes": form.get("notes", "").strip(),
    }


def today_logged_summary():
    """Ce qui a déjà été saisi aujourd'hui, groupé par projet."""
    conn = db.get_db()
    rows = conn.execute(
        "SELECT p.name AS project_name, SUM(e.percent_of_day) AS total_pct "
        "FROM entries e JOIN projects p ON p.id = e.project_id "
        "WHERE e.entry_date = ? GROUP BY e.project_id ORDER BY total_pct DESC",
        (date.today().isoformat(),),
    ).fetchall()
    conn.close()
    return rows


def week_monday(d):
    return d - timedelta(days=d.weekday())


# ------------------------------------------------------------------ views

@app.route("/")
def dashboard():
    settings = db.get_settings()
    loggable = [p for p in all_projects() if p["status"] in ("provisional", "confirmed")]
    active = [p for p in all_projects() if p["status"] == "confirmed"]
    provisional = [p for p in all_projects() if p["status"] == "provisional"]

    cards = []
    for p in active + provisional:
        entries = get_entries(p["id"])
        stats = calc.project_stats(p, entries, settings)
        tally = calc.tally_segments(p, entries, settings)
        cards.append({"project": p, "stats": stats, "tally": tally})
    order = {"behind": 0, "on_track": 1, "ahead": 2}
    cards.sort(key=lambda c: order.get(c["stats"]["pace_status"], 1))

    include_provisional = request.args.get("include_provisional", "1") != "0"
    window_start = week_monday(date.today())
    capacity = calc.daily_capacity(
        all_projects(), window_start, HOME_WINDOW_DAYS, include_provisional, settings
    )

    ranked = []
    for p in all_projects():
        entries = get_entries(p["id"])
        idx = calc.rentability_index(p, entries, settings)
        if idx is not None:
            ranked.append({"project": p, "index": idx})
    ranked.sort(key=lambda r: r["index"], reverse=True)

    return render_template(
        "dashboard.html",
        cards=cards,
        ranked=ranked[:3],
        loggable=loggable,
        today=date.today().isoformat(),
        capacity=capacity,
        include_provisional=include_provisional,
        today_logged=today_logged_summary(),
        settings=settings,
    )


@app.route("/entries", methods=["POST"])
def create_entry():
    project = get_project_or_404(int(request.form.get("project_id")))
    settings = db.get_settings()
    percent = float(request.form.get("percent_of_day"))
    hpd = calc.hours_per_day(project, settings)
    hours = round(percent / 100 * hpd, 2)
    task_id = request.form.get("task_id") or None

    conn = db.get_db()
    conn.execute(
        "INSERT INTO entries (project_id, task_id, entry_date, percent_of_day, hours, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            project["id"], task_id,
            request.form.get("entry_date") or date.today().isoformat(),
            percent, hours,
            request.form.get("note", "").strip(),
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    flash("Entrée ajoutée.", "success")
    return redirect(request.form.get("next") or url_for("dashboard"))


@app.route("/entries/<int:entry_id>/delete", methods=["POST"])
def delete_entry(entry_id):
    conn = db.get_db()
    entry = conn.execute("SELECT project_id FROM entries WHERE id = ?", (entry_id,)).fetchone()
    conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()
    flash("Entrée supprimée.", "success")
    if entry:
        return redirect(url_for("project_detail", project_id=entry["project_id"]))
    return redirect(url_for("dashboard"))


@app.route("/planning")
def planning():
    settings = db.get_settings()
    include_provisional = request.args.get("include_provisional", "1") != "0"
    offset = int(request.args.get("offset", 0))
    window_start = week_monday(date.today()) - timedelta(days=7) + timedelta(days=offset * PLANNING_WINDOW_DAYS)

    projects = [p for p in all_projects() if p["status"] in ("provisional", "confirmed", "paused")]
    rows = calc.gantt_rows(projects, window_start, PLANNING_WINDOW_DAYS)
    capacity = calc.daily_capacity(all_projects(), window_start, PLANNING_WINDOW_DAYS, include_provisional, settings)

    today_idx = (date.today() - window_start).days
    week_labels = []
    for i in range(0, PLANNING_WINDOW_DAYS, 7):
        week_labels.append({"idx": i, "label": (window_start + timedelta(days=i)).strftime("%d %b")})
    window_end = window_start + timedelta(days=PLANNING_WINDOW_DAYS - 1)

    return render_template(
        "planning.html",
        rows=rows,
        capacity=capacity,
        window_days=PLANNING_WINDOW_DAYS,
        today_idx=today_idx,
        week_labels=week_labels,
        include_provisional=include_provisional,
        offset=offset,
        window_start=window_start,
        window_end=window_end,
    )


@app.route("/projects")
def projects_list():
    status_filter = request.args.get("status", "confirmed")
    settings = db.get_settings()
    projects = all_projects(status=status_filter if status_filter != "all" else None)
    rows = []
    for p in projects:
        entries = get_entries(p["id"])
        stats = calc.project_stats(p, entries, settings)
        rows.append({"project": p, "stats": stats})
    return render_template("projects.html", rows=rows, status_filter=status_filter)


@app.route("/projects/new", methods=["GET", "POST"])
def new_project():
    if request.method == "POST":
        data = parse_project_form(request.form)
        if not data["name"]:
            flash("Le projet a besoin d'un nom.", "error")
            return render_template("project_form.html", project=None, form_data=data)
        conn = db.get_db()
        color = db.next_project_color()
        conn.execute(
            "INSERT INTO projects "
            "(name, client, status, days_per_week, duration_value, duration_unit, "
            "day_rate, price_total, hours_per_day, start_date, color, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["name"], data["client"], data["status"], data["days_per_week"],
                data["duration_value"], data["duration_unit"], data["day_rate"],
                data["price_total"], data["hours_per_day"], data["start_date"],
                color, data["notes"], datetime.now().isoformat(),
            ),
        )
        conn.commit()
        new_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.close()
        flash("Projet créé.", "success")
        return redirect(url_for("project_detail", project_id=new_id))

    return render_template("project_form.html", project=None, form_data=None)


@app.route("/projects/<int:project_id>")
def project_detail(project_id):
    project = get_project_or_404(project_id)
    entries = get_entries(project_id)
    settings = db.get_settings()
    stats = calc.project_stats(project, entries, settings)
    tally = calc.tally_segments(project, entries, settings)
    tasks = get_tasks(project_id)
    t_by_id = tasks_by_id(project_id)
    breakdown = calc.task_breakdown(entries, t_by_id)
    burndown = calc.burndown_points(project, entries, settings)
    task_names = {tid: t["name"] for tid, t in t_by_id.items()}
    return render_template(
        "project_detail.html",
        project=project,
        entries=entries,
        stats=stats,
        tally=tally,
        tasks=tasks,
        breakdown=breakdown,
        burndown=burndown,
        task_names=task_names,
        today=date.today().isoformat(),
        settings=settings,
    )


@app.route("/projects/<int:project_id>/edit", methods=["GET", "POST"])
def edit_project(project_id):
    project = get_project_or_404(project_id)
    if request.method == "POST":
        data = parse_project_form(request.form)
        conn = db.get_db()
        conn.execute(
            "UPDATE projects SET name=?, client=?, status=?, days_per_week=?, duration_value=?, "
            "duration_unit=?, day_rate=?, price_total=?, hours_per_day=?, start_date=?, notes=? "
            "WHERE id=?",
            (
                data["name"], data["client"], data["status"], data["days_per_week"],
                data["duration_value"], data["duration_unit"], data["day_rate"],
                data["price_total"], data["hours_per_day"], data["start_date"],
                data["notes"], project_id,
            ),
        )
        conn.commit()
        conn.close()
        flash("Projet mis à jour.", "success")
        return redirect(url_for("project_detail", project_id=project_id))

    return render_template("project_form.html", project=project, form_data=None)


@app.route("/projects/<int:project_id>/status", methods=["POST"])
def set_project_status(project_id):
    new_status = request.form.get("status")
    conn = db.get_db()
    conn.execute("UPDATE projects SET status=? WHERE id=?", (new_status, project_id))
    conn.commit()
    conn.close()
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/delete", methods=["POST"])
def delete_project(project_id):
    conn = db.get_db()
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()
    flash("Projet supprimé.", "success")
    return redirect(url_for("projects_list"))


@app.route("/projects/<int:project_id>/tasks", methods=["POST"])
def create_task(project_id):
    name = request.form.get("name", "").strip()
    if name:
        conn = db.get_db()
        conn.execute(
            "INSERT INTO tasks (project_id, name, archived, created_at) VALUES (?, ?, 0, ?)",
            (project_id, name, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        flash("Tâche ajoutée.", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/tasks/<int:task_id>/archive", methods=["POST"])
def archive_task(task_id):
    conn = db.get_db()
    task = conn.execute("SELECT project_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.execute("UPDATE tasks SET archived = 1 WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    if task:
        return redirect(url_for("project_detail", project_id=task["project_id"]))
    return redirect(url_for("dashboard"))


@app.route("/comparatif")
def comparatif():
    settings = db.get_settings()
    tab = request.args.get("tab", "rentabilite")

    rows = []
    for p in all_projects():
        entries = get_entries(p["id"])
        stats = calc.project_stats(p, entries, settings)
        rows.append({"project": p, "stats": stats})
    rows.sort(
        key=lambda r: (r["stats"]["rentability_index"] is None, -(r["stats"]["rentability_index"] or 0))
    )

    task_totals = {}
    for p in all_projects():
        entries = get_entries(p["id"])
        t_by_id = tasks_by_id(p["id"])
        for row in calc.task_breakdown(entries, t_by_id):
            key = row["name"]
            task_totals[key] = task_totals.get(key, 0) + row["hours"]
    task_rows = sorted(
        [{"name": k, "hours": round(v, 2)} for k, v in task_totals.items()],
        key=lambda r: r["hours"], reverse=True,
    )
    grand_total = sum(r["hours"] for r in task_rows) or 1
    for r in task_rows:
        r["pct"] = round(r["hours"] / grand_total * 100, 1)

    return render_template("comparatif.html", rows=rows, task_rows=task_rows, tab=tab)


@app.route("/reglages", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        db.set_setting("default_hours_per_day", float(request.form.get("default_hours_per_day")))
        db.set_setting("currency_symbol", request.form.get("currency_symbol", "€").strip() or "€")
        db.set_setting("peak_threshold_warning", float(request.form.get("peak_threshold_warning")))
        db.set_setting("peak_threshold_danger", float(request.form.get("peak_threshold_danger")))
        flash("Réglages enregistrés.", "success")
        return redirect(url_for("settings_page"))
    return render_template("settings.html", settings=db.get_settings())


@app.context_processor
def inject_globals():
    return {
        "currency": db.get_settings()["currency_symbol"],
        "today": date.today().isoformat(),
    }


if __name__ == "__main__":
    db.init_db()
    app.run(debug=True, port=5062)
