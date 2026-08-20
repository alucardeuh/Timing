-- Timing V2 — schéma de base de données

CREATE TABLE IF NOT EXISTS projects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    client          TEXT,
    status          TEXT NOT NULL DEFAULT 'provisional', -- provisional | confirmed | paused | completed
    days_per_week   REAL NOT NULL,
    duration_value  REAL NOT NULL,
    duration_unit   TEXT NOT NULL DEFAULT 'weeks',        -- weeks | months
    day_rate        REAL,
    price_total     REAL NOT NULL,
    hours_per_day   REAL,
    start_date      TEXT NOT NULL,
    color           TEXT NOT NULL DEFAULT '#3E8E82',
    notes           TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    archived        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    task_id         INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    entry_date      TEXT NOT NULL,
    percent_of_day  REAL NOT NULL,
    hours           REAL NOT NULL,
    note            TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entries_project ON entries(project_id);
CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(entry_date);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
