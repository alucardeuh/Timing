"""
Logique métier de Timing. Aucune dépendance à Flask ni à sqlite3 ici : tout
reçoit des dicts / sqlite3.Row et renvoie des dicts, ce qui rend chaque
fonction testable isolément (voir tests/).

Trois règles de fond, corrigées par rapport à la V1 :

1. LA CONSOMMATION SE COMPTE EN % DE JOURNÉE, JAMAIS EN HEURES.
   `percent_of_day` est la source de vérité : 50 = une demi-journée, quel
   que soit le réglage d'heures/jour. Les heures sont dérivées à
   l'affichage. Sans ça, changer "7 h/jour" en "8 h/jour" déformait
   rétroactivement tout l'historique de consommation.

2. LA CHARGE SE DIVISE PAR LES JOURS OUVRÉS, PAS PAR 7.
   Un projet à 5 jours/semaine sur une semaine de 5 jours travaillés
   remplit 100 % de chaque jour ouvré, et 0 % du week-end. Diviser par 7
   diluait la charge d'un facteur 1,4 et rendait les seuils inatteignables.

3. UN INDICE DE RENTABILITÉ N'A DE SENS QU'AVEC ASSEZ DE MATIÈRE.
   `prix / heures passées` sur 2 h saisies donne un taux horaire absurde.
   En dessous du seuil de consommation réglé, l'indice vaut None.
"""

from datetime import date, datetime, timedelta

WEEKS_PER_MONTH = 4.345  # moyenne (52 semaines / 12 mois)

ACTIVE_LOAD_STATUSES = {"confirmed"}      # toujours comptés dans la charge
OPTIONAL_LOAD_STATUSES = {"provisional"}  # comptés seulement si demandé

# Statuts qui consomment encore du budget et méritent d'être surveillés.
LIVE_STATUSES = {"confirmed", "paused"}


# ------------------------------------------------------------------ dates

def parse_date(value):
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def week_monday(d):
    return d - timedelta(days=d.weekday())


def working_days_set(settings):
    """Indices des jours travaillés (0 = lundi). Repli sur lundi-vendredi
    si le réglage est vide ou illisible : une capacité nulle ferait une
    division par zéro dans toute la carte de charge."""
    raw = str(settings.get("working_days", "0,1,2,3,4"))
    days = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6:
            days.add(int(part))
    return days or {0, 1, 2, 3, 4}


def build_absence_index(absences):
    """{date: label} pour toutes les dates couvertes par une absence.

    Développer les intervalles une fois pour toutes évite de reparcourir la
    liste des absences pour chacun des 91 jours du planning.
    """
    index = {}
    for a in absences:
        start, end = parse_date(a["start_date"]), parse_date(a["end_date"])
        if end < start:
            start, end = end, start
        day = start
        while day <= end:
            index[day] = a["label"]
            day += timedelta(days=1)
    return index


def is_working_day(day, settings, absence_index=None):
    if day.weekday() not in working_days_set(settings):
        return False
    if absence_index and day in absence_index:
        return False
    return True


# ------------------------------------------------------- volume et durée

def total_weeks(project):
    if project["duration_unit"] == "months":
        return project["duration_value"] * WEEKS_PER_MONTH
    return project["duration_value"]


def total_days_sold(project):
    return round(project["days_per_week"] * total_weeks(project), 2)


def hours_per_day(project, settings):
    """Ne sert QU'À convertir des jours en heures pour l'affichage."""
    try:
        value = project["hours_per_day"]
    except (KeyError, IndexError):
        value = None
    return value or settings["default_hours_per_day"]


def hours_sold(project, settings):
    return total_days_sold(project) * hours_per_day(project, settings)


def planned_end_date(project):
    start = parse_date(project["start_date"])
    return start + timedelta(days=round(total_weeks(project) * 7))


# ------------------------------------------------------------ agrégation

def aggregate_entries(entries):
    """Transforme une liste d'entrées en agrégat, au même format que celui
    renvoyé par db.entries_aggregate_for(). Sert aux tests et au CLI ; les
    routes reçoivent l'agrégat directement depuis SQL."""
    dates = [e["entry_date"] for e in entries]
    return {
        "percent_sum": sum(e["percent_of_day"] for e in entries),
        "entries_count": len(entries),
        "first_date": min(dates) if dates else None,
        "last_date": max(dates) if dates else None,
    }


