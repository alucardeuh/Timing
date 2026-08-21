"""
Accès à la base. Point d'entrée UNIQUE : aucun autre module du projet
n'ouvre de connexion sqlite3 ni n'écrit de SQL. Les routes appellent ces
fonctions, jamais la base directement.

Deux principes qui expliquent la forme du fichier :

1. Les agrégats sont calculés en SQL, pas en Python. Charger toutes les
   entrées de tous les projets pour en faire des sommes coûtait une requête
   par projet ; une seule requête GROUP BY fait le même travail.
2. La migration est automatique et idempotente. Ajouter une colonne au
   schéma ne doit jamais demander de supprimer la base existante.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# Surchargeable par TIMING_DB : les tests tournent sur une base jetable.
DB_PATH = Path(os.environ.get("TIMING_DB") or BASE_DIR / "instance" / "timing.sqlite3")
SCHEMA_PATH = BASE_DIR / "schema.sql"

DEFAULT_SETTINGS = {
    "default_hours_per_day": "7",
    "currency_symbol": "€",
    "peak_threshold_warning": "75",
    "peak_threshold_danger": "100",
    # Jours de la semaine travaillés, index Python (0 = lundi ... 6 = dimanche).
    # C'est le diviseur de la charge : un projet à 5 j/semaine sur 5 jours
    # travaillés remplit 100 % de chaque jour ouvré, et 0 % du week-end.
    "working_days": "0,1,2,3,4",
    # Seuil de consommation en dessous duquel l'indice de rentabilité n'est
    # pas affiché : sur 2 h saisies, le taux réel est absurde.
    "min_consumption_pct": "20",
    # Alerte quand un projet dépasse ce % de son budget vendu.
    "budget_alert_pct": "80",
    "monthly_revenue_goal": "0",
    "annual_revenue_goal": "0",
}

PROJECT_COLORS = [
    "#3E8E82",  # teal
    "#4A6FA5",  # bleu
    "#8B5FBF",  # violet
    "#C97B3D",  # ambre
    "#B5577A",  # rose
    "#4E9B5C",  # vert
    "#6B6FCE",  # indigo
    "#C25B4A",  # corail
]

FLOAT_SETTINGS = {
    "default_hours_per_day", "peak_threshold_warning", "peak_threshold_danger",
    "min_consumption_pct", "budget_alert_pct", "monthly_revenue_goal",
    "annual_revenue_goal",
}

PROJECT_STATUSES = ("provisional", "confirmed", "paused", "completed")
MILESTONE_STATUSES = ("todo", "invoiced", "paid")
ABSENCE_KINDS = ("conges", "ferie", "indispo")

# Colonnes ajoutées après la première version. Chaque entrée est appliquée
# uniquement si la colonne manque — une base créée aujourd'hui les a déjà
# via schema.sql, une base plus ancienne les reçoit au démarrage.
MIGRATIONS = [
    ("projects", "archived", "INTEGER NOT NULL DEFAULT 0"),
    ("projects", "updated_at", "TEXT"),
    ("entries", "updated_at", "TEXT"),
    ("entries", "hours", "REAL NOT NULL DEFAULT 0"),
]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # timeout : plusieurs requêtes peuvent se croiser (page ouverte +
    # formulaire envoyé). Sans lui, SQLite lève "database is locked"
    # immédiatement au lieu d'attendre son tour.
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    # WAL : les lectures ne sont plus bloquées par une écriture en cours.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    # Ordre imposé : tables → migrations → index.
    #
    # Sur une base déjà existante, CREATE TABLE IF NOT EXISTS ne fait rien :
    # les colonnes ajoutées en V2 (archived, updated_at) n'arrivent que par
    # la migration. Or un index porte sur `archived`. Le créer avant la
    # migration échouait avec "no such column: archived" — sur une base
    # vierge le problème était invisible, puisque CREATE TABLE créait
    # directement la colonne.
    tables_sql, _, indexes_sql = SCHEMA_PATH.read_text(encoding="utf-8").partition("-- @INDEXES")
    conn.executescript(tables_sql)
    _apply_migrations(conn)
    if indexes_sql.strip():
        conn.executescript(indexes_sql)

    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def _apply_migrations(conn):
    for table, column, ddl in MIGRATIONS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table pas encore créée sur cette base
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


# --------------------------------------------------------------- réglages

def get_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    settings = dict(DEFAULT_SETTINGS)
    settings.update({r["key"]: r["value"] for r in rows})
    for key in FLOAT_SETTINGS:
        try:
            settings[key] = float(settings[key])
        except (TypeError, ValueError):
            settings[key] = float(DEFAULT_SETTINGS[key])
    return settings


def set_setting(key, value):
    conn = get_db()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()
    conn.close()


def next_project_color():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
    conn.close()
    return PROJECT_COLORS[n % len(PROJECT_COLORS)]


# --------------------------------------------------------------- projets

def list_projects(status=None, archived=False, search=None, client=None):
    clauses = ["archived = ?"]
    params = [1 if archived else 0]
    if status and status != "all":
        clauses.append("status = ?")
        params.append(status)
    if search:
        clauses.append("(name LIKE ? OR client LIKE ? OR notes LIKE ?)")
        params.extend([f"%{search}%"] * 3)
    if client:
        clauses.append("COALESCE(client, '') = ?")
        params.append(client)
    conn = get_db()
    rows = conn.execute(
        f"SELECT * FROM projects WHERE {' AND '.join(clauses)} ORDER BY start_date DESC, id DESC",
        params,
    ).fetchall()
    conn.close()
    return rows


def counts_by_status():
    conn = get_db()
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM projects WHERE archived = 0 GROUP BY status"
    ).fetchall()
    trash = conn.execute("SELECT COUNT(*) AS n FROM projects WHERE archived = 1").fetchone()["n"]
    conn.close()
    counts = {s: 0 for s in PROJECT_STATUSES}
    counts.update({r["status"]: r["n"] for r in rows})
    counts["all"] = sum(counts[s] for s in PROJECT_STATUSES)
    counts["trash"] = trash
    return counts


def get_project(project_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    return row


def list_clients():
    """Clients distincts avec leur nombre de projets — alimente le filtre
    et la page Clients. Les projets sans client sont regroupés sous ''."""
    conn = get_db()
    rows = conn.execute(
        "SELECT COALESCE(client, '') AS client, COUNT(*) AS n "
        "FROM projects WHERE archived = 0 GROUP BY COALESCE(client, '') ORDER BY client"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_project(data):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO projects (name, client, status, days_per_week, duration_value, "
        "duration_unit, day_rate, price_total, hours_per_day, start_date, color, notes, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            data["name"], data["client"], data["status"], data["days_per_week"],
            data["duration_value"], data["duration_unit"], data["day_rate"],
            data["price_total"], data["hours_per_day"], data["start_date"],
            data.get("color") or PROJECT_COLORS[0], data["notes"], now_iso(), now_iso(),
        ),
    )
    project_id = cur.lastrowid
    conn.commit()
    conn.close()
    return project_id


# Champs dont une modification laisse une trace dans scope_changes : ceux
# qui changent le contrat (volume vendu ou prix), pas la présentation.
TRACKED_SCOPE_FIELDS = {
    "days_per_week": "Jours / semaine",
    "duration_value": "Durée",
    "price_total": "Prix total",
}


def _fmt_scope_value(value):
    """Normalise une valeur de périmètre avant archivage.

    Sans ça, la même modification s'enregistrait « 10 » ou « 10.0 » selon le
    type reçu, et deux traces du même changement n'étaient pas comparables.
    """
    number = float(value or 0)
    return str(int(number)) if number == int(number) else f"{number:g}"


def update_project(project_id, data, scope_note=""):
    """Met à jour un projet et journalise les changements de périmètre.

    Retourne la liste des champs de périmètre modifiés, pour que la route
    puisse le signaler plutôt que de modifier en silence.
    """
    before = get_project(project_id)
    changed = []
    conn = get_db()
    conn.execute(
        "UPDATE projects SET name=?, client=?, status=?, days_per_week=?, duration_value=?, "
        "duration_unit=?, day_rate=?, price_total=?, hours_per_day=?, start_date=?, notes=?, "
        "updated_at=? WHERE id=?",
        (
            data["name"], data["client"], data["status"], data["days_per_week"],
            data["duration_value"], data["duration_unit"], data["day_rate"],
            data["price_total"], data["hours_per_day"], data["start_date"],
            data["notes"], now_iso(), project_id,
        ),
    )
    if before:
        for field, label in TRACKED_SCOPE_FIELDS.items():
            old, new = before[field], data[field]
            if float(old or 0) != float(new or 0):
                conn.execute(
                    "INSERT INTO scope_changes (project_id, field, old_value, new_value, note, changed_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (project_id, label, _fmt_scope_value(old), _fmt_scope_value(new),
                     scope_note.strip(), now_iso()),
                )
                changed.append(label)
    conn.commit()
    conn.close()
    return changed


def set_project_status(project_id, status):
    if status not in PROJECT_STATUSES:
        raise ValueError(f"Statut inconnu : {status!r}")
    conn = get_db()
    conn.execute("UPDATE projects SET status=?, updated_at=? WHERE id=?",
                 (status, now_iso(), project_id))
    conn.commit()
    conn.close()


def archive_project(project_id, archived=True):
    conn = get_db()
    conn.execute("UPDATE projects SET archived=?, updated_at=? WHERE id=?",
                 (1 if archived else 0, now_iso(), project_id))
    conn.commit()
    conn.close()


def delete_project_forever(project_id):
    conn = get_db()
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()


def list_scope_changes(project_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM scope_changes WHERE project_id = ? ORDER BY changed_at DESC", (project_id,)
    ).fetchall()
    conn.close()
    return rows


# --------------------------------------------------------------- entrées

def list_entries(project_id, limit=None, offset=0):
    conn = get_db()
    sql = "SELECT * FROM entries WHERE project_id = ? ORDER BY entry_date DESC, id DESC"
    params = [project_id]
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def count_entries(project_id):
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) AS n FROM entries WHERE project_id = ?",
                     (project_id,)).fetchone()["n"]
    conn.close()
    return n


def get_entry(entry_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return row


def create_entry(project_id, entry_date, percent, hours, task_id=None, note=""):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO entries (project_id, task_id, entry_date, percent_of_day, hours, note, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (project_id, task_id, entry_date, percent, hours, note, now_iso()),
    )
    entry_id = cur.lastrowid
    conn.commit()
    conn.close()
    return entry_id


def update_entry(entry_id, entry_date, percent, hours, task_id=None, note=""):
    conn = get_db()
    conn.execute(
        "UPDATE entries SET entry_date=?, percent_of_day=?, hours=?, task_id=?, note=?, updated_at=? "
        "WHERE id=?",
        (entry_date, percent, hours, task_id, note, now_iso(), entry_id),
    )
    conn.commit()
    conn.close()


def delete_entry(entry_id):
    conn = get_db()
    conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()


def entries_aggregate_by_project():
    """{project_id: {'percent_sum', 'entries_count', 'first_date', 'last_date'}}

    Une seule requête pour tous les projets. C'est ce qui remplace le
    "get_entries() dans une boucle" qui ouvrait une connexion par projet.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT project_id, SUM(percent_of_day) AS percent_sum, COUNT(*) AS n, "
        "MIN(entry_date) AS first_date, MAX(entry_date) AS last_date "
        "FROM entries GROUP BY project_id"
    ).fetchall()
    conn.close()
    return {
        r["project_id"]: {
            "percent_sum": r["percent_sum"] or 0.0,
            "entries_count": r["n"],
            "first_date": r["first_date"],
            "last_date": r["last_date"],
        }
        for r in rows
    }


