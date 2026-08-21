"""
Réglages communs aux tests.

Chargé par pytest avant tout fichier de test : TIMING_DB est posé ici, donc
avant le premier `import db`, dont les fonctions figent DB_PATH à l'import.
Conséquence : aucun test ne touche instance/timing.sqlite3.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
_TMP = Path(tempfile.mkdtemp(prefix="timing-tests-"))
os.environ["TIMING_DB"] = str(_TMP / "test.sqlite3")

import db  # noqa: E402


@pytest.fixture
def base():
    """Base vierge par test : un test dont le résultat dépend de ses voisins
    ne prouve rien."""
    path = Path(os.environ["TIMING_DB"])
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    db.init_db()
    yield db
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)


SETTINGS = {
    "default_hours_per_day": 7.0,
    "peak_threshold_warning": 75.0,
    "peak_threshold_danger": 100.0,
    "working_days": "0,1,2,3,4",
    "min_consumption_pct": 20.0,
    "budget_alert_pct": 80.0,
    "monthly_revenue_goal": 0.0,
    "annual_revenue_goal": 0.0,
}


def make_project(**over):
    """Projet de référence : 5 j/semaine pendant 8 semaines = 40 jours
    vendus, à 20 000 € (donc 500 €/jour vendu)."""
    project = {
        "id": 1, "name": "Projet test", "client": "Client A", "status": "confirmed",
        "days_per_week": 5, "duration_value": 8, "duration_unit": "weeks",
        "day_rate": 500, "price_total": 20000, "hours_per_day": None,
        "start_date": (date.today() - timedelta(days=28)).isoformat(),
        "color": "#3E8E82", "notes": "", "archived": 0,
    }
    project.update(over)
    return project


def agg(percent_sum, count=1, first_date=None):
    return {
        "percent_sum": percent_sum,
        "entries_count": count,
        "first_date": first_date or (date.today() - timedelta(days=28)).isoformat(),
        "last_date": date.today().isoformat(),
    }


def project_data(**over):
    data = {
        "name": "Projet", "client": "Client A", "status": "confirmed",
        "days_per_week": 5, "duration_value": 8, "duration_unit": "weeks",
        "day_rate": 500, "price_total": 20000, "hours_per_day": None,
        "start_date": date.today().isoformat(), "notes": "", "color": "#3E8E82",
    }
    data.update(over)
    return data