EMPTY_AGG = {"percent_sum": 0.0, "entries_count": 0, "first_date": None, "last_date": None}


def days_spent(agg):
    """Jours consommés. Directement dérivé des % saisis : aucune dépendance
    à un réglage mutable, donc l'historique ne bouge jamais tout seul."""
    return round((agg.get("percent_sum") or 0) / 100.0, 2)


def hours_spent(agg, project, settings):
    return round(days_spent(agg) * hours_per_day(project, settings), 2)


# ------------------------------------------------------------- avancement

def pct_consumed(project, agg):
    total = total_days_sold(project)
    if total <= 0:
        return 0
    return round(days_spent(agg) / total * 100, 1)


def pct_time_elapsed(project, today=None):
    today = today or date.today()
    start = parse_date(project["start_date"])
    end = planned_end_date(project)
    span = (end - start).days
    if span <= 0:
        return 100.0
    elapsed = (today - start).days
    return round(max(0, min(elapsed, span)) / span * 100, 1)


def days_remaining(project, agg):
    return round(total_days_sold(project) - days_spent(agg), 2)


def pace_status(project, agg, today=None):
    """Cadence : compare le budget consommé au temps écoulé.

    Quatre états au lieu de trois. 'not_started' est nouveau : sans lui, un
    projet sans aucune saisie affichait "en avance", ce qui confondait
    "je suis économe" avec "je n'ai rien saisi".
    """
    if (agg.get("entries_count") or 0) == 0:
        return "not_started"
    delta = pct_consumed(project, agg) - pct_time_elapsed(project, today)
    if delta <= 5:
        return "ahead"
    if delta <= 20:
        return "on_track"
    return "behind"


def projected_end_date(project, agg, today=None):
    """Date de fin au rythme observé. None tant qu'il n'y a pas de rythme
    mesurable (moins de deux jours d'historique)."""
    today = today or date.today()
    first = agg.get("first_date")
    spent = days_spent(agg)
    if not first or spent <= 0:
        return None
    elapsed = max((today - parse_date(first)).days, 1)
    rate = spent / elapsed
    if rate <= 0:
        return None
    remaining = total_days_sold(project) - spent
    if remaining <= 0:
        return today
    return today + timedelta(days=round(remaining / rate))


# ------------------------------------------------------------ rentabilité

def theoretical_day_rate(project):
    total = total_days_sold(project)
    if total <= 0:
        return None
    return round(project["price_total"] / total, 2)


def real_day_rate(project, agg, costs=0.0):
    """Marge par jour réellement passé. Les coûts directs sont déduits :
    sans eux, l'indicateur mesure la productivité, pas la rentabilité."""
    spent = days_spent(agg)
    if spent <= 0:
        return None
    return round((project["price_total"] - (costs or 0)) / spent, 2)


def theoretical_hourly_rate(project, settings):
    sold = hours_sold(project, settings)
    if sold <= 0:
        return None
    return round(project["price_total"] / sold, 2)


def real_hourly_rate(project, agg, settings, costs=0.0):
    hours = hours_spent(agg, project, settings)
    if hours <= 0:
        return None
    return round((project["price_total"] - (costs or 0)) / hours, 2)


def index_is_reliable(project, agg, settings):
    """Un indice calculé sur presque rien est un mensonge présenté comme un
    chiffre. Il faut soit un projet terminé, soit assez de consommation."""
    if project["status"] == "completed":
        return True
    return pct_consumed(project, agg) >= settings.get("min_consumption_pct", 20)


def rentability_index(project, agg, settings, costs=0.0):
    """Marge réelle par jour ÷ prix vendu par jour.

    > 1 : chaque jour passé rapporte plus que prévu.
    < 1 : le projet coûte plus de jours qu'il n'en a été vendu.
    None : pas encore assez de matière pour que le chiffre veuille dire
    quelque chose (voir index_is_reliable).
    """
    if not index_is_reliable(project, agg, settings):
        return None
    real = real_day_rate(project, agg, costs)
    theo = theoretical_day_rate(project)
    if real is None or not theo:
        return None
    return round(real / theo, 3)


