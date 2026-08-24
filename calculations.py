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

def parse_date(value, default=None):
    """Convertit une date ISO. Avec `default` fourni, renvoie ce défaut au
    lieu de lever sur une valeur illisible.

    Défense en profondeur : une donnée corrompue déjà présente en base ne
    doit jamais faire tomber une page entière. La validation en entrée
    (côté routes) reste la première ligne ; celle-ci est le filet.
    """
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        if default is not None:
            return default
        raise


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

    `MAX_SPAN_DAYS` est un filet, pas la validation principale — celle-ci
    vit dans app.py (valid_date borne les années saisissables). Il protège
    une absence déjà en base avant ce correctif, dont la date de fin
    aberrante ferait sinon boucler cette fonction sur des dizaines de
    milliers de jours à chaque page qui affiche la carte de charge.
    """
    MAX_SPAN_DAYS = 730  # ~2 ans : au-delà, une fin de congé est presque
    # sûrement une faute de frappe plutôt qu'une vraie absence continue.
    index = {}
    for a in absences:
        start, end = parse_date(a["start_date"]), parse_date(a["end_date"])
        if end < start:
            start, end = end, start
        if (end - start).days > MAX_SPAN_DAYS:
            end = start + timedelta(days=MAX_SPAN_DAYS)
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


def resolve_price(day_rate, price_total, total_days):
    """Arbitre entre TJM et prix total, et renvoie (day_rate, price_total).

    Règle métier : le TJM prime s'il est renseigné, parce que c'est ainsi
    qu'on vend — le prix total en découle. Elle vivait dans une route, ce
    que la séparation des couches interdit.
    """
    if day_rate:
        return day_rate, round(day_rate * total_days, 2)
    return None, price_total if price_total is not None else 0.0


def planned_end_date(project):
    """Dernier jour couvert par le contrat — borne INCLUSE.

    Le `- 1` est le correctif : `start + semaines * 7` désignait le
    lendemain de la fin, pas la fin. Un projet d'une semaine démarré le
    lundi 2 finissait le lundi 9, donc s'étalait sur six jours ouvrés pour
    cinq jours vendus. Tout en découlait : une colonne de charge en trop
    par projet, une barre Gantt trop longue, un `pct_time_elapsed`
    sous-estimé, et l'alerte « fin prévue dépassée » qui arrivait un jour
    en retard.

    Avec la borne incluse, une semaine pleine va du lundi au dimanche : sept
    jours calendaires qui contiennent exactement les cinq jours ouvrés
    vendus. Inutile donc de reculer sur le dernier jour ouvré — le week-end
    porte déjà une charge nulle.
    """
    start = parse_date(project["start_date"])
    span_days = max(1, round(total_weeks(project) * 7))
    return start + timedelta(days=span_days - 1)


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


def field(row, key, default=None):
    """Lecture tolérante d'un champ, dict ou sqlite3.Row.

    Un dict lève KeyError, un sqlite3.Row lève IndexError. Les colonnes
    ajoutées par migration n'existent pas dans les projets fabriqués à la
    main par les tests ni dans un scénario simulé : sans ce garde-fou, la
    moindre colonne nouvelle faisait exploser tout le module de calcul là où
    elle aurait dû être simplement absente.
    """
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


# --------------------------------------------------------- reste à faire

# Au-delà de ce délai, une estimation de reste à faire n'est plus considérée
# comme fraîche : elle reste affichée, avec sa date, mais cesse de piloter
# la cadence. Une estimation de deux mois pesant autant qu'une estimation du
# jour, c'est pire que pas d'estimation du tout.
REMAINING_FRESH_DAYS = 21


def declared_remaining(project):
    """Reste à faire déclaré, en jours. None si non renseigné."""
    value = field(project, "remaining_days")
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def remaining_age_days(project, today=None):
    """Ancienneté de l'estimation, en jours. None si jamais renseignée."""
    stamp = field(project, "remaining_updated_at")
    if not stamp:
        return None
    try:
        updated = datetime.fromisoformat(str(stamp)).date()
    except ValueError:
        return None
    return ((today or date.today()) - updated).days


def remaining_is_fresh(project, today=None):
    age = remaining_age_days(project, today)
    return age is not None and age <= REMAINING_FRESH_DAYS


def estimate_at_completion(project, agg):
    """Jours totaux prévus à terminaison : consommé + reste déclaré.

    C'est le chiffre que pilote un cabinet, là où le prorata temporel se
    contente de supposer une consommation linéaire.
    """
    remaining = declared_remaining(project)
    if remaining is None:
        return None
    return round(days_spent(agg) + remaining, 2)


def eac_drift(project, agg):
    """Écart entre la terminaison prévue et le budget vendu, en jours.

    Positif = dépassement annoncé. C'est la seule alerte qui peut sonner
    AVANT que le budget soit consommé : déclarer huit jours de reste sur un
    projet à qui il en reste trois est un dépassement acquis, même si le
    compteur affiche encore 60 % de budget disponible.
    """
    eac = estimate_at_completion(project, agg)
    if eac is None:
        return None
    return round(eac - total_days_sold(project), 2)


def eac_drift_pct(project, agg):
    drift = eac_drift(project, agg)
    total = total_days_sold(project)
    if drift is None or total <= 0:
        return None
    return round(drift / total * 100, 1)


def pace_basis(project, today=None):
    """« declared » si la cadence s'appuie sur le reste à faire déclaré,
    « elapsed » si elle retombe sur le prorata temporel."""
    if declared_remaining(project) is not None and remaining_is_fresh(project, today):
        return "declared"
    return "elapsed"


def pace_delta(project, agg, today=None):
    """Écart de cadence, en points.

    Deux bases possibles, la meilleure disponible :

    - reste à faire déclaré et frais : l'écart est le dépassement annoncé en
      % du budget vendu. C'est ce que fait un pilotage de mission, et c'est
      insensible au rythme irrégulier ;
    - sinon : budget consommé moins temps écoulé, le prorata d'origine, qui
      suppose une consommation linéaire.
    """
    if pace_basis(project, today) == "declared":
        drift = eac_drift_pct(project, agg)
        if drift is not None:
            return drift
    return pct_consumed(project, agg) - pct_time_elapsed(project, today)


