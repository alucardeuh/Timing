import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "instance" / "timing.sqlite3"
SCHEMA_PATH = BASE_DIR / "schema.sql"

DEFAULT_SETTINGS = {
    "default_hours_per_day": "7",
    "currency_symbol": "€",
    "peak_threshold_warning": "75",
    "peak_threshold_danger": "100",
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


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
    conn.commit()
    conn.close()


def get_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    settings = dict(DEFAULT_SETTINGS)
    settings.update({row["key"]: row["value"] for row in rows})
    settings["default_hours_per_day"] = float(settings["default_hours_per_day"])
    settings["peak_threshold_warning"] = float(settings["peak_threshold_warning"])
    settings["peak_threshold_danger"] = float(settings["peak_threshold_danger"])
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
    count = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
    conn.close()
    return PROJECT_COLORS[count % len(PROJECT_COLORS)]
