"""
Migration d'une base créée par la V1.

Ces tests couvrent l'angle mort de toute la suite : les autres tests partent
d'une base vierge, où CREATE TABLE crée directement les colonnes de la V2.
Sur une VRAIE base existante, ces colonnes n'arrivent que par migration —
et c'est là qu'un index créé trop tôt faisait planter le démarrage avec
"no such column: archived".
"""
import os
import sqlite3
from pathlib import Path

import pytest

import db

# Schéma exact de la V1, avant l'ajout de archived / updated_at et des
# tables absences, milestones, costs, scope_changes.
SCHEMA_V1 = """
CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, client TEXT,
    status TEXT NOT NULL DEFAULT 'provisional', days_per_week REAL NOT NULL,
    duration_value REAL NOT NULL, duration_unit TEXT NOT NULL DEFAULT 'weeks',
    day_rate REAL, price_total REAL NOT NULL, hours_per_day REAL,
    start_date TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#3E8E82',
    notes TEXT, created_at TEXT NOT NULL);
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL, entry_date TEXT NOT NULL,
    percent_of_day REAL NOT NULL, hours REAL NOT NULL, note TEXT, created_at TEXT NOT NULL);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX idx_entries_project ON entries(project_id);
"""


@pytest.fixture
def base_v1():
    """Base au format V1, avec des données dedans."""
    path = Path(os.environ["TIMING_DB"])
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)

    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_V1)
    conn.execute(
        "INSERT INTO projects (name, client, status, days_per_week, duration_value, "
        "duration_unit, day_rate, price_total, hours_per_day, start_date, color, notes, created_at) "
        "VALUES ('Projet historique','Client V1','confirmed',3,10,'weeks',600,18000,NULL,"
        "'2026-07-01','#3E8E82','notes v1','2026-07-01T09:00:00')"
    )
    conn.execute("INSERT INTO tasks (project_id, name, archived, created_at) "
                 "VALUES (1,'Maquettes',0,'2026-07-01T09:00:00')")
    for day, pct in [("2026-07-02", 60), ("2026-07-03", 80), ("2026-07-06", 100)]:
        conn.execute(
            "INSERT INTO entries (project_id, task_id, entry_date, percent_of_day, hours, note, created_at) "
            "VALUES (1,1,?,?,?,'travail','2026-07-02T09:00:00')", (day, pct, pct / 100 * 7))
    conn.execute("INSERT INTO settings VALUES ('default_hours_per_day','7')")
    conn.commit()
    conn.close()

    yield db
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def test_migration_ne_plante_pas(base_v1):
    """Le démarrage sur une base V1 doit passer. C'est le test qui aurait
    attrapé le bug : les index étaient créés avant les migrations, donc
    idx_projects_status référençait une colonne archived inexistante."""
    base_v1.init_db()


def test_donnees_v1_conservees(base_v1):
    base_v1.init_db()

    project = base_v1.get_project(1)
    assert project["name"] == "Projet historique"
    assert project["notes"] == "notes v1"
    assert base_v1.count_entries(1) == 3
    assert base_v1.entries_aggregate_for(1)["percent_sum"] == 240.0
    assert [t["name"] for t in base_v1.list_tasks(1)] == ["Maquettes"]


def test_colonnes_v2_ajoutees(base_v1):
    base_v1.init_db()

    project = base_v1.get_project(1)
    assert project["archived"] == 0          # défaut appliqué aux lignes existantes
    assert "updated_at" in project.keys()


def test_nouvelles_tables_disponibles(base_v1):
    """Les tables absent de la V1 doivent être créées, et utilisables."""
    base_v1.init_db()

    base_v1.create_milestone(1, "Acompte", 5000, "2026-09-01")
    base_v1.create_cost(1, "Licence", 300, "2026-08-01")
    base_v1.create_absence("Congés", "conges", "2026-08-10", "2026-08-20")

    assert len(base_v1.list_milestones(1)) == 1
    assert len(base_v1.list_costs(1)) == 1
    assert len(base_v1.list_absences()) == 1


def test_migration_idempotente(base_v1):
    """L'app relance init_db() à chaque démarrage : trois fois de suite ne
    doit ni échouer ni dupliquer quoi que ce soit."""
    base_v1.init_db()
    base_v1.init_db()
    base_v1.init_db()

    assert base_v1.count_entries(1) == 3
    assert base_v1.get_project(1) is not None


def test_index_bien_crees(base_v1):
    """Vérifie que les index sont réellement présents après migration, et
    pas silencieusement sautés."""
    base_v1.init_db()

    conn = base_v1.get_db()
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()

    assert "idx_projects_status" in names
    assert "idx_entries_project_date" in names