def pace_status(project, agg, today=None):
    """Cadence : compare le budget consommé au temps écoulé.

    Échelle symétrique. L'ancienne était biaisée dans le sens rassurant —
    le pire sens pour un outil d'alerte : `delta <= 5` renvoyait « en
    avance », donc un projet exactement dans les clous (delta = 0)
    s'affichait comme en avance, et un projet ayant brûlé 19 points de
    budget de plus que le temps écoulé passait pour normal.

        delta < -10      en avance
        -10 .. 10        dans les clous
        10 .. 25         tendu
        > 25             en retard

    Le delta vient du reste à faire déclaré quand il est frais, du prorata
    temporel sinon — voir pace_delta().
    """
    if (agg.get("entries_count") or 0) == 0:
        return "not_started"
    delta = pace_delta(project, agg, today)
    if delta < -10:
        return "ahead"
    if delta <= 10:
        return "on_track"
    if delta <= 25:
        return "tight"
    return "behind"


def working_days_between(start, end, settings, absence_index=None):
    """Nombre de jours ouvrés entre deux dates incluses.

    Sert de base commune aux deux projections : extrapoler sur des jours
    calendaires alors que la consommation ne se fait que les jours ouvrés
    donnait des projections décalées d'un facteur 7/5.
    """
    if end < start:
        return 0
    working = working_days_set(settings)
    absence_index = absence_index or {}
    count = 0
    day = start
    while day <= end:
        if day.weekday() in working and day not in absence_index:
            count += 1
        day += timedelta(days=1)
    return count


def projected_end_date(project, agg, settings=None, today=None, absences=None):
    """Date de fin au rythme observé, exprimée en jours ouvrés.

    None tant qu'il n'y a pas de rythme mesurable.
    """
    today = today or date.today()
    first = agg.get("first_date")
    spent = days_spent(agg)
    if not first or spent <= 0:
        return None

    if settings is None:
        return None
    absence_index = build_absence_index(absences or [])
    elapsed = working_days_between(parse_date(first), today, settings, absence_index)
    if elapsed <= 0:
        return None

    rate = spent / elapsed
    remaining = total_days_sold(project) - spent
    if remaining <= 0:
        return today
    needed = remaining / rate

    # On avance jour par jour en ne décomptant que les jours ouvrés.
    day, left = today, needed
    guard = 0
    while left > 0 and guard < 3000:
        day += timedelta(days=1)
        guard += 1
        if day.weekday() in working_days_set(settings) and day not in absence_index:
            left -= 1
    return day


# ------------------------------------------------------------ rentabilité

def theoretical_day_rate(project):
    total = total_days_sold(project)
    if total <= 0:
        return None
    return round(project["price_total"] / total, 2)


def real_day_rate(project, agg, costs=0.0):
    """Marge par jour réellement passé.

    Les coûts absorbés sont déduits, les frais refacturés ajoutés — voir
    net_of_costs. Sans cette déduction, l'indicateur mesurerait la
    productivité, pas la rentabilité.

    Le garde-fou de fiabilité est appliqué en amont par project_stats, qui
    est le point de décision unique.
    """
    spent = days_spent(agg)
    if spent <= 0:
        return None
    return round(net_of_costs(project, costs) / spent, 2)


def theoretical_hourly_rate(project, settings):
    sold = hours_sold(project, settings)
    if sold <= 0:
        return None
    return round(project["price_total"] / sold, 2)


def real_hourly_rate(project, agg, settings, costs=0.0):
    hours = hours_spent(agg, project, settings)
    if hours <= 0:
        return None
    return round(net_of_costs(project, costs) / hours, 2)


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


def projected_rentability_index(project, agg, settings, costs=0.0, today=None,
                                absences=None):
    """Indice projeté en fin de projet, au rythme actuel.

    Deux garde-fous, parce que sans eux cet indice était le PLUS volatil des
    deux au moment où on le présentait comme le plus lisible : diviser par
    un temps écoulé de 2 % faisait basculer d'×2,88 à ×0,96 pour une
    demi-journée saisie de plus.

    - Rien tant que moins de `min_projection_elapsed_pct` du temps est
      écoulé, ou moins de 3 saisies.
    - Extrapolation en jours ouvrés, comme `projected_end_date`, pour que
      les deux projections de la même page reposent sur la même base.
    """
    today = today or date.today()
    spent = days_spent(agg)
    if spent <= 0 or (agg.get("entries_count") or 0) < 3:
        return None

    min_elapsed = settings.get("min_projection_elapsed_pct", 10)
    elapsed_pct = pct_time_elapsed(project, today)
    if elapsed_pct < min_elapsed:
        return None

    absence_index = build_absence_index(absences or [])
    start = parse_date(project["start_date"])
    elapsed_days = working_days_between(start, today, settings, absence_index)
    total_days = working_days_between(start, planned_end_date(project),
                                      settings, absence_index)
    if elapsed_days <= 0 or total_days <= 0:
        return None

    projected_days = spent / elapsed_days * total_days
    if projected_days <= 0:
        return None
    theo = theoretical_day_rate(project)
    if not theo:
        return None
    return round((net_of_costs(project, costs) / projected_days) / theo, 3)


def split_costs(costs):
    """Accepte soit un nombre (tous coûts absorbés), soit un dict
    {'absorbed', 'rebilled'}. Renvoie toujours le couple."""
    if isinstance(costs, dict):
        return (costs.get("absorbed") or 0.0), (costs.get("rebilled") or 0.0)
    return (costs or 0.0), 0.0


def project_revenue(project, costs=0.0):
    """Revenu du projet, frais refacturés inclus : ils s'ajoutent au prix
    au lieu d'être déduits de la marge."""
    _absorbed, rebilled = split_costs(costs)
    return (project["price_total"] or 0) + rebilled


def net_of_costs(project, costs=0.0):
    """Prix vendu moins les coûts directs.

    Ce n'est PAS une marge : ça ignore le coût de ton propre temps.
    Renommé pour cette raison — l'appeler « marge » laissait croire que le
    chiffre répondait à « est-ce que je gagne de l'argent », alors qu'il
    répond seulement à « est-ce que je tiens mon budget de jours ».
    """
    absorbed, rebilled = split_costs(costs)
    return round((project["price_total"] or 0) + rebilled - absorbed, 2)


# Conservé comme alias : d'autres appels historiques l'utilisent.
margin = net_of_costs


def cost_day_rate(settings):
    """Ce que coûte une journée de ton temps.

    charges fixes annuelles ÷ jours facturables visés. C'est le chaînon
    manquant : en dessous de ce seuil, une journée vendue te fait perdre de
    l'argent, quel que soit l'indice de rentabilité affiché.
    """
    charges = settings.get("annual_fixed_costs", 0) or 0
    days = settings.get("billable_days_per_year", 0) or 0
    if charges <= 0 or days <= 0:
        return None
    return round(charges / days, 2)