def entries_aggregate_for(project_id):
    conn = get_db()
    r = conn.execute(
        "SELECT SUM(percent_of_day) AS percent_sum, COUNT(*) AS n, "
        "MIN(entry_date) AS first_date, MAX(entry_date) AS last_date "
        "FROM entries WHERE project_id = ?", (project_id,)
    ).fetchone()
    conn.close()
    return {
        "percent_sum": r["percent_sum"] or 0.0,
        "entries_count": r["n"] or 0,
        "first_date": r["first_date"],
        "last_date": r["last_date"],
    }


def cumulative_entries(project_id):
    """Consommation d'un projet par jour, triée — le minimum nécessaire
    pour tracer la courbe de cadence."""
    conn = get_db()
    rows = conn.execute(
        "SELECT entry_date, SUM(percent_of_day) AS percent_of_day FROM entries "
        "WHERE project_id = ? GROUP BY entry_date ORDER BY entry_date", (project_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def entries_by_day(start_date, end_date):
    """{date_iso: pourcentage total saisi} — sert à repérer les jours non
    saisis sans charger les entrées une par une."""
    conn = get_db()
    rows = conn.execute(
        "SELECT entry_date, SUM(percent_of_day) AS pct FROM entries "
        "WHERE entry_date BETWEEN ? AND ? GROUP BY entry_date",
        (start_date, end_date),
    ).fetchall()
    conn.close()
    return {r["entry_date"]: r["pct"] or 0.0 for r in rows}


def today_summary(day_iso):
    conn = get_db()
    rows = conn.execute(
        "SELECT p.name AS project_name, SUM(e.percent_of_day) AS total_pct "
        "FROM entries e JOIN projects p ON p.id = e.project_id "
        "WHERE e.entry_date = ? GROUP BY e.project_id ORDER BY total_pct DESC",
        (day_iso,),
    ).fetchall()
    conn.close()
    return rows


def monthly_activity(months=12):
    """[{'month': '2026-03', 'days': 12.5}] — consommation par mois, tous
    projets confondus. Alimente le graphique du Comparatif."""
    conn = get_db()
    rows = conn.execute(
        "SELECT substr(entry_date, 1, 7) AS month, SUM(percent_of_day) / 100.0 AS days "
        "FROM entries GROUP BY month ORDER BY month DESC LIMIT ?", (months,)
    ).fetchall()
    conn.close()
    return [{"month": r["month"], "days": round(r["days"], 2)} for r in reversed(rows)]


# --------------------------------------------------------------- grille hebdo

def week_grid_cells(start_iso, end_iso):
    """{(project_id, task_id): {date_iso: pct}} sur la semaine.

    Regrouper par (projet, tâche) plutôt que par projet seul est ce qui rend
    la grille réversible : une cellule correspond à une clé stable, donc
    l'éditer ne peut pas écraser une saisie faite sur une autre tâche.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT project_id, task_id, entry_date, SUM(percent_of_day) AS pct "
        "FROM entries WHERE entry_date BETWEEN ? AND ? "
        "GROUP BY project_id, task_id, entry_date",
        (start_iso, end_iso),
    ).fetchall()
    conn.close()
    grid = {}
    for r in rows:
        grid.setdefault((r["project_id"], r["task_id"]), {})[r["entry_date"]] = r["pct"]
    return grid


def set_day_total(project_id, task_id, entry_date, percent, hours):
    """Fixe le total d'une case de la grille hebdo.

    S'il existe déjà plusieurs entrées pour cette clé (saisies une par une
    dans la journée), la première est mise à jour et les autres supprimées :
    la grille affiche un total, elle doit donc pouvoir en écrire un. Les
    saisies détaillées avec notes se font depuis la fiche projet.
    """
    conn = get_db()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM entries WHERE project_id = ? AND entry_date = ? AND task_id IS ? ORDER BY id",
        (project_id, entry_date, task_id),
    ).fetchall()]

    if percent <= 0:
        conn.executemany("DELETE FROM entries WHERE id = ?", [(i,) for i in ids])
    elif ids:
        conn.execute(
            "UPDATE entries SET percent_of_day=?, hours=?, updated_at=? WHERE id=?",
            (percent, hours, now_iso(), ids[0]),
        )
        conn.executemany("DELETE FROM entries WHERE id = ?", [(i,) for i in ids[1:]])
    else:
        conn.execute(
            "INSERT INTO entries (project_id, task_id, entry_date, percent_of_day, hours, note, created_at) "
            "VALUES (?,?,?,?,?,'',?)",
            (project_id, task_id, entry_date, percent, hours, now_iso()),
        )
    conn.commit()
    conn.close()


# --------------------------------------------------------------- tâches

def list_tasks(project_id, include_archived=False):
    conn = get_db()
    sql = "SELECT * FROM tasks WHERE project_id = ?"
    if not include_archived:
        sql += " AND archived = 0"
    sql += " ORDER BY archived, name"
    rows = conn.execute(sql, (project_id,)).fetchall()
    conn.close()
    return rows


def list_all_active_tasks():
    """Toutes les tâches non archivées, indexées par projet — la grille
    hebdo en a besoin pour tous les projets d'un coup."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM tasks WHERE archived = 0 ORDER BY name").fetchall()
    conn.close()
    by_project = {}
    for r in rows:
        by_project.setdefault(r["project_id"], []).append(dict(r))
    return by_project


def task_names():
    conn = get_db()
    rows = conn.execute("SELECT id, name FROM tasks").fetchall()
    conn.close()
    return {r["id"]: r["name"] for r in rows}


def create_task(project_id, name):
    conn = get_db()
    conn.execute("INSERT INTO tasks (project_id, name, archived, created_at) VALUES (?,?,0,?)",
                 (project_id, name, now_iso()))
    conn.commit()
    conn.close()


def rename_task(task_id, name):
    conn = get_db()
    conn.execute("UPDATE tasks SET name = ? WHERE id = ?", (name, task_id))
    conn.commit()
    conn.close()


def set_task_archived(task_id, archived=True):
    conn = get_db()
    conn.execute("UPDATE tasks SET archived = ? WHERE id = ?", (1 if archived else 0, task_id))
    conn.commit()
    conn.close()


def delete_task(task_id):
    """Supprime la tâche ; les entrées rattachées sont conservées et
    repassent en 'Sans tâche' (ON DELETE SET NULL). Du temps saisi ne doit
    jamais disparaître avec une étiquette."""
    conn = get_db()
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def get_task_project(task_id):
    conn = get_db()
    row = conn.execute("SELECT project_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return row["project_id"] if row else None


def task_breakdown(project_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT e.task_id, COALESCE(t.name, 'Sans tâche') AS name, "
        "SUM(e.percent_of_day) / 100.0 AS days "
        "FROM entries e LEFT JOIN tasks t ON t.id = e.task_id "
        "WHERE e.project_id = ? GROUP BY e.task_id ORDER BY days DESC", (project_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def task_breakdown_global():
    conn = get_db()
    rows = conn.execute(
        "SELECT COALESCE(t.name, 'Sans tâche') AS name, SUM(e.percent_of_day) / 100.0 AS days "
        "FROM entries e LEFT JOIN tasks t ON t.id = e.task_id "
        "GROUP BY COALESCE(t.name, 'Sans tâche') ORDER BY days DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --------------------------------------------------------------- absences

def list_absences(upcoming_only=False, today_iso=None):
    conn = get_db()
    if upcoming_only:
        rows = conn.execute(
            "SELECT * FROM absences WHERE end_date >= ? ORDER BY start_date", (today_iso,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM absences ORDER BY start_date DESC").fetchall()
    conn.close()
    return rows


def create_absence(label, kind, start_date, end_date):
    if kind not in ABSENCE_KINDS:
        raise ValueError(f"Type d'absence inconnu : {kind!r}")
    conn = get_db()
    conn.execute(
        "INSERT INTO absences (label, kind, start_date, end_date, created_at) VALUES (?,?,?,?,?)",
        (label, kind, start_date, end_date, now_iso()),
    )
    conn.commit()
    conn.close()


def delete_absence(absence_id):
    conn = get_db()
    conn.execute("DELETE FROM absences WHERE id = ?", (absence_id,))
    conn.commit()
    conn.close()


# --------------------------------------------------------------- facturation

def list_milestones(project_id=None):
    conn = get_db()
    if project_id is None:
        rows = conn.execute(
            "SELECT m.*, p.name AS project_name, p.client AS project_client, p.color AS project_color "
            "FROM milestones m JOIN projects p ON p.id = m.project_id "
            "WHERE p.archived = 0 ORDER BY COALESCE(m.due_date, '9999'), m.id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM milestones WHERE project_id = ? ORDER BY COALESCE(due_date, '9999'), id",
            (project_id,),
        ).fetchall()
    conn.close()
    return rows


def create_milestone(project_id, label, amount, due_date):
    conn = get_db()
    conn.execute(
        "INSERT INTO milestones (project_id, label, amount, due_date, status, created_at) "
        "VALUES (?,?,?,?,'todo',?)",
        (project_id, label, amount, due_date or None, now_iso()),
    )
    conn.commit()
    conn.close()


def set_milestone_status(milestone_id, status, invoice_ref=None):
    if status not in MILESTONE_STATUSES:
        raise ValueError(f"Statut de jalon inconnu : {status!r}")
    conn = get_db()
    fields = {"status": status}
    today = now_iso()[:10]
    if status == "invoiced":
        fields["invoiced_at"] = today
    if status == "paid":
        fields["paid_at"] = today
        # Un jalon payé a forcément été facturé : on complète la date
        # manquante plutôt que de laisser un trou dans l'historique de CA.
        row = conn.execute("SELECT invoiced_at FROM milestones WHERE id = ?",
                           (milestone_id,)).fetchone()
        if row and not row["invoiced_at"]:
            fields["invoiced_at"] = today
    if status == "todo":
        fields["invoiced_at"] = None
        fields["paid_at"] = None
    if invoice_ref is not None:
        fields["invoice_ref"] = invoice_ref
    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE milestones SET {assignments} WHERE id = ?",
                 [*fields.values(), milestone_id])
    conn.commit()
    conn.close()


def delete_milestone(milestone_id):
    conn = get_db()
    conn.execute("DELETE FROM milestones WHERE id = ?", (milestone_id,))
    conn.commit()
    conn.close()


def get_milestone(milestone_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
    conn.close()
    return row


def milestone_totals_by_project():
    conn = get_db()
    rows = conn.execute(
        "SELECT project_id, SUM(amount) AS total, "
        "SUM(CASE WHEN status IN ('invoiced','paid') THEN amount ELSE 0 END) AS invoiced, "
        "SUM(CASE WHEN status = 'paid' THEN amount ELSE 0 END) AS paid "
        "FROM milestones GROUP BY project_id"
    ).fetchall()
    conn.close()
    return {r["project_id"]: {"total": r["total"] or 0, "invoiced": r["invoiced"] or 0,
                             "paid": r["paid"] or 0} for r in rows}


def monthly_invoiced(months=12):
    """CA facturé par mois, daté à la facturation (pas à l'encaissement)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT substr(invoiced_at, 1, 7) AS month, SUM(amount) AS amount "
        "FROM milestones WHERE invoiced_at IS NOT NULL AND invoiced_at != '' "
        "GROUP BY month ORDER BY month DESC LIMIT ?", (months,)
    ).fetchall()
    conn.close()
    return [{"month": r["month"], "amount": round(r["amount"] or 0, 2)} for r in reversed(rows)]


def invoiced_between(start_iso, end_iso):
    conn = get_db()
    r = conn.execute(
        "SELECT SUM(amount) AS amount FROM milestones "
        "WHERE invoiced_at IS NOT NULL AND invoiced_at BETWEEN ? AND ?",
        (start_iso, end_iso),
    ).fetchone()
    conn.close()
    return r["amount"] or 0.0


# --------------------------------------------------------------- coûts

def list_costs(project_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM costs WHERE project_id = ? ORDER BY COALESCE(cost_date, created_at) DESC",
        (project_id,),
    ).fetchall()
    conn.close()
    return rows


def create_cost(project_id, label, amount, cost_date):
    conn = get_db()
    conn.execute(
        "INSERT INTO costs (project_id, label, amount, cost_date, created_at) VALUES (?,?,?,?,?)",
        (project_id, label, amount, cost_date or None, now_iso()),
    )
    conn.commit()
    conn.close()


def delete_cost(cost_id):
    conn = get_db()
    conn.execute("DELETE FROM costs WHERE id = ?", (cost_id,))
    conn.commit()
    conn.close()


def get_cost_project(cost_id):
    conn = get_db()
    row = conn.execute("SELECT project_id FROM costs WHERE id = ?", (cost_id,)).fetchone()
    conn.close()
    return row["project_id"] if row else None


def costs_by_project():
    conn = get_db()
    rows = conn.execute("SELECT project_id, SUM(amount) AS total FROM costs GROUP BY project_id").fetchall()
    conn.close()
    return {r["project_id"]: r["total"] or 0.0 for r in rows}


# --------------------------------------------------------------- export

def export_entries():
    conn = get_db()
    rows = conn.execute(
        "SELECT e.entry_date AS date, p.name AS projet, COALESCE(p.client, '') AS client, "
        "COALESCE(t.name, '') AS tache, e.percent_of_day AS pourcentage_jour, "
        "ROUND(e.percent_of_day / 100.0, 4) AS jours, e.hours AS heures, "
        "COALESCE(e.note, '') AS note "
        "FROM entries e JOIN projects p ON p.id = e.project_id "
        "LEFT JOIN tasks t ON t.id = e.task_id ORDER BY e.entry_date DESC, p.name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def export_projects():
    conn = get_db()
    rows = conn.execute(
        "SELECT p.name AS projet, COALESCE(p.client, '') AS client, p.status AS statut, "
        "p.start_date AS debut, p.days_per_week AS jours_par_semaine, "
        "p.duration_value AS duree, p.duration_unit AS unite_duree, "
        "p.day_rate AS tjm, p.price_total AS prix_total, "
        "COALESCE((SELECT SUM(percent_of_day) FROM entries WHERE project_id = p.id), 0) / 100.0 AS jours_consommes, "
        "COALESCE((SELECT SUM(amount) FROM costs WHERE project_id = p.id), 0) AS couts, "
        "COALESCE((SELECT SUM(amount) FROM milestones WHERE project_id = p.id AND status IN ('invoiced','paid')), 0) AS facture, "
        "COALESCE((SELECT SUM(amount) FROM milestones WHERE project_id = p.id AND status = 'paid'), 0) AS encaisse "
        "FROM projects p WHERE p.archived = 0 ORDER BY p.start_date DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def export_milestones():
    conn = get_db()
    rows = conn.execute(
        "SELECT p.name AS projet, COALESCE(p.client, '') AS client, m.label AS jalon, "
        "m.amount AS montant, COALESCE(m.due_date, '') AS echeance, m.status AS statut, "
        "COALESCE(m.invoice_ref, '') AS reference, COALESCE(m.invoiced_at, '') AS date_facturation, "
        "COALESCE(m.paid_at, '') AS date_encaissement "
        "FROM milestones m JOIN projects p ON p.id = m.project_id "
        "ORDER BY COALESCE(m.due_date, '9999')"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
