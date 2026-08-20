"""
Logique métier de Timing. Aucune dépendance à Flask ni à sqlite3 ici :
tout reçoit des dicts / sqlite3.Row et renvoie des dicts, pour rester
facilement testable isolément.
"""

from datetime import date, datetime, timedelta

WEEKS_PER_MONTH = 4.345  # moyenne (52 semaines / 12 mois)

ACTIVE_LOAD_STATUSES = {"confirmed"}  # toujours comptés dans la charge
OPTIONAL_LOAD_STATUSES = {"provisional"}  # comptés seulement si demandé


def parse_date(value):
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def total_weeks(project):
    if project["duration_unit"] == "months":
        return project["duration_value"] * WEEKS_PER_MONTH
    return project["duration_value"]


def total_days_sold(project):
    return round(project["days_per_week"] * total_weeks(project), 2)


def hours_per_day(project, settings):
    return project["hours_per_day"] or settings["default_hours_per_day"]


def hours_sold(project, settings):
    return total_days_sold(project) * hours_per_day(project, settings)


def planned_end_date(project):
    start = parse_date(project["start_date"])
    return start + timedelta(days=round(total_weeks(project) * 7))


def hours_spent(entries):
    return round(sum(e["hours"] for e in entries), 2)


def days_spent(project, entries, settings):
    hpd = hours_per_day(project, settings)
    if hpd <= 0:
        return 0
    return round(hours_spent(entries) / hpd, 2)


def pct_consumed(project, entries, settings):
    total = total_days_sold(project)
    if total <= 0:
        return 0
    return round(days_spent(project, entries, settings) / total * 100, 1)


def pct_time_elapsed(project, today=None):
    today = today or date.today()
    start = parse_date(project["start_date"])
    end = planned_end_date(project)
    total_span = (end - start).days
    if total_span <= 0:
        return 100.0
    elapsed = (today - start).days
    return round(max(0, min(elapsed, total_span)) / total_span * 100, 1)


def real_hourly_rate(project, entries):
    hs = hours_spent(entries)
    if hs <= 0:
        return None
    return round(project["price_total"] / hs, 2)


def theoretical_hourly_rate(project, settings):
    hsold = hours_sold(project, settings)
    if hsold <= 0:
        return None
    return round(project["price_total"] / hsold, 2)


def rentability_index(project, entries, settings):
    real = real_hourly_rate(project, entries)
    theo = theoretical_hourly_rate(project, settings)
    if real is None or not theo:
        return None
    return round(real / theo, 3)


def days_remaining(project, entries, settings):
    return round(total_days_sold(project) - days_spent(project, entries, settings), 2)


def pace_status(project, entries, settings):
    consumed = pct_consumed(project, entries, settings)
    elapsed = pct_time_elapsed(project)
    delta = consumed - elapsed
    if delta <= 5:
        return "ahead"
    if delta <= 20:
        return "on_track"
    return "behind"


def projected_end_date(project, entries, settings, today=None):
    today = today or date.today()
    start = parse_date(project["start_date"])
    elapsed_days = max((today - start).days, 1)
    hs = hours_spent(entries)
    if hs <= 0:
        return None
    rate_per_day = hs / elapsed_days
    remaining_hours = hours_sold(project, settings) - hs
    if remaining_hours <= 0:
        return today
    days_needed = remaining_hours / rate_per_day
    return today + timedelta(days=round(days_needed))


def project_stats(project, entries, settings):
    return {
        "total_days_sold": total_days_sold(project),
        "hours_sold": round(hours_sold(project, settings), 1),
        "hours_spent": hours_spent(entries),
        "days_spent": days_spent(project, entries, settings),
        "pct_consumed": pct_consumed(project, entries, settings),
        "pct_time_elapsed": pct_time_elapsed(project),
        "days_remaining": days_remaining(project, entries, settings),
        "real_hourly_rate": real_hourly_rate(project, entries),
        "theoretical_hourly_rate": theoretical_hourly_rate(project, settings),
        "rentability_index": rentability_index(project, entries, settings),
        "pace_status": pace_status(project, entries, settings),
        "planned_end_date": planned_end_date(project),
        "projected_end_date": projected_end_date(project, entries, settings),
        "entries_count": len(entries),
    }


def tally_segments(project, entries, settings, max_segments=40):
    total = total_days_sold(project)
    spent = days_spent(project, entries, settings)
    if total <= 0:
        return {"unit": "day", "segments": []}

    unit = "day"
    count = total
    if total > max_segments:
        unit = "week"
        count = total_weeks(project)
        spent = (days_spent(project, entries, settings) / total) * count

    n_segments = max(1, round(count))
    fill_per_segment = count / n_segments if n_segments else 0
    segments = []
    remaining_spent = spent
    for _ in range(n_segments):
        if remaining_spent >= fill_per_segment:
            fill = 1.0
            remaining_spent -= fill_per_segment
        elif remaining_spent > 0:
            fill = round(remaining_spent / fill_per_segment, 3)
            remaining_spent = 0
        else:
            fill = 0.0
        segments.append(fill)

    return {"unit": unit, "segments": segments}