def real_margin(project, agg, settings, costs=0.0):
    """Marge réelle : prix − coûts directs − coût des jours passés.

    Négative, elle signifie que le projet t'a coûté plus qu'il ne t'a
    rapporté, même si tu as respecté ton budget de jours.
    """
    rate = cost_day_rate(settings)
    if rate is None:
        return None
    return round(net_of_costs(project, costs) - days_spent(agg) * rate, 2)


def break_even(settings, days_billed_this_year):
    """Seuil de rentabilité annuel : jours déjà facturés contre jours
    nécessaires pour couvrir les charges fixes."""
    charges = settings.get("annual_fixed_costs", 0) or 0
    rate = cost_day_rate(settings)
    if charges <= 0 or not rate:
        return None
    needed = settings.get("billable_days_per_year", 0) or 0
    return {
        "days_needed": round(needed, 1),
        "days_done": round(days_billed_this_year, 1),
        "pct": round(days_billed_this_year / needed * 100, 1) if needed else None,
        "cost_day_rate": rate,
        "annual_fixed_costs": charges,
    }


# --------------------------------------------------------- stats projet

def work_in_progress(project, agg, invoiced=None):
    """Travail produit et pas encore facturé, valorisé au TJM théorique.

    Un indépendant qui facture par jalons produit avant d'encaisser. Ni le
    temps passé ni le CA facturé ne montrent seuls cet écart : c'est
    pourtant lui qui dit combien d'argent dort dans du travail déjà fait.

    Négatif = facturé d'avance, ce qui est une information tout aussi utile
    et ne doit donc pas être écrasé à zéro.
    """
    produced = round(days_spent(agg) * theoretical_day_rate(project), 2)
    billed = round((invoiced or {}).get("invoiced", 0) or 0, 2)
    return {"produced": produced, "invoiced": billed, "wip": round(produced - billed, 2)}


def project_stats(project, agg, settings, costs=0.0, invoiced=None, today=None,
                  absences=None):
    """Toutes les statistiques d'un projet, en une passe.

    `absences` est transmis aux deux projections. Il manquait : les appels
    passaient `today` en quatrième position positionnelle de
    `projected_end_date` et en cinquième de
    `projected_rentability_index`, ce qui laissait `absences` à None dans
    les deux cas. Tout le calcul en jours ouvrés hors congés
    (`working_days_between` + `absence_index`) était donc du code mort sur
    les pages réelles : trois semaines de congés déclarées ne décalaient pas
    d'un jour la date de fin projetée, alors qu'elles décalaient bien la
    carte de charge. Deux vues de la même app racontaient deux histoires.
    """
    agg = agg or EMPTY_AGG
    today = today or date.today()

    # Un seul point de décision pour toute la fiche : si l'indice n'est pas
    # fiable, AUCUN taux réel ne l'est non plus. Ils sortent du même
    # dénominateur (les jours passés). Afficher « 40 000 €/jour » à côté
    # d'un encadré expliquant qu'on ne peut rien conclure était contradictoire.
    reliable = index_is_reliable(project, agg, settings)

    stats = {
        "total_days_sold": total_days_sold(project),
        "days_spent": days_spent(agg),
        "hours_sold": round(hours_sold(project, settings), 1),
        "hours_spent": hours_spent(agg, project, settings),
        "pct_consumed": pct_consumed(project, agg),
        "pct_time_elapsed": pct_time_elapsed(project, today),
        "days_remaining": days_remaining(project, agg),
        "theoretical_day_rate": theoretical_day_rate(project),
        "real_day_rate": real_day_rate(project, agg, costs) if reliable else None,
        "theoretical_hourly_rate": theoretical_hourly_rate(project, settings),
        "real_hourly_rate": real_hourly_rate(project, agg, settings, costs) if reliable else None,
        "rentability_index": rentability_index(project, agg, settings, costs),
        "projected_index": projected_rentability_index(project, agg, settings, costs,
                                                       today, absences),
        "index_reliable": reliable,
        "pace_status": pace_status(project, agg, today),
        "pace_basis": pace_basis(project, today),
        "pace_delta": round(pace_delta(project, agg, today), 1),
        "remaining_declared": declared_remaining(project),
        "remaining_age_days": remaining_age_days(project, today),
        "remaining_fresh": remaining_is_fresh(project, today),
        "eac_days": estimate_at_completion(project, agg),
        "eac_drift_days": eac_drift(project, agg),
        "eac_drift_pct": eac_drift_pct(project, agg),
        "planned_end_date": planned_end_date(project),
        "projected_end_date": projected_end_date(project, agg, settings, today, absences),
        "entries_count": agg.get("entries_count") or 0,
        "costs": round(split_costs(costs)[0], 2),
        "rebilled_costs": round(split_costs(costs)[1], 2),
        "net_of_costs": net_of_costs(project, costs),
        "margin": net_of_costs(project, costs),  # alias historique
        "cost_of_days": (round(days_spent(agg) * cost_day_rate(settings), 2)
                         if cost_day_rate(settings) else None),
        "real_margin": real_margin(project, agg, settings, costs),
    }
    if invoiced is not None:
        stats["invoiced"] = round(invoiced.get("invoiced", 0), 2)
        stats["paid"] = round(invoiced.get("paid", 0), 2)
        stats["milestones_total"] = round(invoiced.get("total", 0), 2)
        stats["to_invoice"] = round(project["price_total"] - invoiced.get("invoiced", 0), 2)
        stats["wip"] = work_in_progress(project, agg, invoiced)
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

def project_weekdays(project, settings):
    """Jours de la semaine occupés par ce projet.

    Si le projet déclare des jours précis, la charge s'y concentre : un
    projet vendu 2 j/semaine sur lundi et mercredi remplit 100 % de ces deux
    jours et 0 % des autres. Sinon elle est lissée sur tous les jours
    ouvrés — un projet à 2 j/semaine affiche alors 40 % partout, ce qui est
    une moyenne honnête mais pas un planning.
    """
    working = working_days_set(settings)
    try:
        raw = project["weekdays"]
    except (KeyError, IndexError):
        raw = None
    if not raw:
        return working, False
    chosen = {int(part) for part in str(raw).split(",")
              if part.strip().isdigit() and 0 <= int(part.strip()) <= 6}
    chosen &= working
    return (chosen, True) if chosen else (working, False)


