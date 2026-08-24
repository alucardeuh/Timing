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

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

try:
    from flask import g as _g
    from flask import has_app_context as _has_app_context
except ImportError:  # pragma: no cover — Flask est une dépendance du
    # projet ; ce repli existe uniquement pour que ce module reste
    # important depuis un script ou un test qui n'installerait pas Flask.
    _g = None

    def _has_app_context():
        return False

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
    # Thème d'affichage : auto (suit le système), light, dark. Stocké en
    # base plutôt qu'en localStorage : l'app est mono-utilisateur et locale,
    # donc le réglage doit suivre la base, pas le navigateur.
    "theme": "auto",
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
    ("projects", "client_id", "INTEGER REFERENCES clients(id) ON DELETE SET NULL"),
    ("projects", "remaining_days", "REAL"),
    ("projects", "remaining_updated_at", "TEXT"),
]

# Durée de conservation d'une ligne en corbeille. Au-delà, la purge du
# démarrage l'efface : une corbeille qui ne se vide jamais finit par peser
# plus lourd que les données vivantes.
TRASH_RETENTION_DAYS = 30

# Types restaurables. La clé est le `kind` stocké, la valeur la table cible.
# Une table absente de ce dictionnaire ne peut pas être restaurée : mieux
# vaut refuser que réinsérer une ligne dans une table dont on ne connaît
# pas les contraintes.
TRASH_TABLES = {
    "entry": "entries",
    "milestone": "milestones",
    "cost": "costs",
    "absence": "absences",
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_db():
    """Connexion SQLite.

    À l'intérieur d'une requête Flask, UNE SEULE connexion est ouverte et
    partagée pour toute la durée de la requête (posée sur `g`, fermée par
    `close_db_for_request` au teardown). Avant ce correctif, chaque fonction
    de ce module ouvrait sa propre connexion et la refermait aussitôt :
    afficher la fiche d'un projet en ouvrait quatorze, chacune refaisant le
    `mkdir` et les PRAGMA de départ.

    Hors d'une requête (tests, scripts, `python3 app.py` au tout premier
    appel), le comportement d'origine est conservé à l'identique : une
    connexion jetable par appel, à fermer via `release_db`.
    """
    if _has_app_context():
        if "db_conn" not in _g:
            _g.db_conn = _open_connection()
        return _g.db_conn
    return _open_connection()


def _open_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # timeout : plusieurs requêtes peuvent se croiser (page ouverte +
    # formulaire envoyé). Sans lui, SQLite lève "database is locked"
    # immédiatement au lieu d'attendre son tour.
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def release_db(conn):
    """Remplace chaque `conn.close()` de ce module.

    Ferme réellement la connexion, SAUF si c'est celle, partagée, de la
    requête Flask en cours — dans ce cas la fermer ici casserait le prochain
    appel de la même requête. Sa fermeture attend alors le teardown
    (`close_db_for_request`), posé une fois par `app.py` au démarrage.
    """
    if _has_app_context() and _g.get("db_conn") is conn:
        return
    conn.close()


def close_db_for_request(_exception=None):
    """Teardown Flask : ferme la connexion partagée en fin de requête.

    Enregistrée par `app.teardown_appcontext(db.close_db_for_request)`.
    `_exception` est le paramètre que Flask fournit toujours à un teardown,
    même quand la requête s'est bien passée — il n'est pas utilisé ici.
    """
    if _g is not None:
        conn = _g.pop("db_conn", None)
        if conn is not None:
            conn.close()  # ici, PAS release_db : c'est la fermeture réelle


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
    _ensure_clients_name_nocase(conn)
    _backfill_clients(conn)
    if indexes_sql.strip():
        conn.executescript(indexes_sql)

    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    release_db(conn)


def _apply_migrations(conn):
    for table, column, ddl in MIGRATIONS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table pas encore créée sur cette base
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


CLIENTS_COLLATION_FLAG = "clients_name_nocase_migrated"


def _ensure_clients_name_nocase(conn):
    """Réécrit clients.name en COLLATE NOCASE si la table existante ne l'a
    pas déjà.

    schema.sql décrit cette collation pour une base neuve, mais
    `CREATE TABLE IF NOT EXISTS` ne touche pas une table déjà là : sans
    cette migration, une base créée avant ce correctif gardait une
    contrainte UNIQUE sensible à la casse, alors que get_client_by_name()
    compare déjà en `COLLATE NOCASE`. L'écart ne mordait sur aucun chemin
    connu de l'app — ensure_client(), new_client() et edit_client()
    vérifient tous l'existence au préalable — mais restait une faille pour
    le premier appel direct à create_client() qui contournerait ce
    contrôle : « Alpha SA » et « alpha sa » auraient coexisté.

    SQLite n'a pas d'ALTER COLUMN : la seule façon de changer une collation
    est de reconstruire la table. `projects.client_id` y fait référence
    (ON DELETE SET NULL) — reconstruire en conservant les mêmes `id`
    (copiés explicitement, pas réattribués) garde ces références valides
    sans y toucher.
    """
    done = conn.execute("SELECT value FROM settings WHERE key = ?",
                        (CLIENTS_COLLATION_FLAG,)).fetchone()
    if done and done["value"] == "1":
        return
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='clients'"
    ).fetchone():
        return  # table pas encore créée : schema.sql lui donne directement la bonne collation

    conn.execute("DROP TABLE IF EXISTS clients_ncase")
    conn.execute("""
        CREATE TABLE clients_ncase (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT NOT NULL UNIQUE COLLATE NOCASE,
            contact_name        TEXT,
            email               TEXT,
            phone               TEXT,
            address             TEXT,
            default_day_rate    REAL,
            payment_terms_days  INTEGER,
            notes               TEXT,
            archived            INTEGER NOT NULL DEFAULT 0,
            created_at          TEXT NOT NULL,
            updated_at          TEXT
        )
    """)
    try:
        conn.execute("INSERT INTO clients_ncase SELECT * FROM clients")
    except sqlite3.IntegrityError:
        # Deux fiches existantes ne diffèrent que par la casse : forcément
        # créées par un chemin antérieur à ce correctif, hors des contrôles
        # applicatifs. On abandonne plutôt que de bloquer le démarrage de
        # l'app pour cette base précise ; à fusionner à la main, puis la
        # migration passera au prochain lancement.
        conn.execute("DROP TABLE clients_ncase")
        return
    conn.execute("DROP TABLE clients")
    conn.execute("ALTER TABLE clients_ncase RENAME TO clients")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_clients_name ON clients(name)")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')",
                 (CLIENTS_COLLATION_FLAG,))


