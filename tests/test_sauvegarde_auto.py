"""
Sauvegarde automatique.

La sauvegarde manuelle des Réglages ne protège que les gens qui y pensent.
Le cas réel est la fausse manipulation qu'on ne remarque que le lendemain.
"""
import sqlite3
from datetime import date

from conftest import project_data


def test_une_sauvegarde_est_prise_et_est_lisible(base, tmp_path):
    base.create_project(project_data(name="Projet sauvegardé",
                                     start_date=date.today().isoformat()))

    chemin = base.auto_backup(tmp_path)

    assert chemin.exists()
    copie = sqlite3.connect(chemin)
    noms = [r[0] for r in copie.execute("SELECT name FROM projects")]
    copie.close()
    assert noms == ["Projet sauvegardé"]


def test_une_seule_sauvegarde_par_jour(base, tmp_path):
    """Relancer l'app dix fois dans la journée ne doit pas écraser dix fois
    l'état du matin : c'est justement lui qu'on veut pouvoir retrouver."""
    base.create_project(project_data(name="Du matin",
                                     start_date=date.today().isoformat()))
    premier = base.auto_backup(tmp_path)

    base.create_project(project_data(name="De l'après-midi",
                                     start_date=date.today().isoformat()))
    second = base.auto_backup(tmp_path)

    assert premier == second
    copie = sqlite3.connect(second)
    noms = [r[0] for r in copie.execute("SELECT name FROM projects")]
    copie.close()
    assert noms == ["Du matin"]
    assert len(list(tmp_path.glob("timing-*.sqlite3"))) == 1


def test_la_rotation_garde_le_nombre_demande(base, tmp_path):
    for jour in range(1, 11):
        (tmp_path / f"timing-2026-01-{jour:02d}.sqlite3").write_bytes(b"")

    base.auto_backup(tmp_path, keep=7)

    restants = sorted(p.name for p in tmp_path.glob("timing-*.sqlite3"))
    assert len(restants) == 7
    # Ce sont bien les plus récents qui restent, celle du jour comprise.
    assert restants[-1] == f"timing-{date.today().isoformat()}.sqlite3"


def test_le_mode_wal_ne_donne_pas_une_copie_en_retard(base, tmp_path):
    """En WAL, copier le seul fichier principal donne une sauvegarde en
    retard : les écritures récentes vivent encore dans le journal. D'où
    sqlite3.backup() plutôt qu'un shutil.copy."""
    pid = base.create_project(project_data(name="Frais",
                                           start_date=date.today().isoformat()))
    base.create_entry(pid, date.today().isoformat(), 100, 7)

    chemin = base.backup_to(tmp_path / "copie.sqlite3")

    copie = sqlite3.connect(chemin)
    total = copie.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    copie.close()
    assert total == 1
