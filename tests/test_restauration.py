"""
Restauration d'une sauvegarde.

L'application savait produire une sauvegarde complète et cohérente, mais
aucune route ni écran ne permettait de la réinjecter : il fallait fermer
l'app, remplacer instance/timing.sqlite3 à la main, et penser aux fichiers
-wal et -shm. Une sauvegarde qu'on ne sait pas restaurer sans terminal, un
soir de panique, n'est une sauvegarde qu'à moitié.
"""
import io
import sqlite3
from datetime import date
from pathlib import Path

from conftest import project_data

import app as flask_app


def _client():
    flask_app.app.config["CSRF_PROTECT"] = False
    return flask_app.app.test_client()


# ------------------------------------------------------------- inspection

def test_un_fichier_qui_n_est_pas_sqlite_est_refuse(base, tmp_path):
    """sqlite3.connect() CRÉE le fichier au lieu d'échouer quand il n'est
    pas une base : « ça se connecte » ne prouve donc rien, et l'en-tête doit
    être vérifiée à la main."""
    faux = tmp_path / "photo.sqlite3"
    faux.write_bytes(b"\x89PNG\r\n\x1a\n pas une base du tout")

    resume, erreur = base.inspect_backup(faux)
    assert resume is None
    assert "SQLite" in erreur


def test_une_base_sqlite_etrangere_est_refusee(base, tmp_path):
    """Restaurer n'importe quelle base SQLite parce qu'elle porte la bonne
    extension laisserait une app vivante sur des tables vides, sans rien
    dire."""
    etrangere = tmp_path / "autre.sqlite3"
    conn = sqlite3.connect(etrangere)
    conn.execute("CREATE TABLE recettes (id INTEGER PRIMARY KEY, nom TEXT)")
    conn.commit()
    conn.close()

    resume, erreur = base.inspect_backup(etrangere)
    assert resume is None
    assert "Timing" in erreur


def test_un_fichier_absent_est_refuse(base, tmp_path):
    resume, erreur = base.inspect_backup(tmp_path / "jamais-existe.sqlite3")
    assert resume is None
    assert "introuvable" in erreur.lower()


def test_l_inspection_decrit_le_contenu_sans_rien_ecrire(base, tmp_path):
    """Montrer ce qu'on s'apprête à écraser, et par quoi : restaurer à
    l'aveugle est le meilleur moyen de perdre ce qu'on voulait sauver."""
    pid = base.create_project(project_data(start_date=date.today().isoformat()))
    base.create_entry(pid, date.today().isoformat(), 100, 7)
    sauvegarde = base.backup_to(tmp_path / "copie.sqlite3")

    resume, erreur = base.inspect_backup(sauvegarde)
    assert erreur is None
    assert resume["projects"] == 1
    assert resume["entries"] == 1
    assert resume["last_entry"] == date.today().isoformat()
    # L'inspection ne touche pas la base courante.
    assert len(base.list_projects()) == 1


# ----------------------------------------------------------- restauration

def test_une_sauvegarde_en_wal_est_lisible(base, tmp_path):
    """Une base produite par sqlite3.backup() conserve journal_mode=wal, et
    SQLite doit créer un fichier -shm à côté pour ouvrir une base WAL — ce
    que le mode lecture seule lui interdit. L'ouverture échouait alors avec
    « unable to open database file », et le comportement variait selon le
    système : sur macOS oui, ailleurs non.
    """
    base.create_project(project_data(start_date=date.today().isoformat()))
    sauvegarde = base.backup_to(tmp_path / "copie.sqlite3")

    conn = sqlite3.connect(sauvegarde)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"

    resume, erreur = base.inspect_backup(sauvegarde)
    assert erreur is None
    assert resume["projects"] == 1


def test_lire_une_sauvegarde_ne_laisse_aucun_journal_a_cote(base, tmp_path):
    """La lecture se fait sur une copie temporaire : la sauvegarde d'origine
    ne doit pas se retrouver flanquée de fichiers -wal et -shm, ni pouvoir
    être modifiée par l'inspection."""
    base.create_project(project_data(start_date=date.today().isoformat()))
    sauvegarde = base.backup_to(tmp_path / "copie.sqlite3")
    empreinte = sauvegarde.read_bytes()

    base.inspect_backup(sauvegarde)

    assert sorted(p.name for p in tmp_path.glob("copie.sqlite3*")) == ["copie.sqlite3"]
    assert sauvegarde.read_bytes() == empreinte


def test_restaurer_ramene_l_etat_sauvegarde(base, tmp_path):
    pid = base.create_project(project_data(name="Avant sauvegarde",
                                           start_date=date.today().isoformat()))
    base.create_entry(pid, date.today().isoformat(), 100, 7)
    sauvegarde = base.backup_to(tmp_path / "copie.sqlite3")

    base.create_project(project_data(name="Ajouté après",
                                     start_date=date.today().isoformat()))
    assert len(base.list_projects()) == 2

    resultat, erreur = base.restore_from(sauvegarde)

    assert erreur is None
    noms = [p["name"] for p in base.list_projects()]
    assert noms == ["Avant sauvegarde"]
    assert resultat["resume"]["entries"] == 1