def projected_rentability_index(project, agg, settings, costs=0.0):
    """Indice projeté en fin de projet, au rythme actuel. C'est LUI qu'on
    peut regarder en cours de route : il suppose que le rythme observé se
    poursuit jusqu'au bout, au lieu de diviser par les seules heures déjà
    passées (ce qui donnait ×280 sur une heure saisie)."""
    spent = days_spent(agg)
    if spent <= 0 or (agg.get("entries_count") or 0) == 0:
        return None
    elapsed_pct = pct_time_elapsed(project)
    if elapsed_pct <= 0:
        return None
    projected_days = spent / (elapsed_pct / 100.0)
    if projected_days <= 0:
        return None
    theo = theoretical_day_rate(project)
    if not theo:
        return None
    projected_rate = (project["price_total"] - (costs or 0)) / projected_days
    return round(projected_rate / theo, 3)


def margin(project, costs=0.0):
    return round(project["price_total"] - (costs or 0), 2)


# --------------------------------------------------------- stats projet

def project_stats(project, agg, settings, costs=0.0, invoiced=None):
    agg = agg or EMPTY_AGG
    stats = {
        "total_days_sold": total_days_sold(project),
        "days_spent": days_spent(agg),
        "hours_sold": round(hours_sold(project, settings), 1),
        "hours_spent": hours_spent(agg, project, settings),
        "pct_consumed": pct_consumed(project, agg),
        "pct_time_elapsed": pct_time_elapsed(project),
        "days_remaining": days_remaining(project, agg),
        "theoretical_day_rate": theoretical_day_rate(project),
        "real_day_rate": real_day_rate(project, agg, costs),
        "theoretical_hourly_rate": theoretical_hourly_rate(project, settings),
        "real_hourly_rate": real_hourly_rate(project, agg, settings, costs),
        "rentability_index": rentability_index(project, agg, settings, costs),
        "projected_index": projected_rentability_index(project, agg, settings, costs),
        "index_reliable": index_is_reliable(project, agg, settings),
        "pace_status": pace_status(project, agg),
        "planned_end_date": planned_end_date(project),
        "projected_end_date": projected_end_date(project, agg),
        "entries_count": agg.get("entries_count") or 0,
        "costs": round(costs or 0, 2),
        "margin": margin(project, costs),
    }
    if invoiced is not None:
        stats["invoiced"] = round(invoiced.get("invoiced", 0), 2)
        stats["paid"] = round(invoiced.get("paid", 0), 2)
        stats["milestones_total"] = round(invoiced.get("total", 0), 2)
        stats["to_invoice"] = round(project["price_total"] - invoiced.get("invoiced", 0), 2)
    return stats


def tally_segments(project, agg, max_segments=40):
    total = total_days_sold(project)
    spent = days_spent(agg)
    if total <= 0:
        return {"unit": "day", "segments": []}

    unit, count = "day", total
    if total > max_segments:
        unit = "week"
        count = total_weeks(project)
        spent = spent / total * count

    n = max(1, round(count))
    per_segment = count / n if n else 0
    segments, remaining = [], spent
    for _ in range(n):
        if per_segment and remaining >= per_segment:
            segments.append(1.0)
            remaining -= per_segment
        elif remaining > 0 and per_segment:
            segments.append(round(remaining / per_segment, 3))
            remaining = 0
        else:
            segments.append(0.0)
    return {"unit": unit, "segments": segments}


# ------------------------------------------------------------ carte de charge

def project_is_active_on(project, day):
    return parse_date(project["start_date"]) <= day <= planned_end_date(project)


