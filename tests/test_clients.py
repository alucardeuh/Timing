"""
Fiches clients.

Avant, « client » n'était qu'un texte recopié sur chaque projet : une faute
de frappe créait un doublon silencieux, et il n'y avait nulle part où noter
un contact, un TJM habituel ou un délai de paiement.
"""
import sqlite3
from pathlib import Path

from conftest import project_data

import app as flask_app


def client_http():
    flask_app.app.config["CSRF_PROTECT"] = False
    return flask_app.app.test_client()


def test_nom_tape_librement_cree_la_fiche(base):
    """Taper un nom inconnu dans le formulaire projet doit créer la fiche,
    sans imposer un aller-retour par la page Clients."""
    client_http().post("/projects/new", data={
        "name": "Site vitrine", "client_new": "Gamma SARL", "status": "confirmed",
        "days_per_week": "2", "duration_value": "6", "duration_unit": "weeks",
        "day_rate": "500", "start_date": "2026-08-01"})

    records = base.list_client_records()
    assert [r["name"] for r in records] == ["Gamma SARL"]
    assert records[0]["projects_count"] == 1


def test_casse_differente_ne_cree_pas_de_doublon(base):
    """« Alpha SA » et « alpha sa » sont le même client."""
    base.ensure_client("Alpha SA")
    base.ensure_client("alpha sa")
    base.ensure_client("  ALPHA SA  ")

    assert len(base.list_client_records()) == 1


def test_tjm_habituel_repris(base):
    """C'est la raison d'être d'une fiche plutôt qu'un simple nom."""
    cid = base.create_client({"name": "Omega", "default_day_rate": 820})

    client_http().post("/projects/new", data={
        "name": "Mission Omega", "client_id": str(cid), "status": "confirmed",
        "days_per_week": "2", "duration_value": "5", "duration_unit": "weeks",
        "start_date": "2026-08-01"})

    project = [p for p in base.list_projects() if p["name"] == "Mission Omega"][0]
    assert project["day_rate"] == 820
    assert project["price_total"] == 8200


def test_tjm_saisi_prime_sur_celui_du_client(base):
    cid = base.create_client({"name": "Omega", "default_day_rate": 820})

    client_http().post("/projects/new", data={
        "name": "Mission", "client_id": str(cid), "status": "confirmed",
        "days_per_week": "1", "duration_value": "1", "duration_unit": "weeks",
        "day_rate": "400", "start_date": "2026-08-01"})

    assert [p for p in base.list_projects() if p["name"] == "Mission"][0]["day_rate"] == 400


def test_renommer_propage_sur_les_projets(base):
    """projects.client garde le nom en clair ; le renommer d'un seul côté
    ferait diverger la page Clients du reste de l'app."""
    cid = base.ensure_client("Gamma SARL")
    base.create_project(project_data(client="Gamma SARL", client_id=cid))

    base.update_client(cid, {"name": "Gamma Industries", "contact_name": None,
                             "email": None, "phone": None, "address": None,
                             "default_day_rate": None, "payment_terms_days": None,
                             "notes": None})

    assert base.list_projects()[0]["client"] == "Gamma Industries"


def test_supprimer_la_fiche_conserve_les_projets(base):
    """Supprimer un contact ne doit pas effacer l'historique de facturation
    qui s'y rattache."""
    cid = base.ensure_client("Gamma")
    base.create_project(project_data(client="Gamma", client_id=cid))

    base.delete_client(cid)

    project = base.list_projects()[0]
    assert project["client"] == "Gamma"      # nom conservé
    assert project["client_id"] is None      # lien rompu


def test_client_sans_projet_apparait(base):
    """Pouvoir saisir un prospect à l'avance est tout l'intérêt de la fiche."""
    base.create_client({"name": "Epsilon (prospect)"})

    records = base.list_client_records()
    assert records[0]["name"] == "Epsilon (prospect)"
    assert records[0]["projects_count"] == 0