def project_daily_pct(project, day, settings):
    """Charge que ce projet pose sur ce jour précis, en pourcentage.

    Même formule dans les deux cas : jours vendus par semaine ÷ nombre de
    jours qui les portent. Les jours déclarés étaient auparavant plafonnés
    à 100 % (`min(..., 1.0)`), pas les jours lissés — donc un projet vendu
    5 j/semaine concentré sur lundi et mardi s'affichait à 100 % au lieu de
    250 %, et la grille d'allocation comptait 2 jours engagés là où le
    contrat en vendait 5.

    Le plafond mentait dans le sens rassurant, et précisément là où le
    planning est censé être littéral : déclarer des jours impossibles doit
    faire réagir la carte, pas la calmer. Un dépassement de 100 % est une
    information, pas une valeur à écrêter — c'est déjà ce que fait le
    lissage, et ce que fait le cumul de deux projets sur un même jour.
    """
    days, _explicit = project_weekdays(project, settings)
    if day.weekday() not in days:
        return 0.0
    return project["days_per_week"] / len(days) * 100


def project_is_active_on(project, day, overrun_weeks=0):
    """Un projet occupe-t-il ce jour-là ?

    `overrun_weeks` prolonge la fenêtre au-delà de la fin planifiée pour les
    projets encore vivants. Sans cette prolongation, un projet confirmé
    consommé à 130 %, deux semaines après sa fin prévue, portait 0 % de
    charge — précisément au moment où il mange les journées à venir. La
    carte devenait purement contractuelle et ignorait le réel.
    """
    start = parse_date(project["start_date"])
    end = planned_end_date(project)
    if overrun_weeks and project["status"] in ("confirmed", "provisional"):
        end = end + timedelta(weeks=overrun_weeks)
    return start <= day <= end


def project_is_overrunning(project, day):
    """Le jour tombe-t-il dans la prolongation, au-delà de la fin prévue ?"""
    return day > planned_end_date(project)


def _project_load_profile(project, settings, overrun_weeks):
    """Ce qui ne dépend PAS du jour, calculé une fois par projet.

    daily_capacity() appelait project_weekdays(), planned_end_date() et
    parse_date(project["start_date"]) une fois PAR JOUR PAR PROJET — sur
    une fenêtre de 56 jours (8 semaines), c'est 56 fois plus de travail que
    nécessaire, puisqu'aucune de ces valeurs ne change d'un jour à l'autre
    pour un même projet. La boucle jour n'a plus qu'à comparer des dates et
    tester une appartenance à un ensemble.
    """
    weekdays, _explicit = project_weekdays(project, settings)
    pct_per_day = project["days_per_week"] / len(weekdays) * 100 if weekdays else 0.0
    start = parse_date(project["start_date"])
    planned_end = planned_end_date(project)
    end = planned_end
    if overrun_weeks and project["status"] in ("confirmed", "provisional"):
        end = planned_end + timedelta(weeks=overrun_weeks)
    return {"weekdays": weekdays, "pct_per_day": pct_per_day,
           "start": start, "end": end, "planned_end": planned_end}


def daily_capacity(projects, window_start, window_days, include_provisional,
                   settings, absences=None):
    """Charge cumulée jour par jour sur la fenêtre.

    Le pourcentage d'un projet pour un jour ouvré vaut
    `jours_vendus_par_semaine / nombre_de_jours_ouvrés_par_semaine`. Un jour
    non ouvré (week-end, congé, férié) porte une charge nulle et le signale
    par `level = 'off'` : il ne doit ni colorer la carte, ni compter comme
    un jour non saisi.

    Un projet non terminé prolonge sa charge au-delà de sa fin planifiée,
    dans la limite de `overrun_weeks` — c'est le dépassement, signalé à part.
    """
    absence_index = build_absence_index(absences or [])
    working = working_days_set(settings)
    overrun_weeks = settings.get("overrun_weeks", 4)

    eligible = [p for p in projects if p["status"] in ACTIVE_LOAD_STATUSES | OPTIONAL_LOAD_STATUSES]
    profiles = [(p, _project_load_profile(p, settings, overrun_weeks)) for p in eligible]

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
            for p, profile in profiles:
                if not (profile["start"] <= day <= profile["end"]):
                    continue
                if day.weekday() not in profile["weekdays"]:
                    continue
                daily_pct = profile["pct_per_day"]
                if daily_pct <= 0:
                    continue
                overrun = day > profile["planned_end"]
                if p["status"] == "confirmed":
                    pct_confirmed += daily_pct
                    contributors.append({"name": p["name"], "pct": round(daily_pct, 1),
                                         "provisional": False, "overrun": overrun})
                elif include_provisional:
                    pct_provisional += daily_pct
                    contributors.append({"name": p["name"], "pct": round(daily_pct, 1),
                                         "provisional": True, "overrun": overrun})

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
            "has_overrun": any(c["overrun"] for c in contributors),
            "contributors": contributors,
        })
    return days


SCENARIO_ID = -1
SCENARIO_CLIENT = "Simulation"


def scenario_project(name, start_date, days_per_week, duration_value,
                     duration_unit="weeks", weekdays=None):
    """Projet FICTIF, jamais écrit en base, injecté dans les calculs de charge.

    La page Planning répondait à « puis-je accepter un projet de plus ? » à
    condition de créer d'abord le projet, donc de polluer la base, les
    exports et le carnet de commandes pour répondre à une question qui
    n'engage à rien. Le scénario vit le temps d'une URL.

    Statut « confirmed » à dessein : on simule un projet SIGNÉ, sinon la
    simulation disparaîtrait au premier décochage des provisoires — c'est-à-
    dire précisément quand on veut comparer au socle certain.
    """
    return {
        "id": SCENARIO_ID,
        "name": name or "Projet simulé",
        "client": SCENARIO_CLIENT,
        "client_id": None,
        "status": "confirmed",
        "days_per_week": float(days_per_week),
        "duration_value": float(duration_value),
        "duration_unit": duration_unit if duration_unit in ("weeks", "months") else "weeks",
        "day_rate": None,
        "price_total": 0.0,
        "hours_per_day": None,
        "start_date": start_date,
        "weekdays": weekdays or "",
        "color": "#6B4FA8",
        "notes": "",
        "archived": 0,
        "remaining_days": None,
        "remaining_updated_at": None,
        "created_at": "",
        "updated_at": None,
        "is_scenario": True,
    }