def daily_capacity(projects, window_start, window_days, include_provisional,
                   settings, absences=None):
    """Charge cumulée jour par jour sur la fenêtre.

    Le pourcentage d'un projet pour un jour ouvré vaut
    `jours_vendus_par_semaine / nombre_de_jours_ouvrés_par_semaine`. Un jour
    non ouvré (week-end, congé, férié) porte une charge nulle et le signale
    par `level = 'off'` : il ne doit ni colorer la carte, ni compter comme
    un jour non saisi.
    """
    absence_index = build_absence_index(absences or [])
    working = working_days_set(settings)
    n_working = len(working)

    days = []
    for i in range(window_days):
        day = window_start + timedelta(days=i)
        off_reason = None
        if day.weekday() not in working:
            off_reason = "week-end"
        elif day in absence_index:
            off_reason = absence_index[day]

        pct_confirmed = pct_provisional = 0.0
        contributors = []
        if not off_reason:
            for p in projects:
                if p["status"] not in ACTIVE_LOAD_STATUSES | OPTIONAL_LOAD_STATUSES:
                    continue
                if not project_is_active_on(p, day):
                    continue
                daily_pct = p["days_per_week"] / n_working * 100
                if p["status"] == "confirmed":
                    pct_confirmed += daily_pct
                    contributors.append({"name": p["name"], "pct": round(daily_pct, 1),
                                         "provisional": False})
                elif include_provisional:
                    pct_provisional += daily_pct
                    contributors.append({"name": p["name"], "pct": round(daily_pct, 1),
                                         "provisional": True})

        total = pct_confirmed + pct_provisional
        if off_reason:
            level = "off"
        elif total <= 0:
            level = "free"
        elif total <= settings["peak_threshold_warning"]:
            level = "light"
        elif total <= settings["peak_threshold_danger"]:
            level = "heavy"
        else:
            level = "storm"

        days.append({
            "date": day,
            "pct_confirmed": round(pct_confirmed, 1),
            "pct_provisional": round(pct_provisional, 1),
            "pct_total": round(total, 1),
            "level": level,
            "off_reason": off_reason,
            "has_provisional": pct_provisional > 0,
            "contributors": contributors,
        })
    return days


def capacity_summary(capacity):
    """Résumé chiffré d'une fenêtre de charge : de quoi écrire une phrase
    plutôt que de laisser lire une bande de couleurs."""
    working = [d for d in capacity if d["level"] != "off"]
    if not working:
        return {"working_days": 0, "avg_load": 0, "storm_days": 0, "free_days": 0}
    return {
        "working_days": len(working),
        "avg_load": round(sum(d["pct_total"] for d in working) / len(working), 1),
        "storm_days": sum(1 for d in working if d["level"] == "storm"),
        "heavy_days": sum(1 for d in working if d["level"] == "heavy"),
        "free_days": sum(1 for d in working if d["level"] == "free"),
    }


# ------------------------------------------------------------ jours non saisis

def missing_days(entries_by_day, settings, absences, today=None, lookback=14):
    """Jours ouvrés récents sans aucune saisie.

    Le jour même est exclu : il n'est pas fini, ce n'est pas un oubli.
    """
    today = today or date.today()
    absence_index = build_absence_index(absences or [])
    missing = []
    for i in range(1, lookback + 1):
        day = today - timedelta(days=i)
        if not is_working_day(day, settings, absence_index):
            continue
        if (entries_by_day.get(day.isoformat()) or 0) <= 0:
            missing.append(day)
    return sorted(missing)


# ------------------------------------------------------------------ alertes