def task_breakdown(entries, tasks_by_id):
    """
    Regroupe les heures d'un projet par tâche (et une entrée 'Sans tâche' pour
    les entrées non rattachées). Renvoie une liste triée par heures décroissantes,
    avec le % du temps total du projet que représente chaque tâche.
    """
    totals = {}
    for e in entries:
        key = e["task_id"] if e["task_id"] else None
        totals[key] = totals.get(key, 0) + e["hours"]

    grand_total = sum(totals.values())
    rows = []
    for task_id, hours in totals.items():
        if task_id and task_id in tasks_by_id:
            name = tasks_by_id[task_id]["name"]
        else:
            name = "Sans tâche"
        pct = round(hours / grand_total * 100, 1) if grand_total else 0
        rows.append({"task_id": task_id, "name": name, "hours": round(hours, 2), "pct": pct})

    rows.sort(key=lambda r: r["hours"], reverse=True)
    return rows


def project_is_active_on(project, day):
    start = parse_date(project["start_date"])
    end = planned_end_date(project)
    return start <= day <= end


def daily_capacity(projects, window_start, window_days, include_provisional, settings):
    """
    Pour chaque jour de la fenêtre, calcule le % de charge cumulé de tous les
    projets actifs ce jour-là (jours_vendus/semaine réparti uniformément sur 7
    jours), en séparant la part confirmée de la part provisoire.
    Renvoie une liste de dicts, un par jour.
    """
    days = []
    for i in range(window_days):
        day = window_start + timedelta(days=i)
        pct_confirmed = 0.0
        pct_provisional = 0.0
        contributors = []
        for p in projects:
            if p["status"] not in ACTIVE_LOAD_STATUSES | OPTIONAL_LOAD_STATUSES:
                continue
            if not project_is_active_on(p, day):
                continue
            daily_pct = p["days_per_week"] / 7 * 100
            if p["status"] == "confirmed":
                pct_confirmed += daily_pct
                contributors.append({"name": p["name"], "pct": round(daily_pct, 1), "provisional": False})
            elif p["status"] == "provisional" and include_provisional:
                pct_provisional += daily_pct
                contributors.append({"name": p["name"], "pct": round(daily_pct, 1), "provisional": True})

        total_pct = pct_confirmed + pct_provisional
        if total_pct <= 0:
            level = "free"
        elif total_pct <= settings["peak_threshold_warning"]:
            level = "light"
        elif total_pct <= settings["peak_threshold_danger"]:
            level = "heavy"
        else:
            level = "storm"

        days.append({
            "date": day,
            "pct_confirmed": round(pct_confirmed, 1),
            "pct_provisional": round(pct_provisional, 1),
            "pct_total": round(total_pct, 1),
            "level": level,
            "has_provisional": pct_provisional > 0,
            "contributors": contributors,
        })
    return days


def burndown_points(project, entries, settings, width=560, height=140, pad=26):
    """Points SVG pour le graphique de cadence d'un projet (jours cumulés)."""
    start = parse_date(project["start_date"])
    end = planned_end_date(project)
    total = total_days_sold(project)
    today = date.today()

    x_end_date = max(end, today) if entries else end
    total_span = max((x_end_date - start).days, 1)

    def x_for(d):
        return pad + (d - start).days / total_span * (width - 2 * pad)

    def y_for(days_val):
        if total <= 0:
            return height - pad
        ratio = min(days_val / total, 1.3)
        return (height - pad) - ratio * (height - 2 * pad)

    ref_points = f"{x_for(start):.1f},{y_for(0):.1f} {x_for(end):.1f},{y_for(total):.1f}"

    sorted_entries = sorted(entries, key=lambda e: e["entry_date"])
    hpd = hours_per_day(project, settings)
    cumulative_hours = 0
    real_pts = [f"{x_for(start):.1f},{y_for(0):.1f}"]
    for e in sorted_entries:
        cumulative_hours += e["hours"]
        d = parse_date(e["entry_date"])
        real_pts.append(f"{x_for(d):.1f},{y_for(cumulative_hours / hpd):.1f}")
    if len(real_pts) == 1:
        real_pts.append(f"{x_for(today):.1f},{y_for(0):.1f}")

    return {
        "width": width, "height": height, "pad": pad,
        "reference": ref_points,
        "actual": " ".join(real_pts),
        "today_x": f"{x_for(today):.1f}",
    }


def gantt_rows(projects, window_start, window_days):
    """
    Position de chaque projet sur la fenêtre du Gantt, en index de colonne
    (0 = premier jour de la fenêtre). Les projets hors fenêtre sont recadrés.
    """
    window_end = window_start + timedelta(days=window_days - 1)
    rows = []
    for p in projects:
        start = parse_date(p["start_date"])
        end = planned_end_date(p)
        if end < window_start or start > window_end:
            continue
        clipped_start = max(start, window_start)
        clipped_end = min(end, window_end)
        start_idx = (clipped_start - window_start).days
        end_idx = (clipped_end - window_start).days
        rows.append({
            "project": p,
            "start_idx": start_idx,
            "end_idx": end_idx,
            "span": end_idx - start_idx + 1,
            "clipped_start": clipped_start < start,
            "clipped_end": clipped_end > end,
        })
    return rows
