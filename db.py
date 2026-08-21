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
    # Temps écoulé minimum avant d'afficher un indice projeté. En dessous,
    # diviser par un temps écoulé quasi nul rend l'indice absurdement volatil.
    "min_projection_elapsed_pct": "10",
    # Alerte quand un projet dépasse ce % de son budget vendu.
    "budget_alert_pct": "80",
    # Prolongation maximale d'un projet non terminé au-delà de sa fin prévue,
    # en semaines. Au-delà, il cesse de polluer le planning.
    "overrun_weeks": "4",
    "monthly_revenue_goal": "0",
    "annual_revenue_goal": "0",
    # Coût de revient : charges fixes annuelles (URSSAF, assurances, loyer,
    # matériel, logiciels...) et nombre de jours facturables visés dans
    # l'année. Le rapport des deux donne ce que coûte une journée de ton
    # temps — sans quoi aucune "marge" affichée n'est une vraie marge.
    "annual_fixed_costs": "0",
    "billable_days_per_year": "180",
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

def _is_numeric(value):
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


# Dérivé de DEFAULT_SETTINGS plutôt que maintenu à la main : une liste
# écrite en dur se désynchronisait au moindre réglage ajouté, et le réglage
# oublié restait une chaîne — d'où un « '<=' not supported between str and
# int » à la première comparaison, sur toutes les pages d'un coup.
FLOAT_SETTINGS = {key for key, value in DEFAULT_SETTINGS.items() if _is_numeric(value)}

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
    ("costs", "billable", "INTEGER NOT NULL DEFAULT 0"),
    ("projects", "weekdays", "TEXT"),
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
        # Échappement des jokers : sans lui, un « % » ou un « _ » tapé par
        # l'utilisateur agit comme caractère générique et la recherche
        # renvoie n'importe quoi.
        needle = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append("(name LIKE ? ESCAPE '\\' OR client LIKE ? ESCAPE '\\' "
                       "OR notes LIKE ? ESCAPE '\\')")
        params.extend([needle] * 3)
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
        "duration_unit, day_rate, price_total, hours_per_day, start_date, weekdays, "
        "color, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            data["name"], data["client"], data["status"], data["days_per_week"],
            data["duration_value"], data["duration_unit"], data["day_rate"],
            data["price_total"], data["hours_per_day"], data["start_date"],
            data.get("weekdays") or None,
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
        "duration_unit=?, day_rate=?, price_total=?, hours_per_day=?, start_date=?, "
        "weekdays=?, notes=?, updated_at=? WHERE id=?",
        (
            data["name"], data["client"], data["status"], data["days_per_week"],
            data["duration_value"], data["duration_unit"], data["day_rate"],
            data["price_total"], data["hours_per_day"], data["start_date"],
            data.get("weekdays") or None, data["notes"], now_iso(), project_id,
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

def _entry_filters(project_id, date_from, date_to, task_id):
    """Clauses communes à la liste et au comptage des saisies : les deux
    doivent filtrer exactement pareil, sinon la pagination ment."""
    clauses, params = ["project_id = ?"], [project_id]
    if date_from:
        clauses.append("entry_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("entry_date <= ?")
        params.append(date_to)
    if task_id:
        if str(task_id) == "none":
            clauses.append("task_id IS NULL")
        else:
            clauses.append("task_id = ?")
            params.append(int(task_id))
    return " AND ".join(clauses), params


def list_entries(project_id, limit=None, offset=0, date_from=None, date_to=None, task_id=None):
    where, params = _entry_filters(project_id, date_from, date_to, task_id)
    sql = f"SELECT * FROM entries WHERE {where} ORDER BY entry_date DESC, id DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def count_entries(project_id, date_from=None, date_to=None, task_id=None):
    where, params = _entry_filters(project_id, date_from, date_to, task_id)
    conn = get_db()
    n = conn.execute(f"SELECT COUNT(*) AS n FROM entries WHERE {where}", params).fetchone()["n"]
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


def set_day_totals(cells):
    """Écrit toutes les cases de la grille hebdo en UNE transaction.

    `cells` est une liste de tuples (project_id, task_id, entry_date,
    percent, hours). Soit tout est enregistré, soit rien ne l'est.

    Remplace une boucle qui appelait set_day_total() par cellule : sur une
    semaine de 8 lignes, cela ouvrait 113 connexions et 56 commits. Surtout,
    une erreur sur la 30ᵉ case laissait les 29 premières committées alors que
    l'utilisateur lisait « saisie invalide » et croyait que rien n'avait été
    enregistré.
    """
    conn = get_db()
    try:
        with conn:  # commit si tout passe, rollback à la moindre exception
            for project_id, task_id, entry_date, percent, hours in cells:
                _set_day_total(conn, project_id, task_id, entry_date, percent, hours)
    finally:
        conn.close()


def _set_day_total(conn, project_id, task_id, entry_date, percent, hours):
    """Cœur de l'écriture d'une case, sur une connexion déjà ouverte.

    S'il existe déjà plusieurs entrées pour cette clé (saisies une par une
    dans la journée), la première est mise à jour et les autres supprimées :
    la grille affiche un total, elle doit donc pouvoir en écrire un. Les
    saisies détaillées avec notes se font depuis la fiche projet.
    """
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


def set_day_total(project_id, task_id, entry_date, percent, hours):
    """Écrit une seule case. Conservé pour les appels unitaires et les tests."""
    set_day_totals([(project_id, task_id, entry_date, percent, hours)])


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


def update_milestone(milestone_id, label, amount, due_date):
    """Modifie un jalon existant.

    Rien ne permettait de corriger un libellé, un montant ou une échéance :
    seules la création, le changement de statut et la suppression
    existaient. C'est pourtant la donnée qui pilote les objectifs de CA, le
    carnet de commandes et toute la page Facturation.
    """
    conn = get_db()
    conn.execute("UPDATE milestones SET label=?, amount=?, due_date=? WHERE id=?",
                 (label, amount, due_date or None, milestone_id))
    conn.commit()
    conn.close()


def set_milestone_status(milestone_id, status, invoice_ref=None, dated=None):
    """Change le statut d'un jalon.

    `dated` permet d'antidater : marquer le 3 août un jalon facturé le
    28 juillet rangeait le CA dans le mauvais mois, sans aucun moyen de
    corriger.
    """
    if status not in MILESTONE_STATUSES:
        raise ValueError(f"Statut de jalon inconnu : {status!r}")
    conn = get_db()
    fields = {"status": status}
    today = dated or now_iso()[:10]
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
    """Totaux par projet, volontairement NON filtrés sur archived.

    Seule exception au principe « la corbeille sort des agrégats » : cette
    fonction alimente la fiche d'un projet précis, y compris quand on
    consulte un projet en corbeille pour décider de le restaurer. Les
    agrégats transverses (CA du mois, de l'année) passent, eux, par
    invoiced_between() qui filtre.
    """
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


# PRINCIPE : la corbeille sort de TOUS les agrégats.
#
# list_milestones() filtrait déjà sur p.archived = 0, mais pas les deux
# fonctions de CA ci-dessous : un projet mis à la corbeille disparaissait de
# la page Facturation tout en continuant à peser dans « Facturé ce mois ».
# Deux pages affichaient des totaux qui ne se recoupaient pas.

def monthly_invoiced(months=12):
    """CA facturé par mois, daté à la facturation (pas à l'encaissement)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT substr(m.invoiced_at, 1, 7) AS month, SUM(m.amount) AS amount "
        "FROM milestones m JOIN projects p ON p.id = m.project_id "
        "WHERE p.archived = 0 AND m.invoiced_at IS NOT NULL AND m.invoiced_at != '' "
        "GROUP BY month ORDER BY month DESC LIMIT ?", (months,)
    ).fetchall()
    conn.close()
    return [{"month": r["month"], "amount": round(r["amount"] or 0, 2)} for r in reversed(rows)]


def invoiced_between(start_iso, end_iso):
    conn = get_db()
    r = conn.execute(
        "SELECT SUM(m.amount) AS amount FROM milestones m "
        "JOIN projects p ON p.id = m.project_id "
        "WHERE p.archived = 0 AND m.invoiced_at IS NOT NULL "
        "AND m.invoiced_at BETWEEN ? AND ?",
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


def create_cost(project_id, label, amount, cost_date, billable=False):
    conn = get_db()
    conn.execute(
        "INSERT INTO costs (project_id, label, amount, cost_date, billable, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (project_id, label, amount, cost_date or None, 1 if billable else 0, now_iso()),
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
    """{project_id: {'absorbed', 'rebilled'}}

    Les coûts refacturés sont séparés : ils s'ajoutent au revenu au lieu de
    peser sur la marge.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT project_id, "
        "SUM(CASE WHEN billable = 0 THEN amount ELSE 0 END) AS absorbed, "
        "SUM(CASE WHEN billable = 1 THEN amount ELSE 0 END) AS rebilled "
        "FROM costs GROUP BY project_id"
    ).fetchall()
    conn.close()
    return {r["project_id"]: {"absorbed": r["absorbed"] or 0.0,
                             "rebilled": r["rebilled"] or 0.0} for r in rows}


# --------------------------------------------------------------- export

def export_entries():
    conn = get_db()
    rows = conn.execute(
        "SELECT e.entry_date AS date, p.name AS projet, COALESCE(p.client, '') AS client, "
        "COALESCE(t.name, '') AS tache, e.percent_of_day AS pourcentage_jour, "
        "ROUND(e.percent_of_day / 100.0, 4) AS jours, "
        # Heures recalculées avec le réglage COURANT, jamais la colonne
        # e.hours figée à la saisie : sinon l'export mélange deux
        # conversions et contredit sa propre colonne « jours ».
        "ROUND(e.percent_of_day / 100.0 * COALESCE(p.hours_per_day, "
        "  (SELECT CAST(value AS REAL) FROM settings WHERE key = 'default_hours_per_day')"
        "), 2) AS heures, "
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


def backup_to(destination):
    """Copie cohérente de la base vers `destination`.

    send_file() sur le fichier principal seul était incomplet : en mode WAL,
    les transactions récentes vivent dans le fichier -wal et n'auraient pas
    été copiées. L'API backup() de sqlite3 produit un fichier cohérent qui
    intègre le WAL — c'est la seule sauvegarde censée tout restaurer, elle
    doit vraiment tout contenir.
    """
    source = get_db()
    target = sqlite3.connect(destination)
    try:
        with target:
            source.backup(target)
    finally:
        target.close()
        source.close()
    return destination


def duplicate_project(project_id, start_date=None):
    """Reconduit un projet : copie le contrat, pas l'historique.

    Le renouvellement est le cas le plus fréquent en régie. Sont copiés le
    nom (suffixé), le client, le volume, le prix et les tâches. Ni saisies,
    ni jalons, ni coûts : ce sont des faits datés qui appartiennent au
    projet d'origine.
    """
    source = get_project(project_id)
    if not source:
        return None
    data = {
        "name": f"{source['name']} — reconduction",
        "client": source["client"],
        "status": "provisional",
        "days_per_week": source["days_per_week"],
        "duration_value": source["duration_value"],
        "duration_unit": source["duration_unit"],
        "day_rate": source["day_rate"],
        "price_total": source["price_total"],
        "hours_per_day": source["hours_per_day"],
        "start_date": start_date or source["start_date"],
        "notes": source["notes"],
        "color": next_project_color(),
    }
    new_id = create_project(data)
    for task in list_tasks(project_id):
        create_task(new_id, task["name"])
    return new_id


def days_spent_between(start_iso, end_iso):
    """Jours consommés sur une période, projets en corbeille exclus."""
    conn = get_db()
    r = conn.execute(
        "SELECT SUM(e.percent_of_day) / 100.0 AS days FROM entries e "
        "JOIN projects p ON p.id = e.project_id "
        "WHERE p.archived = 0 AND e.entry_date BETWEEN ? AND ?",
        (start_iso, end_iso),
    ).fetchone()
    conn.close()
    return round(r["days"] or 0.0, 2)


def payment_delays():
    """Délai de paiement observé par client, en jours.

    Calculé sur l'historique réel (paid_at − invoiced_at) plutôt que sur une
    règle contractuelle : c'est le comportement constaté qui permet de
    prévoir, pas ce qui était prévu au contrat.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT COALESCE(p.client, '') AS client, "
        "julianday(m.paid_at) - julianday(m.invoiced_at) AS delay "
        "FROM milestones m JOIN projects p ON p.id = m.project_id "
        "WHERE p.archived = 0 AND m.status = 'paid' "
        "AND m.paid_at IS NOT NULL AND m.invoiced_at IS NOT NULL"
    ).fetchall()
    conn.close()
    by_client = {}
    for r in rows:
        if r["delay"] is None or r["delay"] < 0:
            continue
        by_client.setdefault(r["client"] or "Sans client", []).append(r["delay"])
    return {name: {"days": round(sum(v) / len(v), 1), "samples": len(v)}
            for name, v in by_client.items()}


def open_milestones():
    """Jalons non encaissés, avec le client — base de la projection."""
    conn = get_db()
    rows = conn.execute(
        "SELECT m.*, p.name AS project_name, COALESCE(p.client, '') AS project_client "
        "FROM milestones m JOIN projects p ON p.id = m.project_id "
        "WHERE p.archived = 0 AND m.status != 'paid' "
        "ORDER BY COALESCE(m.due_date, '9999')"
    ).fetchall()
    conn.close()
    return rows


def export_to_invoice():
    """Jalons à facturer, prêts à être repris dans un outil de facturation.

    Volontairement plat et complet : c'est le format qui survivra au passage
    à la facturation électronique obligatoire, contrairement à un PDF généré
    ici.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT COALESCE(p.client, '') AS client, p.name AS projet, m.label AS jalon, "
        "m.amount AS montant_ht, COALESCE(m.due_date, '') AS echeance, "
        "COALESCE(m.invoice_ref, '') AS reference, "
        "CASE m.status WHEN 'todo' THEN 'a facturer' ELSE 'facture' END AS statut "
        "FROM milestones m JOIN projects p ON p.id = m.project_id "
        "WHERE p.archived = 0 AND m.status != 'paid' "
        "ORDER BY COALESCE(m.due_date, '9999'), client"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