def build_alerts(project_rows, capacity, milestones, missing, settings, today=None):
    """Liste d'alertes triées par gravité.

    Chaque alerte est un dict {level, text, url_name, url_arg} : la mise en
    forme reste au template, la décision reste ici (donc testable).
    """
    today = today or date.today()
    alerts = []
    budget_threshold = settings.get("budget_alert_pct", 80)

    for row in project_rows:
        p, stats = row["project"], row["stats"]
        if p["status"] not in LIVE_STATUSES:
            continue
        if stats["pct_consumed"] >= 100:
            alerts.append({
                "level": "danger",
                "text": f"{p['name']} a dépassé son budget vendu ({stats['pct_consumed']} %).",
                "url_name": "project_detail", "url_arg": p["id"],
            })
        elif stats["pct_consumed"] >= budget_threshold:
            alerts.append({
                "level": "warning",
                "text": f"{p['name']} a consommé {stats['pct_consumed']} % de son budget.",
                "url_name": "project_detail", "url_arg": p["id"],
            })
        if stats["pace_status"] == "behind":
            alerts.append({
                "level": "warning",
                "text": f"{p['name']} brûle son budget plus vite que le temps ne passe.",
                "url_name": "project_detail", "url_arg": p["id"],
            })
        end = stats["planned_end_date"]
        if p["status"] == "confirmed" and today <= end <= today + timedelta(days=14):
            alerts.append({
                "level": "info",
                "text": f"{p['name']} se termine le {end.isoformat()}.",
                "url_name": "project_detail", "url_arg": p["id"],
            })

    for m in milestones:
        if m["status"] == "todo" and m["due_date"] and m["due_date"] <= today.isoformat():
            alerts.append({
                "level": "danger",
                "text": f"Jalon à facturer en retard : {m['label']} ({m['project_name']}).",
                "url_name": "billing", "url_arg": None,
            })
        elif m["status"] == "invoiced" and m["invoiced_at"]:
            invoiced = parse_date(m["invoiced_at"][:10])
            if (today - invoiced).days > 30:
                alerts.append({
                    "level": "warning",
                    "text": f"Facture non encaissée depuis plus de 30 jours : {m['label']} ({m['project_name']}).",
                    "url_name": "billing", "url_arg": None,
                })

    storm = [d for d in capacity if d["level"] == "storm"]
    if storm:
        alerts.append({
            "level": "warning",
            "text": f"{len(storm)} jour(s) en surcharge sur la période affichée, à partir du {storm[0]['date'].isoformat()}.",
            "url_name": "planning", "url_arg": None,
        })

    if missing:
        jours = ", ".join(d.strftime("%d/%m") for d in missing[-4:])
        alerts.append({
            "level": "info",
            "text": f"{len(missing)} jour(s) ouvré(s) sans saisie : {jours}.",
            "url_name": "week", "url_arg": None,
        })

    order = {"danger": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: order[a["level"]])
    return alerts


# ------------------------------------------------------------------ revenu

def revenue_overview(projects, milestone_totals, invoiced_this_month,
                     invoiced_this_year, settings):
    """Vue d'ensemble du CA : réalisé vs objectif, et carnet de commandes.

    Le carnet de commandes (le "reste à facturer" des projets confirmés) est
    l'indicateur qui manque le plus quand on est indépendant : il dit
    combien de CA est déjà sécurisé, indépendamment de ce qui est encaissé.
    """
    backlog = provisional_pipeline = 0.0
    for p in projects:
        totals = milestone_totals.get(p["id"], {})
        invoiced = totals.get("invoiced", 0)
        remaining = max(0.0, (p["price_total"] or 0) - invoiced)
        if p["status"] in ("confirmed", "paused"):
            backlog += remaining
        elif p["status"] == "provisional":
            provisional_pipeline += p["price_total"] or 0

    monthly_goal = settings.get("monthly_revenue_goal", 0)
    annual_goal = settings.get("annual_revenue_goal", 0)
    return {
        "invoiced_month": round(invoiced_this_month, 2),
        "invoiced_year": round(invoiced_this_year, 2),
        "monthly_goal": monthly_goal,
        "annual_goal": annual_goal,
        "monthly_pct": round(invoiced_this_month / monthly_goal * 100, 1) if monthly_goal else None,
        "annual_pct": round(invoiced_this_year / annual_goal * 100, 1) if annual_goal else None,
        "backlog": round(backlog, 2),
        "provisional_pipeline": round(provisional_pipeline, 2),
    }