# Marqueur de migration de données. Une migration de SCHÉMA peut se rejouer
# sans dommage (la colonne existe ou non) ; une migration de DONNÉES, non :
# rejouée, elle recrée ce que l'utilisateur a supprimé entre-temps.
BACKFILL_FLAG = "clients_backfilled"


def _backfill_clients(conn):
    """Crée une fiche pour chaque nom de client déjà saisi sur un projet.

    Migration de données, pas de schéma : les noms vivaient uniquement en
    texte libre sur les projets. Sans ce rattrapage, ouvrir la page Clients
    après mise à jour donnerait une liste vide alors que les projets, eux,
    affichent toujours leurs clients.

    NE TOURNE QU'UNE FOIS, et c'est le cœur du correctif. delete_client()
    remet projects.client_id à NULL (ON DELETE SET NULL) tout en conservant
    le nom en clair — exactement la signature que ce backfill cherchait.
    Rejoué au démarrage suivant, il ressuscitait donc chaque fiche client
    supprimée : la suppression tenait jusqu'au prochain lancement, puis
    s'annulait toute seule.
    """
    done = conn.execute("SELECT value FROM settings WHERE key = ?",
                        (BACKFILL_FLAG,)).fetchone()
    if done and done["value"] == "1":
        return

    columns = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
    if "client_id" not in columns:
        return  # base d'avant la migration : on réessaiera au prochain démarrage
    orphans = conn.execute(
        "SELECT DISTINCT TRIM(client) AS name FROM projects "
        "WHERE client_id IS NULL AND client IS NOT NULL AND TRIM(client) != ''"
    ).fetchall()
    for row in orphans:
        name = row["name"]
        conn.execute(
            "INSERT OR IGNORE INTO clients (name, created_at, updated_at) VALUES (?,?,?)",
            (name, now_iso(), now_iso()),
        )
        client = conn.execute("SELECT id FROM clients WHERE name = ?", (name,)).fetchone()
        conn.execute("UPDATE projects SET client_id = ? WHERE client_id IS NULL AND TRIM(client) = ?",
                     (client["id"], name))

    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')",
                 (BACKFILL_FLAG,))


