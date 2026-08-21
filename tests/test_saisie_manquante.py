"""
La détection des jours non saisis ne doit jamais signaler un jour où tu ne
travaillais pas : une alerte qui se déclenche pendant les vacances est une
alerte qu'on apprend à ignorer.
"""
from datetime import date, timedelta

import calculations as calc
from conftest import SETTINGS


def test_jour_ouvre_sans_saisie_est_signale(base):
    today = date(2026, 8, 21)  # un vendredi
    missing = calc.missing_days({}, SETTINGS, [], today, lookback=7)

    assert date(2026, 8, 20) in missing  # jeudi
    assert date(2026, 8, 19) in missing  # mercredi


def test_week_end_jamais_signale(base):
    today = date(2026, 8, 21)
    missing = calc.missing_days({}, SETTINGS, [], today, lookback=7)

    assert all(d.weekday() < 5 for d in missing)


def test_conges_jamais_signales(base):
    today = date(2026, 8, 21)
    absences = [{"label": "Congés", "kind": "conges",
                 "start_date": "2026-08-17", "end_date": "2026-08-20"}]
    # lookback=4 : du lundi 17 au jeudi 20, entièrement couverts par les congés.
    missing = calc.missing_days({}, SETTINGS, absences, today, lookback=4)

    assert missing == []


def test_jour_saisi_non_signale(base):
    today = date(2026, 8, 21)
    saisi = {"2026-08-20": 100.0, "2026-08-19": 50.0}
    missing = calc.missing_days(saisi, SETTINGS, [], today, lookback=3)

    assert date(2026, 8, 20) not in missing
    assert date(2026, 8, 19) not in missing


def test_jour_meme_jamais_signale(base):
    """La journée n'est pas finie : ce n'est pas un oubli."""
    today = date(2026, 8, 21)
    missing = calc.missing_days({}, SETTINGS, [], today, lookback=7)

    assert today not in missing