def scenario_impact(before, after):
    """Différence entre deux grilles d'allocation, avant et après scénario.

    Un planning simulé qui affiche seulement l'état final oblige à comparer
    deux écrans de tête. Ce qui décide, c'est le delta : combien de jours
    libres il reste, et combien de semaines passent en surcharge.
    """
    weeks = []
    over_before = sum(1 for t in before["totals"] if t["over"])
    over_after = sum(1 for t in after["totals"] if t["over"])
    for i, col in enumerate(after["columns"]):
        b, a = before["totals"][i], after["totals"][i]
        weeks.append({
            "label": col["label"],
            "free_before": b["free"], "free_after": a["free"],
            "booked_after": a["booked"], "capacity": a["capacity"],
            "pct_after": a["pct"], "over": a["over"], "newly_over": a["over"] and not b["over"],
        })
    return {
        "weeks": weeks,
        "free_before": round(sum(max(0, t["free"]) for t in before["totals"]), 2),
        "free_after": round(sum(max(0, t["free"]) for t in after["totals"]), 2),
        "over_before": over_before,
        "over_after": over_after,
        "newly_over": sum(1 for w in weeks if w["newly_over"]),
        "feasible": over_after == over_before,
    }


def weekly_load(capacity, today=None):
    """Regroupe la charge quotidienne par semaine, en JOURS et en phrases.

    La carte de charge répond bien à « est-ce tenable ? », mais elle demande
    de décoder des hauteurs et des couleurs pour en tirer le seul chiffre
    dont on a besoin pour décider : combien de jours il reste. Cette lecture
    donne directement ce chiffre, semaine par semaine, sans rien inventer —
    elle réagrège exactement les mêmes journées.

    Les jours se comptent en jours et non en pourcentages : « 2 jours
    libres » se lit sans effort, « 60 % de charge moyenne » demande une
    division mentale par le nombre de jours ouvrés de la semaine.
    """
    today = today or date.today()
    semaines = {}
    for jour in capacity:
        debut = week_monday(jour["date"])
        bloc = semaines.setdefault(debut, {
            "start": debut, "days": 0, "booked": 0.0, "provisional": 0.0,
            "overrun": False,
        })
        if jour["level"] == "off":
            continue
        bloc["days"] += 1
        bloc["booked"] += jour["pct_total"] / 100.0
        bloc["provisional"] += jour["pct_provisional"] / 100.0
        bloc["overrun"] = bloc["overrun"] or jour["has_overrun"]

    lignes = []
    for debut in sorted(semaines):
        bloc = semaines[debut]
        capacite = bloc["days"]
        engage = round(bloc["booked"], 2)
        libre = round(capacite - engage, 2)

        if capacite == 0:
            niveau, phrase = "off", "semaine non travaillée"
        elif libre < -0.05:
            niveau = "over"
            phrase = f"{_jours(-libre)} de trop"
        elif libre < 0.5:
            niveau = "full"
            phrase = "complet"
        elif libre < 1.5:
            niveau = "tight"
            phrase = f"{_jours(libre)} libre"
        else:
            niveau = "free"
            phrase = f"{_jours(libre)} libres"

        lignes.append({
            "start": debut,
            "end": debut + timedelta(days=6),
            "label": debut.strftime("%d/%m"),
            "capacity_days": capacite,
            "booked_days": engage,
            "provisional_days": round(bloc["provisional"], 2),
            "free_days": libre,
            "pct": round(engage / capacite * 100, 1) if capacite else 0,
            "level": niveau,
            "phrase": phrase,
            "has_overrun": bloc["overrun"],
            "is_current": debut <= today <= debut + timedelta(days=6),
        })
    return lignes


def _nombre(valeur):
    """Virgule décimale, pas point : « 13,3 » et non « 13.3 »."""
    return f"{valeur:g}".replace(".", ",")


def _argent(valeur, devise="€"):
    """« 3 750 € » — espace fine avant le millier, comme partout ailleurs
    dans l'app. Un montant collé (« 3750 ») se relit deux fois."""
    return f"{valeur:,.0f}".replace(",", " ") + devise


def _jours(valeur):
    """« 1 jour », « 2,5 jours » — la virgule décimale, pas le point."""
    arrondi = round(valeur * 2) / 2      # au demi-jour : l'unité de vente
    texte = _nombre(arrondi)
    return f"{texte} jour" if arrondi <= 1 else f"{texte} jours"


def weekly_headline(lignes):
    """Une phrase pour toute la fenêtre, à mettre en tête.

    Sans elle, la lecture par semaine reste un tableau à parcourir : ce
    qu'on veut savoir en arrivant, c'est s'il y a un problème et quand.
    """
    if not lignes:
        return "Aucune semaine travaillée sur la période."
    surcharge = [l for l in lignes if l["level"] == "over"]
    libres = round(sum(l["free_days"] for l in lignes if l["free_days"] > 0), 1)
    # « 0 jour disponibles ailleurs » : la phrase de repli ne doit pas se
    # déclencher quand il n'y a rien à replier.
    ailleurs = f" {_jours(libres)} disponibles ailleurs." if libres >= 0.5 else ""
    if surcharge:
        premiere = surcharge[0]
        return (f"{len(surcharge)} semaine{'s' if len(surcharge) > 1 else ''} en surcharge, "
                f"à partir du {premiere['label']}.{ailleurs}")
    if libres < 0.5:
        return "Aucune semaine en surcharge, et plus un jour de libre sur la période."
    return f"Aucune semaine en surcharge. {_jours(libres)} disponibles sur la période."