# --------------------------------------------------------------- réglages

# Clés techniques qui vivent dans la même table `settings` que les réglages
# affichables, mais qui n'ont rien à faire dans un template : la clé de
# session en fait partie. get_settings() alimente `inject_globals()` et
# plusieurs routes rendent `settings=settings` tel quel — la clé de session
# s'y retrouvait donc dans le contexte Jinja de chaque page, prête à fuir
# au premier `{{ settings }}` ou `{% for k, v in settings.items() %}` ajouté
# par erreur. Aucun template actuel ne le fait, mais c'était une fuite qui
# n'attendait qu'une occasion.
INTERNAL_SETTING_KEYS = {"secret_key", BACKFILL_FLAG, CLIENTS_COLLATION_FLAG}


def get_settings():
    """Réglages publics, prêts à passer à un template.

    Les clés internes (voir INTERNAL_SETTING_KEYS) sont filtrées ici, pas à
    la source : la table `settings` reste le stockage clé/valeur générique
    qu'elle a toujours été, seule cette fonction — le point d'entrée de
    tout le reste de l'app — décide de ce qui est montrable.
    """
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    release_db(conn)
    settings = dict(DEFAULT_SETTINGS)
    settings.update({r["key"]: r["value"] for r in rows
                     if r["key"] not in INTERNAL_SETTING_KEYS})
    for key in FLOAT_SETTINGS:
        try:
            settings[key] = float(settings[key])
        except (TypeError, ValueError):
            settings[key] = float(DEFAULT_SETTINGS[key])
    return settings


def get_setting_raw(key, default=None):
    """Lit une valeur brute de la table `settings`, y compris une clé
    interne que get_settings() masque.

    Ce n'est PAS l'API publique des réglages : c'est la sortie de secours
    pour les rares appels — la clé de session, au démarrage — qui ont
    explicitement besoin d'une clé technique et savent ce qu'ils font.
    """
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    release_db(conn)
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()
    release_db(conn)


def next_project_color():
    """Couleur suivante dans le cycle des huit couleurs de PROJECT_COLORS.

    Basé sur `sqlite_sequence`, pas sur COUNT(*) FROM projects : le compteur
    AUTOINCREMENT de SQLite ne redescend jamais, même après une suppression
    définitive, alors que COUNT(*) si. Avec COUNT(*), supprimer
    définitivement un projet parmi huit puis en créer un nouveau donnait le
    même index qu'un projet encore actif — deux projets vivants avec la même
    couleur, sans lien avec un passage par la corbeille.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = 'projects'"
    ).fetchone()
    release_db(conn)
    n = row["seq"] if row else 0
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
    release_db(conn)
    return rows


def counts_by_status():
    conn = get_db()
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM projects WHERE archived = 0 GROUP BY status"
    ).fetchall()
    trash = conn.execute("SELECT COUNT(*) AS n FROM projects WHERE archived = 1").fetchone()["n"]
    release_db(conn)
    counts = {s: 0 for s in PROJECT_STATUSES}
    counts.update({r["status"]: r["n"] for r in rows})
    counts["all"] = sum(counts[s] for s in PROJECT_STATUSES)
    counts["trash"] = trash
    return counts


def get_project(project_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    release_db(conn)
    return row


def list_clients():
    """Noms de clients présents sur des projets, avec leur nombre.

    Alimente le filtre de la liste de projets. Distinct de list_client_records
    qui, lui, renvoie les fiches — y compris celles sans aucun projet.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT COALESCE(client, '') AS client, COUNT(*) AS n "
        "FROM projects WHERE archived = 0 GROUP BY COALESCE(client, '') ORDER BY client"
    ).fetchall()
    release_db(conn)
    return [dict(r) for r in rows]


# ---------------------------------------------------------- fiches clients