def test_un_filet_est_pose_avant_d_ecraser(base, tmp_path):
    """Se tromper de fichier de restauration ne doit pas être irréversible :
    l'état d'avant part dans une sauvegarde horodatée à part."""
    base.create_project(project_data(name="État actuel",
                                     start_date=date.today().isoformat()))
    vide = base.backup_to(tmp_path / "vide.sqlite3")
    # On vide la sauvegarde de son projet pour simuler une restauration
    # ratée vers une base plus ancienne.
    conn = sqlite3.connect(vide)
    conn.execute("DELETE FROM projects")
    conn.commit()
    conn.close()

    resultat, erreur = base.restore_from(vide)
    assert erreur is None
    assert base.list_projects() == []

    filet = Path(resultat["safety_backup"])
    assert filet.exists()
    assert filet.name.startswith("avant-restauration")

    # Et ce filet permet vraiment de revenir. Deux restaurations enchaînées
    # tombaient sur le même nom de filet, à la seconde près : la seconde
    # écrasait sa propre source avant de la lire, et restaurait donc l'état
    # qu'elle était censée remplacer.
    base.restore_from(filet)
    assert [p["name"] for p in base.list_projects()] == ["État actuel"]


def test_la_cle_de_session_survit_a_la_restauration(base, tmp_path):
    """Restaurer une base ancienne aurait ramené son ancienne clé,
    invalidant la session au prochain démarrage : l'app aurait paru cassée
    juste après une opération déjà anxiogène."""
    base.set_setting("secret_key", "cle-de-la-base-sauvegardee")
    sauvegarde = base.backup_to(tmp_path / "copie.sqlite3")
    base.set_setting("secret_key", "cle-actuelle")

    base.restore_from(sauvegarde)
    assert base.get_setting_raw("secret_key") == "cle-actuelle"


def test_une_base_d_une_version_anterieure_est_migree(base, tmp_path):
    """Le schéma restauré peut dater : les migrations doivent le remettre à
    niveau avant que la moindre page ne l'interroge."""
    sauvegarde = base.backup_to(tmp_path / "ancienne.sqlite3")
    conn = sqlite3.connect(sauvegarde)
    conn.execute("ALTER TABLE projects DROP COLUMN remaining_days")
    conn.commit()
    conn.close()

    resultat, erreur = base.restore_from(sauvegarde)
    assert erreur is None
    colonnes = {r["name"] for r in
                base.get_db().execute("PRAGMA table_info(projects)")}
    assert "remaining_days" in colonnes


def test_restaurer_un_fichier_invalide_ne_touche_a_rien(base, tmp_path):
    base.create_project(project_data(name="Intact",
                                     start_date=date.today().isoformat()))
    faux = tmp_path / "faux.sqlite3"
    faux.write_bytes(b"n'importe quoi")

    resultat, erreur = base.restore_from(faux)

    assert resultat is None
    assert erreur is not None
    assert [p["name"] for p in base.list_projects()] == ["Intact"]


# ------------------------------------------------------------------ écran

def test_l_ecran_de_restauration_repond(base):
    html = _client().get("/reglages/restaurer").data.decode()
    assert "Restaurer une sauvegarde" in html
    assert flask_app.RESTORE_CONFIRMATION in html


def test_les_reglages_menent_a_la_restauration(base):
    assert "/reglages/restaurer" in _client().get("/reglages").data.decode()


def test_sans_le_mot_de_confirmation_rien_ne_se_passe(base, tmp_path):
    base.create_project(project_data(name="Intact",
                                     start_date=date.today().isoformat()))
    vide = base.backup_to(tmp_path / "vide.sqlite3")
    conn = sqlite3.connect(vide)
    conn.execute("DELETE FROM projects")
    conn.commit()
    conn.close()

    reponse = _client().post("/reglages/restaurer", data={
        "confirmation": "oui",
        "backup": (io.BytesIO(vide.read_bytes()), "vide.sqlite3"),
    }, content_type="multipart/form-data", follow_redirects=True)

    assert reponse.status_code == 200
    assert [p["name"] for p in base.list_projects()] == ["Intact"]


def test_avec_le_mot_de_confirmation_la_restauration_a_lieu(base, tmp_path):
    pid = base.create_project(project_data(name="Sauvegardé",
                                           start_date=date.today().isoformat()))
    base.create_entry(pid, date.today().isoformat(), 100, 7)
    sauvegarde = base.backup_to(tmp_path / "copie.sqlite3")
    base.create_project(project_data(name="Ajouté après",
                                     start_date=date.today().isoformat()))

    reponse = _client().post("/reglages/restaurer", data={
        "confirmation": flask_app.RESTORE_CONFIRMATION,
        "backup": (io.BytesIO(sauvegarde.read_bytes()), "copie.sqlite3"),
    }, content_type="multipart/form-data", follow_redirects=True)

    assert reponse.status_code == 200
    assert [p["name"] for p in base.list_projects()] == ["Sauvegardé"]


def test_sans_fichier_l_ecran_le_dit(base):
    reponse = _client().post("/reglages/restaurer", data={
        "confirmation": flask_app.RESTORE_CONFIRMATION,
    }, follow_redirects=True)

    assert reponse.status_code == 200
    assert "Choisis un fichier" in reponse.data.decode()