def mission_review(project, agg, settings, costs=0.0, invoiced=None, tasks=None,
                   scope_changes=None, milestones=None, today=None):
    """Bilan d'une mission : ce qui était vendu, ce qui a été fait, l'écart.

    Toutes les données existent déjà, éparpillées sur la fiche projet, le
    comparatif et la facturation. Les rassembler sur une page n'ajoute aucun
    calcul nouveau, mais change ce qu'on peut en faire : la fiche sert à
    piloter une mission en cours, ce bilan sert à chiffrer la SUIVANTE.

    Aucune recommandation ici, seulement des constats calculés. « Vends 20 %
    de plus » serait un conseil que l'app n'a pas les moyens de donner ;
    « il aurait fallu vendre 47,5 jours pour tenir ton TJM » est un fait.
    """
    today = today or date.today()
    stats = project_stats(project, agg, settings, costs, invoiced, today)
    devise = settings.get("currency_symbol", "€")

    vendu = total_days_sold(project)
    passe = days_spent(agg)
    ecart = round(passe - vendu, 2)
    ecart_pct = round(ecart / vendu * 100, 1) if vendu > 0 else None

    # Durée calendaire réellement occupée, des premières aux dernières
    # saisies. Une mission de six semaines étalée sur quatre mois n'a pas
    # coûté plus de jours, mais elle a occupé la tête plus longtemps.
    #
    # Conditionné au fait qu'il y ait des saisies : sur un projet vierge,
    # des bornes de dates sans aucune journée derrière décriraient une
    # période pendant laquelle il ne s'est rien passé.
    a_des_saisies = (agg.get("entries_count") or 0) > 0
    debut_reel = (parse_date(agg.get("first_date"), None)
                  if a_des_saisies and agg.get("first_date") else None)
    fin_reelle = (parse_date(agg.get("last_date"), None)
                  if a_des_saisies and agg.get("last_date") else None)
    semaines_reelles = (round(((fin_reelle - debut_reel).days + 1) / 7, 1)
                        if debut_reel and fin_reelle else None)

    taches = []
    total_taches = sum(t["days"] for t in (tasks or [])) or 0
    for t in (tasks or []):
        taches.append({"name": t["name"], "days": round(t["days"], 2),
                       "pct": round(t["days"] / total_taches * 100, 1) if total_taches else 0})
    taches.sort(key=lambda t: t["days"], reverse=True)

    # Délai d'encaissement réellement constaté sur CE projet, pas la moyenne
    # du client : c'est celui-là qui dit comment s'est passée cette mission.
    delais = []
    for m in (milestones or []):
        if m["status"] == "paid" and m["invoiced_at"] and m["paid_at"]:
            delais.append((parse_date(m["paid_at"][:10]) - parse_date(m["invoiced_at"][:10])).days)
    delai_moyen = round(sum(delais) / len(delais)) if delais else None

    constats = []
    if vendu > 0 and passe > 0:
        if ecart > 0.5:
            constats.append(
                f"{_jours(passe)} passés pour {_jours(vendu)} vendus, "
                f"soit {ecart_pct:+.0f} %.")
            if stats["theoretical_day_rate"] and stats["real_day_rate"] is not None:
                constats.append(
                    f"Le prix par jour réellement obtenu est de "
                    f"{_argent(stats['real_day_rate'], devise)} contre "
                    f"{_argent(stats['theoretical_day_rate'], devise)} vendus.")
            prix = project_revenue(project, costs)
            if stats["theoretical_day_rate"]:
                manque = round(passe * stats["theoretical_day_rate"] - prix, 0)
                if manque > 0:
                    constats.append(
                        f"Pour tenir le prix par jour vendu, il aurait fallu "
                        f"facturer {_argent(manque, devise)} de plus, ou vendre "
                        f"{_jours(passe)} dès le départ.")
        elif ecart < -0.5:
            constats.append(
                f"{_jours(passe)} passés pour {_jours(vendu)} vendus : "
                f"{_jours(-ecart)} de moins que prévu.")
        else:
            constats.append(f"Budget tenu : {_jours(passe)} pour {_jours(vendu)} vendus.")

    if semaines_reelles and total_weeks(project):
        prevu = round(total_weeks(project), 1)
        if semaines_reelles > prevu * 1.2:
            constats.append(
                f"La mission s'est étalée sur {_nombre(semaines_reelles)} semaines "
                f"pour {_nombre(prevu)} prévues.")

    # Le constat par tâche ne vaut que s'il y a des tâches. Sur un projet
    # sans découpage, tout le temps tombe dans « Sans tâche » et la phrase
    # « 100 % du temps est parti sur Sans tâche » n'apprend rien.
    nommees = [t for t in taches if t["name"] != "Sans tâche"]
    if nommees and nommees[0]["pct"] >= 40:
        constats.append(
            f"{nommees[0]['pct']:.0f} % du temps est parti sur « {nommees[0]['name']} ».")

    if scope_changes:
        constats.append(
            f"Le périmètre a changé {len(scope_changes)} fois en cours de route.")

    if delai_moyen is not None:
        constats.append(f"Les factures ont été encaissées en {delai_moyen} jours en moyenne.")

    return {
        "stats": stats,
        "days_sold": vendu,
        "days_spent": passe,
        "days_gap": ecart,
        "days_gap_pct": ecart_pct,
        "first_entry": debut_reel,
        "last_entry": fin_reelle,
        "weeks_planned": round(total_weeks(project), 1),
        "weeks_actual": semaines_reelles,
        "tasks": taches,
        "scope_changes": list(scope_changes or []),
        "payment_delay": delai_moyen,
        "milestones": list(milestones or []),
        "constats": constats,
    }