def list_client_records(include_archived=False):
    """Fiches clients avec le nombre de projets rattachés.

    Un client fraîchement créé, sans projet, doit apparaître : c'est tout
    l'intérêt de pouvoir le saisir à l'avance.
    """
    conn = get_db()
    sql = ("SELECT c.*, "
           "(SELECT COUNT(*) FROM projects p WHERE p.client_id = c.id AND p.archived = 0) AS projects_count "
           "FROM clients c")
    if not include_archived:
        sql += " WHERE c.archived = 0"
    sql += " ORDER BY c.name COLLATE NOCASE"
    rows = conn.execute(sql).fetchall()
    release_db(conn)
    return rows


def get_client(client_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    release_db(conn)
    return row


def get_client_by_name(name):
    conn = get_db()
    row = conn.execute("SELECT * FROM clients WHERE name = ? COLLATE NOCASE",
                       ((name or "").strip(),)).fetchone()
    release_db(conn)
    return row


def create_client(data):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO clients (name, contact_name, email, phone, address, "
        "default_day_rate, payment_terms_days, notes, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (data["name"], data.get("contact_name"), data.get("email"), data.get("phone"),
         data.get("address"), data.get("default_day_rate"), data.get("payment_terms_days"),
         data.get("notes"), now_iso(), now_iso()),
    )
    client_id = cur.lastrowid
    conn.commit()
    release_db(conn)
    return client_id


def ensure_client(name):
    """Renvoie l'id du client portant ce nom, en le créant au besoin.

    Le rapprochement est insensible à la casse : « Alpha SA » et « alpha sa »
    ne doivent pas produire deux fiches.
    """
    name = (name or "").strip()
    if not name:
        return None
    existing = get_client_by_name(name)
    if existing:
        return existing["id"]
    return create_client({"name": name})


def update_client(client_id, data):
    """Met à jour une fiche et propage le nom sur tous ses projets.

    projects.client garde le nom en clair ; le renommer d'un côté sans
    l'autre ferait diverger la page Clients et le reste de l'app.
    """
    conn = get_db()
    conn.execute(
        "UPDATE clients SET name=?, contact_name=?, email=?, phone=?, address=?, "
        "default_day_rate=?, payment_terms_days=?, notes=?, updated_at=? WHERE id=?",
        (data["name"], data.get("contact_name"), data.get("email"), data.get("phone"),
         data.get("address"), data.get("default_day_rate"), data.get("payment_terms_days"),
         data.get("notes"), now_iso(), client_id),
    )
    conn.execute("UPDATE projects SET client = ? WHERE client_id = ?", (data["name"], client_id))
    conn.commit()
    release_db(conn)


def set_client_archived(client_id, archived=True):
    conn = get_db()
    conn.execute("UPDATE clients SET archived = ?, updated_at = ? WHERE id = ?",
                 (1 if archived else 0, now_iso(), client_id))
    conn.commit()
    release_db(conn)


def delete_client(client_id):
    """Supprime la fiche. Les projets sont conservés et gardent le nom du
    client en clair : supprimer un contact ne doit pas effacer l'historique
    de facturation qui s'y rattache."""
    conn = get_db()
    conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    conn.commit()
    release_db(conn)


def client_projects(client_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM projects WHERE client_id = ? AND archived = 0 "
        "ORDER BY start_date DESC", (client_id,)
    ).fetchall()
    release_db(conn)
    return rows