def client_rollup(project_rows, milestone_totals):
    """Agrégat par client, avec la part de CA que chacun représente.

    La concentration est l'information la plus utile ici : savoir qu'un
    client pèse 70 % de ton activité est un signal de risque, pas une
    statistique décorative.
    """
    clients = {}
    for row in project_rows:
        p, stats = row["project"], row["stats"]
        name = (p["client"] or "").strip() or "Sans client"
        c = clients.setdefault(name, {
            "client": name, "projects": 0, "revenue": 0.0, "invoiced": 0.0,
            "days_sold": 0.0, "days_spent": 0.0, "costs": 0.0,
        })
        c["projects"] += 1
        c["revenue"] += p["price_total"] or 0
        c["invoiced"] += milestone_totals.get(p["id"], {}).get("invoiced", 0)
        c["days_sold"] += stats["total_days_sold"]
        c["days_spent"] += stats["days_spent"]
        c["costs"] += stats["costs"]

    total_revenue = sum(c["revenue"] for c in clients.values()) or 1
    rows = []
    for c in clients.values():
        c["share"] = round(c["revenue"] / total_revenue * 100, 1)
        c["real_day_rate"] = round((c["revenue"] - c["costs"]) / c["days_spent"], 2) if c["days_spent"] else None
        c["sold_day_rate"] = round(c["revenue"] / c["days_sold"], 2) if c["days_sold"] else None
        for key in ("revenue", "invoiced", "days_sold", "days_spent", "costs"):
            c[key] = round(c[key], 2)
        rows.append(c)
    rows.sort(key=lambda r: r["revenue"], reverse=True)
    return rows


# ------------------------------------------------------------------ graphiques

def burndown_points(project, daily_entries, width=560, height=140, pad=26):
    """Points SVG de la courbe de cadence (jours cumulés vs référence)."""
    start = parse_date(project["start_date"])
    end = planned_end_date(project)
    total = total_days_sold(project)
    today = date.today()

    x_end = max(end, today) if daily_entries else end
    span = max((x_end - start).days, 1)

    def x_for(d):
        return pad + (d - start).days / span * (width - 2 * pad)

    def y_for(days_val):
        if total <= 0:
            return height - pad
        return (height - pad) - min(days_val / total, 1.3) * (height - 2 * pad)

    reference = f"{x_for(start):.1f},{y_for(0):.1f} {x_for(end):.1f},{y_for(total):.1f}"

    cumulative = 0.0
    points = [f"{x_for(start):.1f},{y_for(0):.1f}"]
    for e in daily_entries:
        cumulative += e["percent_of_day"] / 100.0
        points.append(f"{x_for(parse_date(e['entry_date'])):.1f},{y_for(cumulative):.1f}")
    if len(points) == 1:
        points.append(f"{x_for(today):.1f},{y_for(0):.1f}")

    return {
        "width": width, "height": height, "pad": pad,
        "reference": reference, "actual": " ".join(points),
        "today_x": f"{x_for(today):.1f}",
    }


def bar_chart(series, value_key, width=640, height=170, pad=30):
    """Barres verticales génériques (mois en x). Renvoie des rectangles
    prêts à poser dans un <svg>, pour ne pas avoir à embarquer une
    bibliothèque de graphiques."""
    if not series:
        return {"width": width, "height": height, "bars": [], "max": 0}
    maximum = max((s[value_key] or 0) for s in series) or 1
    inner_w = width - 2 * pad
    slot = inner_w / len(series)
    bar_w = max(6, slot * 0.6)
    bars = []
    for i, s in enumerate(series):
        value = s[value_key] or 0
        h = (value / maximum) * (height - 2 * pad)
        bars.append({
            "x": round(pad + i * slot + (slot - bar_w) / 2, 1),
            "y": round(height - pad - h, 1),
            "w": round(bar_w, 1),
            "h": round(max(h, 0), 1),
            "label": s["month"][5:] if len(s.get("month", "")) >= 7 else s.get("month", ""),
            "label_x": round(pad + i * slot + slot / 2, 1),
            "value": value,
        })
    return {"width": width, "height": height, "pad": pad, "bars": bars, "max": maximum}


def gantt_rows(projects, window_start, window_days):
    """Position de chaque projet sur la fenêtre, en index de colonne
    (0 = premier jour). Les projets débordants sont recadrés."""
    window_end = window_start + timedelta(days=window_days - 1)
    rows = []
    for p in projects:
        start, end = parse_date(p["start_date"]), planned_end_date(p)
        if end < window_start or start > window_end:
            continue
        clipped_start, clipped_end = max(start, window_start), min(end, window_end)
        start_idx = (clipped_start - window_start).days
        end_idx = (clipped_end - window_start).days
        rows.append({
            "project": p,
            "start_idx": start_idx,
            "end_idx": end_idx,
            "span": end_idx - start_idx + 1,
            "clipped_start": clipped_start > start,
            "clipped_end": clipped_end < end,
        })
    return rows