def test_migration_cree_les_fiches_depuis_les_noms_existants(base, tmp_path, monkeypatch):
    """Ouvrir la page Clients après mise à jour ne doit pas donner une liste
    vide alors que les projets affichent toujours leurs clients."""
    path = tmp_path / "ancienne.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
          client TEXT, status TEXT NOT NULL DEFAULT 'provisional', days_per_week REAL NOT NULL,
          duration_value REAL NOT NULL, duration_unit TEXT NOT NULL DEFAULT 'weeks',
          day_rate REAL, price_total REAL NOT NULL, hours_per_day REAL, start_date TEXT NOT NULL,
          color TEXT NOT NULL DEFAULT '#3E8E82', notes TEXT, created_at TEXT NOT NULL);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    for name, client in [("Refonte", "Alpha SA"), ("Audit", "Alpha SA"),
                         ("BI", "Beta Group"), ("Interne", None)]:
        conn.execute(
            "INSERT INTO projects (name, client, status, days_per_week, duration_value, "
            "duration_unit, day_rate, price_total, hours_per_day, start_date, color, notes, "
            "created_at) VALUES (?,?,'confirmed',2,8,'weeks',500,8000,NULL,'2026-07-01',"
            "'#3E8E82','','2026-07-01T09:00:00')", (name, client))
    conn.commit()
    conn.close()

    monkeypatch.setattr(base, "DB_PATH", Path(path))
    base.init_db()

    noms = {r["name"]: r["projects_count"] for r in base.list_client_records()}
    assert noms == {"Alpha SA": 2, "Beta Group": 1}

    base.init_db()  # idempotent : pas de doublon au redémarrage suivant
    assert len(base.list_client_records()) == 2


def test_supprimer_une_fiche_client_survit_au_redemarrage(base):
    """Une fiche supprimée ne doit pas réapparaître au lancement suivant.

    _backfill_clients() recréait une fiche pour tout projet dont client_id
    était NULL — or delete_client() met justement client_id à NULL (ON
    DELETE SET NULL) tout en conservant le nom en clair sur le projet. Le
    backfill rejoué à chaque init_db() ressuscitait donc chaque suppression :
    la fiche disparaissait de la page Clients, puis revenait au redémarrage,
    sans que rien ne l'explique à l'écran.
    """
    client_id = base.ensure_client("Alpha SA")
    base.create_project(project_data(client="Alpha SA", client_id=client_id))
    base.delete_client(client_id)
    assert base.list_client_records() == []

    base.init_db()  # simule un relancement de l'app

    assert [r["name"] for r in base.list_client_records()] == []


def test_next_project_color_survit_a_une_suppression_definitive(base):
    """Compter COUNT(*) FROM projects redescendait après une suppression
    définitive : le projet suivant recevait le même index de couleur qu'un
    projet encore actif, sans rapport avec un passage par la corbeille.
    sqlite_sequence, lui, ne redescend jamais."""
    ids = []
    for i in range(len(base.PROJECT_COLORS)):
        color = base.next_project_color()
        pid = base.create_project(project_data(name=f"P{i}"))
        ids.append((pid, color))

    # Toutes les couleurs du cycle ont été utilisées une fois.
    assert {c for _, c in ids} == set(base.PROJECT_COLORS)

    # Suppression définitive du tout premier projet — l'ancien calcul
    # (COUNT(*)) aurait fait redescendre le compteur.
    first_id = ids[0][0]
    base.archive_project(first_id, True)
    base.delete_project_forever(first_id)

    next_color = base.next_project_color()
    assert next_color not in {c for _, c in ids[1:]}  # pas de collision avec un projet encore actif


def test_migration_nocase_ne_bloque_pas_sur_un_doublon_existant(base, tmp_path):
    """Une base antérieure à ce correctif peut déjà contenir deux fiches
    qui ne diffèrent que par la casse (créées hors des contrôles
    applicatifs). La migration doit s'écarter plutôt que d'empêcher l'app
    de démarrer."""
    import sqlite3
    path = tmp_path / "legacy.sqlite3"
    raw = sqlite3.connect(path)
    raw.executescript(
        "CREATE TABLE clients (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL UNIQUE, contact_name TEXT, email TEXT, phone TEXT, "
        "address TEXT, default_day_rate REAL, payment_terms_days INTEGER, "
        "notes TEXT, archived INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL, updated_at TEXT);"
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
    )
    raw.execute("INSERT INTO clients (name, created_at) VALUES ('Gamma', 'x')")
    raw.execute("INSERT INTO clients (name, created_at) VALUES ('gamma', 'x')")
    raw.commit()
    raw.close()

    old_db_path = base.DB_PATH
    base.DB_PATH = path
    try:
        base.init_db()  # ne doit pas lever
        assert {r["name"] for r in base.list_client_records()} == {"Gamma", "gamma"}
    finally:
        base.DB_PATH = old_db_path