def create_project(data):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO projects (name, client, client_id, status, days_per_week, duration_value, "
        "duration_unit, day_rate, price_total, hours_per_day, start_date, weekdays, "
        "color, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            data["name"], data["client"], data.get("client_id"),
            data["status"], data["days_per_week"],
            data["duration_value"], data["duration_unit"], data["day_rate"],
            data["price_total"], data["hours_per_day"], data["start_date"],
            data.get("weekdays") or None,
            data.get("color") or PROJECT_COLORS[0], data["notes"], now_iso(), now_iso(),
        ),
    )
    project_id = cur.lastrowid
    conn.commit()
    release_db(conn)
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
        "UPDATE projects SET name=?, client=?, client_id=?, status=?, days_per_week=?, "
        "duration_value=?, duration_unit=?, day_rate=?, price_total=?, hours_per_day=?, "
        "start_date=?, weekdays=?, notes=?, updated_at=? WHERE id=?",
        (
            data["name"], data["client"], data.get("client_id"),
            data["status"], data["days_per_week"],
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
    release_db(conn)
    return changed


def set_project_status(project_id, status):
    if status not in PROJECT_STATUSES:
        raise ValueError(f"Statut inconnu : {status!r}")
    conn = get_db()
    conn.execute("UPDATE projects SET status=?, updated_at=? WHERE id=?",
                 (status, now_iso(), project_id))
    conn.commit()
    release_db(conn)


def archive_project(project_id, archived=True):
    conn = get_db()
    conn.execute("UPDATE projects SET archived=?, updated_at=? WHERE id=?",
                 (1 if archived else 0, now_iso(), project_id))
    conn.commit()
    release_db(conn)


def delete_project_forever(project_id):
    conn = get_db()
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    release_db(conn)


def list_scope_changes(project_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM scope_changes WHERE project_id = ? ORDER BY changed_at DESC", (project_id,)
    ).fetchall()
    release_db(conn)
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
    release_db(conn)
    return rows


def count_entries(project_id, date_from=None, date_to=None, task_id=None):
    where, params = _entry_filters(project_id, date_from, date_to, task_id)
    conn = get_db()
    n = conn.execute(f"SELECT COUNT(*) AS n FROM entries WHERE {where}", params).fetchone()["n"]
    release_db(conn)
    return n


def get_entry(entry_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    release_db(conn)
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
    release_db(conn)
    return entry_id


def update_entry(entry_id, entry_date, percent, hours, task_id=None, note=""):
    conn = get_db()
    conn.execute(
        "UPDATE entries SET entry_date=?, percent_of_day=?, hours=?, task_id=?, note=?, updated_at=? "
        "WHERE id=?",
        (entry_date, percent, hours, task_id, note, now_iso(), entry_id),
    )
    conn.commit()
    release_db(conn)


def delete_entry(entry_id):
    """Supprime une saisie, après copie en corbeille.

    Une saisie effacée par erreur (mauvaise ligne dans la grille hebdo) était
    irrécupérable : ni confirmation, ni retour en arrière.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT e.*, p.name AS _project_name FROM entries e "
        "LEFT JOIN projects p ON p.id = e.project_id WHERE e.id = ?", (entry_id,)
    ).fetchone()
    trash_id = None
    if row is not None:
        label = f"Saisie du {row['entry_date']} — {row['_project_name'] or 'projet supprimé'}"
        payload = {k: row[k] for k in row.keys() if not k.startswith("_")}
        trash_id = conn.execute(
            "INSERT INTO trash (kind, label, payload, deleted_at) VALUES (?,?,?,?)",
            ("entry", label, json.dumps(payload, ensure_ascii=False), now_iso()),
        ).lastrowid
    conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    conn.commit()
    release_db(conn)
    return trash_id


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
    release_db(conn)
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
    release_db(conn)
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
    release_db(conn)
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
    release_db(conn)
    return {r["entry_date"]: r["pct"] or 0.0 for r in rows}


def today_summary(day_iso):
    conn = get_db()
    rows = conn.execute(
        "SELECT p.name AS project_name, SUM(e.percent_of_day) AS total_pct "
        "FROM entries e JOIN projects p ON p.id = e.project_id "
        "WHERE e.entry_date = ? GROUP BY e.project_id ORDER BY total_pct DESC",
        (day_iso,),
    ).fetchall()
    release_db(conn)
    return rows


def monthly_activity(months=12):
    """[{'month': '2026-03', 'days': 12.5}] — consommation par mois, tous
    projets confondus. Alimente le graphique du Comparatif."""
    conn = get_db()
    rows = conn.execute(
        "SELECT substr(entry_date, 1, 7) AS month, SUM(percent_of_day) / 100.0 AS days "
        "FROM entries GROUP BY month ORDER BY month DESC LIMIT ?", (months,)
    ).fetchall()
    release_db(conn)
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
    release_db(conn)
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
        release_db(conn)


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
    release_db(conn)
    return rows


def list_all_active_tasks():
    """Toutes les tâches non archivées, indexées par projet — la grille
    hebdo en a besoin pour tous les projets d'un coup."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM tasks WHERE archived = 0 ORDER BY name").fetchall()
    release_db(conn)
    by_project = {}
    for r in rows:
        by_project.setdefault(r["project_id"], []).append(dict(r))
    return by_project


def task_names():
    conn = get_db()
    rows = conn.execute("SELECT id, name FROM tasks").fetchall()
    release_db(conn)
    return {r["id"]: r["name"] for r in rows}


def create_task(project_id, name):
    conn = get_db()
    conn.execute("INSERT INTO tasks (project_id, name, archived, created_at) VALUES (?,?,0,?)",
                 (project_id, name, now_iso()))
    conn.commit()
    release_db(conn)


def rename_task(task_id, name):
    conn = get_db()
    conn.execute("UPDATE tasks SET name = ? WHERE id = ?", (name, task_id))
    conn.commit()
    release_db(conn)


def set_task_archived(task_id, archived=True):
    conn = get_db()
    conn.execute("UPDATE tasks SET archived = ? WHERE id = ?", (1 if archived else 0, task_id))
    conn.commit()
    release_db(conn)


def delete_task(task_id):
    """Supprime la tâche ; les entrées rattachées sont conservées et
    repassent en 'Sans tâche' (ON DELETE SET NULL). Du temps saisi ne doit
    jamais disparaître avec une étiquette."""
    conn = get_db()
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    release_db(conn)


def get_task_project(task_id):
    conn = get_db()
    row = conn.execute("SELECT project_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    release_db(conn)
    return row["project_id"] if row else None


def task_breakdown(project_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT e.task_id, COALESCE(t.name, 'Sans tâche') AS name, "
        "SUM(e.percent_of_day) / 100.0 AS days "
        "FROM entries e LEFT JOIN tasks t ON t.id = e.task_id "
        "WHERE e.project_id = ? GROUP BY e.task_id ORDER BY days DESC", (project_id,)
    ).fetchall()
    release_db(conn)
    return [dict(r) for r in rows]


def task_breakdown_global():
    conn = get_db()
    rows = conn.execute(
        "SELECT COALESCE(t.name, 'Sans tâche') AS name, SUM(e.percent_of_day) / 100.0 AS days "
        "FROM entries e LEFT JOIN tasks t ON t.id = e.task_id "
        "GROUP BY COALESCE(t.name, 'Sans tâche') ORDER BY days DESC"
    ).fetchall()
    release_db(conn)
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
    release_db(conn)
    return rows


def get_absence(absence_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM absences WHERE id = ?", (absence_id,)).fetchone()
    release_db(conn)
    return row


def create_absence(label, kind, start_date, end_date):
    if kind not in ABSENCE_KINDS:
        raise ValueError(f"Type d'absence inconnu : {kind!r}")
    conn = get_db()
    conn.execute(
        "INSERT INTO absences (label, kind, start_date, end_date, created_at) VALUES (?,?,?,?,?)",
        (label, kind, start_date, end_date, now_iso()),
    )
    conn.commit()
    release_db(conn)


def delete_absence(absence_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM absences WHERE id = ?", (absence_id,)).fetchone()
    trash_id = trash_put(conn, "absence", f"Absence « {row['label']} »", row) if row else None
    conn.execute("DELETE FROM absences WHERE id = ?", (absence_id,))
    conn.commit()
    release_db(conn)
    return trash_id


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
    release_db(conn)
    return rows


def create_milestone(project_id, label, amount, due_date):
    conn = get_db()
    conn.execute(
        "INSERT INTO milestones (project_id, label, amount, due_date, status, created_at) "
        "VALUES (?,?,?,?,'todo',?)",
        (project_id, label, amount, due_date or None, now_iso()),
    )
    conn.commit()
    release_db(conn)


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
    release_db(conn)


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
    release_db(conn)


def delete_milestone(milestone_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
    trash_id = trash_put(conn, "milestone", f"Jalon « {row['label']} »", row) if row else None
    conn.execute("DELETE FROM milestones WHERE id = ?", (milestone_id,))
    conn.commit()
    release_db(conn)
    return trash_id


def get_milestone(milestone_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
    release_db(conn)
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
    release_db(conn)
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
    release_db(conn)
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
    release_db(conn)
    return r["amount"] or 0.0


# --------------------------------------------------------------- coûts

def list_costs(project_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM costs WHERE project_id = ? ORDER BY COALESCE(cost_date, created_at) DESC",
        (project_id,),
    ).fetchall()
    release_db(conn)
    return rows


def create_cost(project_id, label, amount, cost_date, billable=False):
    conn = get_db()
    conn.execute(
        "INSERT INTO costs (project_id, label, amount, cost_date, billable, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (project_id, label, amount, cost_date or None, 1 if billable else 0, now_iso()),
    )
    conn.commit()
    release_db(conn)


def delete_cost(cost_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM costs WHERE id = ?", (cost_id,)).fetchone()
    trash_id = trash_put(conn, "cost", f"Coût « {row['label']} »", row) if row else None
    conn.execute("DELETE FROM costs WHERE id = ?", (cost_id,))
    conn.commit()
    release_db(conn)
    return trash_id


def get_cost_project(cost_id):
    conn = get_db()
    row = conn.execute("SELECT project_id FROM costs WHERE id = ?", (cost_id,)).fetchone()
    release_db(conn)
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
    release_db(conn)
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
    release_db(conn)
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
    release_db(conn)
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
    release_db(conn)
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
        release_db(source)
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
        "client_id": source["client_id"],
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
    release_db(conn)
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
    release_db(conn)
    by_client = {}
    for r in rows:
        if r["delay"] is None or r["delay"] < 0:
            continue
        by_client.setdefault(r["client"] or "Sans client", []).append(r["delay"])
    return {name: {"days": round(sum(v) / len(v), 1), "samples": len(v)}
            for name, v in by_client.items()}


def contractual_delays():
    """Délai de paiement contractuel par client, depuis les fiches.

    Sert de repli au prévisionnel quand aucune facture n'a encore été
    encaissée : mieux vaut la valeur négociée que 30 jours arbitraires.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT name, payment_terms_days FROM clients WHERE payment_terms_days IS NOT NULL"
    ).fetchall()
    release_db(conn)
    return {r["name"]: r["payment_terms_days"] for r in rows}


def open_milestones():
    """Jalons non encaissés, avec le client — base de la projection."""
    conn = get_db()
    rows = conn.execute(
        "SELECT m.*, p.name AS project_name, COALESCE(p.client, '') AS project_client "
        "FROM milestones m JOIN projects p ON p.id = m.project_id "
        "WHERE p.archived = 0 AND m.status != 'paid' "
        "ORDER BY COALESCE(m.due_date, '9999')"
    ).fetchall()
    release_db(conn)
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
    release_db(conn)
    return [dict(r) for r in rows]


# ------------------------------------------------------------- reste à faire

def set_project_remaining(project_id, days):
    """Enregistre le reste à faire déclaré d'un projet (None = efface).

    Stocké à part du budget vendu : c'est une estimation qui bouge, pas une
    donnée contractuelle. La date de mise à jour l'accompagne, faute de quoi
    une estimation vieille de deux mois pèserait autant qu'une estimation du
    jour dans les alertes.
    """
    conn = get_db()
    conn.execute(
        "UPDATE projects SET remaining_days = ?, remaining_updated_at = ?, updated_at = ? "
        "WHERE id = ?",
        (days, now_iso() if days is not None else None, now_iso(), project_id),
    )
    conn.commit()
    release_db(conn)


# ------------------------------------------------------------- corbeille

def _row_to_payload(row):
    return {key: row[key] for key in row.keys()}


def trash_put(conn, kind, label, row):
    """Recopie une ligne en corbeille. Appelé AVANT le DELETE, sur la même
    connexion, pour qu'une suppression et son archivage soient atomiques :
    archiver dans une transaction séparée laissait la porte ouverte à une
    ligne effacée dont la copie n'existait pas.
    """
    if kind not in TRASH_TABLES:
        raise ValueError(f"Type non restaurable : {kind!r}")
    cur = conn.execute(
        "INSERT INTO trash (kind, label, payload, deleted_at) VALUES (?,?,?,?)",
        (kind, label, json.dumps(_row_to_payload(row), ensure_ascii=False), now_iso()),
    )
    return cur.lastrowid


def list_trash(limit=100):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM trash ORDER BY deleted_at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    release_db(conn)
    return [{"id": r["id"], "kind": r["kind"], "label": r["label"],
             "deleted_at": r["deleted_at"],
             "payload": json.loads(r["payload"])} for r in rows]


def get_trash(trash_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM trash WHERE id = ?", (trash_id,)).fetchone()
    release_db(conn)
    return row


def restore_trash(trash_id):
    """Réinsère une ligne supprimée, avec son id d'origine si celui-ci est
    resté libre.

    Réinsérer sans id aurait cassé tout ce qui pointait vers l'ancien
    (rien aujourd'hui, mais la règle vaut d'être tenue), et le forcer alors
    qu'il est repris depuis aurait levé une contrainte d'unicité en pleine
    restauration. Le projet parent peut aussi avoir disparu entre-temps :
    la restauration échoue alors proprement au lieu de créer un orphelin.
    """
    conn = get_db()
    row = conn.execute("SELECT * FROM trash WHERE id = ?", (trash_id,)).fetchone()
    if row is None:
        release_db(conn)
        return None, "Cet élément n'est plus dans la corbeille."

    kind = row["kind"]
    table = TRASH_TABLES.get(kind)
    if table is None:
        release_db(conn)
        return None, "Cet élément n'est pas restaurable."

    payload = json.loads(row["payload"])
    columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    data = {k: v for k, v in payload.items() if k in columns}

    parent = data.get("project_id")
    if parent is not None:
        exists = conn.execute("SELECT 1 FROM projects WHERE id = ?", (parent,)).fetchone()
        if exists is None:
            release_db(conn)
            return None, "Le projet d'origine n'existe plus."

    taken = conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (data.get("id"),)).fetchone()
    if taken is not None:
        data.pop("id", None)

    cols = ", ".join(data.keys())
    marks = ", ".join("?" for _ in data)
    cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(data.values()))
    new_id = cur.lastrowid
    conn.execute("DELETE FROM trash WHERE id = ?", (trash_id,))
    conn.commit()
    release_db(conn)
    return new_id, None


def empty_trash():
    conn = get_db()
    conn.execute("DELETE FROM trash")
    conn.commit()
    release_db(conn)


def purge_trash(retention_days=TRASH_RETENTION_DAYS):
    """Efface les lignes de corbeille plus anciennes que la rétention.

    Comparaison inclusive : les horodatages sont à la seconde près, donc
    avec une borne stricte une ligne supprimée dans la même seconde que la
    purge y échappait — invisible avec trente jours de rétention, net avec
    zéro.
    """
    cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat(timespec="seconds")
    conn = get_db()
    cur = conn.execute("DELETE FROM trash WHERE deleted_at <= ?", (cutoff,))
    conn.commit()
    release_db(conn)
    return cur.rowcount


# ------------------------------------------------------------- sauvegarde

def backup_to(path):
    """Copie cohérente de la base vers `path`, via sqlite3.backup().

    En mode WAL, copier le seul fichier principal donne une sauvegarde en
    retard sur la base réelle : les écritures récentes vivent encore dans le
    journal.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    target = sqlite3.connect(str(path))
    try:
        conn.backup(target)
    finally:
        target.close()
        release_db(conn)
    return path


def auto_backup(directory=None, keep=7):
    """Sauvegarde datée au démarrage, avec rotation.

    Une sauvegarde manuelle depuis les Réglages ne protège que les gens qui
    y pensent. Une copie par jour d'utilisation, conservée `keep` jours,
    couvre le cas réel : la fausse manipulation qu'on ne remarque que le
    lendemain. Une seule par jour, donc relancer l'app dix fois n'écrase pas
    dix fois l'état du matin.
    """
    directory = Path(directory or DB_PATH.parent / "backups")
    stamp = datetime.now().date().isoformat()
    target = directory / f"timing-{stamp}.sqlite3"
    if not target.exists():
        backup_to(target)
    existing = sorted(directory.glob("timing-*.sqlite3"))
    for old in existing[:-keep] if keep > 0 else []:
        try:
            old.unlink()
        except OSError:
            pass
    return target