def capacity_scale(capacity, minimum=120):
    """Hauteur de référence des colonnes de la carte de charge.

    Au moins `minimum` pour que la ligne des 100 % reste visible même sur une
    période calme, et au moins le pic réel pour qu'aucune colonne ne soit
    tronquée. Arrondi à la dizaine supérieure : une échelle qui bouge à
    chaque pourcentage rendrait deux semaines incomparables entre elles.
    """
    peak = max((d["pct_total"] for d in capacity), default=0)
    return max(minimum, int((peak + 9) // 10 * 10))


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

def build_alerts(project_rows, capacity, milestones, missing, today=None):
    """Liste d'alertes triées par gravité.

    Volontairement muette sur tout ce qui concerne un projet précis —
    budget, cadence, dépassement de fin — parce que late_projects() (le
    bandeau rouge du tableau de bord) le dit déjà, en réunissant toutes les
    raisons d'un même projet sur UNE ligne plutôt que d'en éparpiller une
    par raison ici avec une formulation différente. Avant ce correctif, un
    projet en retard apparaissait deux fois sur la même page, sous deux
    formes qui se recoupaient sans se répondre exactement.

    Ce qui reste ici est ce que late_projects() ne couvre pas : les jalons
    de facturation, les jours non saisis, la surcharge de la carte de
    charge, et l'échéance qui approche sans être encore un dépassement (donc
    hors du champ de late_projects, qui ne parle que de ce qui a DÉJÀ
    débordé).
    """
    today = today or date.today()
    alerts = []

    for row in project_rows:
        p, stats = row["project"], row["stats"]
        if p["status"] not in LIVE_STATUSES:
            continue
        # Dépassement ANNONCÉ : le reste à faire déclaré fait sortir le
        # projet de son budget, même si le compteur de consommation est
        # encore rassurant. C'est la seule alerte qui arrive avant les
        # faits, donc la seule qui laisse encore le temps de renégocier.
        drift = stats.get("eac_drift_days")
        if drift is not None and stats.get("remaining_fresh") and drift > 0.5:
            alerts.append({
                "level": "danger" if (stats.get("eac_drift_pct") or 0) > 10 else "warning",
                "text": (f"{p['name']} : le reste à faire annoncé dépasse le budget de "
                         f"{drift:.1f} j."),
                "url_name": "project_detail", "url_arg": p["id"],
            })
        elif (stats.get("remaining_declared") is not None
              and not stats.get("remaining_fresh")
              and (stats.get("remaining_age_days") or 0) > REMAINING_FRESH_DAYS):
            alerts.append({
                "level": "info",
                "text": (f"{p['name']} : le reste à faire date de "
                         f"{stats['remaining_age_days']} jours, la cadence est repassée "
                         f"au prorata."),
                "url_name": "project_detail", "url_arg": p["id"],
            })

        if p["status"] != "confirmed":
            continue
        end = stats["planned_end_date"]
        if today <= end <= today + timedelta(days=14):
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
                    "text": (f"Facture non encaissée depuis plus de 30 jours : "
                             f"{m['label']} ({m['project_name']})."),
                    "url_name": "billing", "url_arg": None,
                })

    storm = [d for d in capacity if d["level"] == "storm"]
    if storm:
        alerts.append({
            "level": "warning",
            "text": (f"{len(storm)} jour(s) en surcharge sur la période affichée, "
                     f"à partir du {storm[0]['date'].isoformat()}."),
            "url_name": "planning", "url_arg": None,
        })

    if missing:
        jours = ", ".join(d.strftime("%d/%m") for d in missing[-4:])
        alerts.append({
            "level": "info",
            "text": f"{len(missing)} jour(s) ouvré(s) sans saisie : {jours}.",
            "url_name": "week", "url_arg": None,
            # Permet de clore l'alerte pour un jour légitimement non
            # travaillé mais non déclaré : sans ça, elle revient chaque
            # jour et on apprend à l'ignorer.
            "dismiss_day": missing[-1].isoformat(),
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


def client_rollup(project_rows, milestone_totals, settings=None):
    """Agrégat par client, avec la part de CA que chacun représente.

    La concentration est l'information la plus utile ici : savoir qu'un
    client pèse 70 % de ton activité est un signal de risque, pas une
    statistique décorative.

    Le revenu inclut les frais refacturés et exclut les coûts absorbés,
    exactement comme `net_of_costs` sur la fiche projet : les deux pages
    doivent donner le même chiffre pour le même projet.
    """
    clients = {}
    partial = set()
    for row in project_rows:
        p, stats = row["project"], row["stats"]
        name = (p["client"] or "").strip() or "Sans client"
        c = clients.setdefault(name, {
            "client": name, "projects": 0, "revenue": 0.0, "invoiced": 0.0,
            "days_sold": 0.0, "days_spent": 0.0, "costs": 0.0, "rebilled": 0.0,
            "reliable_days": 0.0, "reliable_net": 0.0,
        })
        c["projects"] += 1
        c["revenue"] += p["price_total"] or 0
        c["rebilled"] += stats.get("rebilled_costs", 0)
        c["invoiced"] += milestone_totals.get(p["id"], {}).get("invoiced", 0)
        c["days_sold"] += stats["total_days_sold"]
        c["days_spent"] += stats["days_spent"]
        c["costs"] += stats["costs"]

        # Le taux réel n'agrège que les projets dont l'indice est fiable :
        # mélanger un projet à 2 % de consommation avec un projet terminé
        # produisait une moyenne qui ne voulait rien dire.
        if stats["index_reliable"]:
            c["reliable_days"] += stats["days_spent"]
            c["reliable_net"] += stats["net_of_costs"]
        elif stats["days_spent"] > 0:
            partial.add(name)

    total_revenue = sum(c["revenue"] for c in clients.values()) or 1
    rows = []
    for c in clients.values():
        c["share"] = round(c["revenue"] / total_revenue * 100, 1)
        c["real_day_rate"] = (round(c["reliable_net"] / c["reliable_days"], 2)
                              if c["reliable_days"] else None)
        c["sold_day_rate"] = round(c["revenue"] / c["days_sold"], 2) if c["days_sold"] else None
        c["partial"] = c["client"] in partial
        if settings:
            rate = cost_day_rate(settings)
            c["real_margin"] = (round(c["reliable_net"] - c["reliable_days"] * rate, 2)
                                if rate and c["reliable_days"] else None)
        else:
            c["real_margin"] = None
        for key in ("revenue", "invoiced", "days_sold", "days_spent", "costs", "rebilled"):
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
        # Une entrée à date illisible est ignorée du tracé plutôt que de
        # faire tomber la fiche projet — qui est la seule page depuis
        # laquelle on peut justement supprimer cette entrée.
        day = parse_date(e["entry_date"], default=False)
        if day is False:
            continue
        cumulative += e["percent_of_day"] / 100.0
        points.append(f"{x_for(day):.1f},{y_for(cumulative):.1f}")
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


def gantt_rows(projects, window_start, window_days, settings=None, today=None):
    """Position de chaque projet sur la fenêtre, en index de colonne
    (0 = premier jour). Les projets débordants sont recadrés.

    La barre est prolongée au-delà de la fin planifiée pour les projets
    encore vivants, de la même façon que la carte de charge : les deux
    doivent raconter la même histoire.
    """
    window_end = window_start + timedelta(days=window_days - 1)
    overrun_weeks = (settings or {}).get("overrun_weeks", 4)
    today = today or date.today()
    rows = []
    for p in projects:
        start = parse_date(p["start_date"])
        planned = planned_end_date(p)
        end = planned
        overrun = False
        if p["status"] in ("confirmed", "provisional") and planned < today:
            end = min(planned + timedelta(weeks=overrun_weeks), max(today, planned))
            overrun = end > planned
        if end < window_start or start > window_end:
            continue
        clipped_start, clipped_end = max(start, window_start), min(end, window_end)
        start_idx = (clipped_start - window_start).days
        end_idx = (clipped_end - window_start).days
        planned_idx = (min(planned, window_end) - window_start).days
        rows.append({
            "project": p,
            "start_idx": start_idx,
            "end_idx": end_idx,
            "span": end_idx - start_idx + 1,
            "clipped_start": clipped_start > start,
            "clipped_end": clipped_end < end,
            "overrun": overrun,
            "planned_idx": planned_idx,
        })
    return rows


# ------------------------------------------------- prévisionnel d'encaissement

DEFAULT_PAYMENT_DELAY = 30  # jours, faute d'historique pour ce client


def cash_forecast(open_milestones, delays, months=3, today=None, contractual=None):
    """Projette les encaissements à venir, mois par mois.

    Un jalon déjà facturé est daté à `invoiced_at + délai du client` ; un
    jalon encore à facturer, à `échéance + délai`. Toutes les données
    existaient déjà — il manquait seulement de les croiser.

    Pour un indépendant, savoir ce qui tombe en octobre est plus actionnable
    que de savoir quel projet a le meilleur indice.

    Ordre de préférence pour le délai : constaté sur l'historique, puis
    contractuel (fiche client), puis valeur par défaut.
    """
    contractual = contractual or {}
    today = today or date.today()
    buckets = {}
    for i in range(months):
        month = (today.replace(day=1) + timedelta(days=32 * i)).replace(day=1)
        buckets[month.strftime("%Y-%m")] = {"month": month.strftime("%Y-%m"),
                                            "amount": 0.0, "items": []}

    overdue = []
    for m in open_milestones:
        client = m["project_client"] or "Sans client"
        delay = delays.get(client, {}).get(
            "days", contractual.get(client, DEFAULT_PAYMENT_DELAY))

        if m["status"] == "invoiced" and m["invoiced_at"]:
            base = parse_date(m["invoiced_at"][:10], default=None)
        elif m["due_date"]:
            base = parse_date(m["due_date"][:10], default=None)
        else:
            base = None
        if base is None:
            continue

        expected = base + timedelta(days=round(delay))
        if expected < today:
            overdue.append({"milestone": m, "expected": expected,
                            "late_days": (today - expected).days})
            continue

        key = expected.strftime("%Y-%m")
        if key in buckets:
            buckets[key]["amount"] += m["amount"] or 0
            buckets[key]["items"].append({"label": m["label"],
                                          "project": m["project_name"],
                                          "client": client,
                                          "amount": m["amount"] or 0,
                                          "expected": expected,
                                          "estimated": m["status"] != "invoiced"})

    series = list(buckets.values())
    for b in series:
        b["amount"] = round(b["amount"], 2)
        b["items"].sort(key=lambda i: i["expected"])
    overdue.sort(key=lambda o: o["expected"])
    return {"months": series, "overdue": overdue,
            "total": round(sum(b["amount"] for b in series), 2)}


# ------------------------------------------------ grille d'allocation

def allocation_grid(projects, window_start, weeks, settings, absences=None,
                    include_provisional=True, today=None):
    """Grille projets × semaines : combien de jours chaque projet occupe
    chaque semaine, plus la capacité disponible en regard.

    C'est la vue des outils de planification de charge (Float, Runn) :
    une barre continue dit seulement « ce projet court de mars à mai », là
    où un chiffre par semaine dit « cette semaine-là, il me prend 2 jours
    sur les 5 dont je dispose ». La seconde information est la seule qui
    permette de décider si on peut accepter un projet de plus.
    """
    today = today or date.today()
    absence_index = build_absence_index(absences or [])
    working = working_days_set(settings)
    overrun_weeks = settings.get("overrun_weeks", 4)

    columns = []
    for w in range(weeks):
        start = window_start + timedelta(weeks=w)
        end = start + timedelta(days=6)
        capacity_days = sum(
            1 for i in range(7)
            if (start + timedelta(days=i)).weekday() in working
            and (start + timedelta(days=i)) not in absence_index
        )
        off_days = sum(
            1 for i in range(7)
            if (start + timedelta(days=i)).weekday() in working
            and (start + timedelta(days=i)) in absence_index
        )
        columns.append({
            "start": start, "end": end,
            "label": start.strftime("%d/%m"),
            "capacity_days": capacity_days,
            "off_days": off_days,
            "is_current": start <= today <= end,
        })

    rows = []
    for p in projects:
        if p["status"] == "provisional" and not include_provisional:
            continue
        cells, total = [], 0.0
        for col in columns:
            days = 0.0
            for i in range(7):
                day = col["start"] + timedelta(days=i)
                if day in absence_index or not project_is_active_on(p, day, overrun_weeks):
                    continue
                days += project_daily_pct(p, day, settings) / 100.0
            days = round(days, 2)
            total += days
            cells.append({
                # Testé sur le DÉBUT de la semaine : avec la fin, une semaine
                # qui chevauchait la date de fin prévue passait déjà pour du
                # dépassement, et le rouge s'étalait d'une semaine de trop.
                "days": days,
                "overrun": days > 0 and project_is_overrunning(p, col["start"]),
            })
        if total > 0:
            rows.append({"project": p, "cells": cells, "total": round(total, 2)})

    # Regroupement par client : c'est l'axe de lecture naturel quand on
    # cherche à savoir qui occupe le planning.
    groups = {}
    for row in rows:
        name = (row["project"]["client"] or "").strip() or "Sans client"
        groups.setdefault(name, []).append(row)
    grouped = [{"client": name,
                "rows": sorted(items, key=lambda r: r["project"]["start_date"]),
                "totals": [round(sum(r["cells"][i]["days"] for r in items), 2)
                           for i in range(weeks)]}
               for name, items in sorted(groups.items())]

    totals = []
    for i, col in enumerate(columns):
        booked = round(sum(r["cells"][i]["days"] for r in rows), 2)
        capacity = col["capacity_days"]
        totals.append({
            "booked": booked,
            "capacity": capacity,
            "free": round(capacity - booked, 2),
            "pct": round(booked / capacity * 100, 1) if capacity else 0,
            "over": booked > capacity,
        })

    return {"columns": columns, "groups": grouped, "totals": totals, "rows": rows}


def late_projects(project_rows, settings, today=None):
    """Projets en difficulté, du plus grave au moins grave.

    Trois motifs distincts, souvent confondus alors qu'ils appellent des
    décisions différentes :
      - `overrun`  : la date de fin est passée, le projet tourne encore ;
      - `budget`   : les jours vendus sont consommés (ou près de l'être) ;
      - `pace`     : le budget part plus vite que le temps.
    """
    today = today or date.today()
    threshold = settings.get("budget_alert_pct", 80)
    late = []
    for row in project_rows:
        p, stats = row["project"], row["stats"]
        if p["status"] not in LIVE_STATUSES:
            continue
        reasons, severity = [], 0
        end = stats["planned_end_date"]
        if end < today:
            reasons.append(f"fin prévue dépassée de {(today - end).days} jour(s)")
            severity = max(severity, 3)
        if stats["pct_consumed"] >= 100:
            reasons.append(f"budget dépassé ({stats['pct_consumed']} %)")
            severity = max(severity, 3)
        elif stats["pct_consumed"] >= threshold:
            reasons.append(f"{stats['pct_consumed']} % du budget consommé")
            severity = max(severity, 1)
        if stats["pace_status"] == "behind":
            reasons.append("cadence en retard")
            severity = max(severity, 2)
        elif stats["pace_status"] == "tight":
            reasons.append("cadence tendue")
            severity = max(severity, 1)
        if reasons:
            late.append({
                "project": p, "stats": stats, "reasons": reasons,
                "severity": severity,
                "critical": severity >= 2,
                "days_remaining": stats["days_remaining"],
                "end": end,
            })
    late.sort(key=lambda item: (-item["severity"], item["stats"]["pct_consumed"] * -1))
    return late
